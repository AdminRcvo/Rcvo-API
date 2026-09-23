from __future__ import annotations

import importlib.util
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RAW_ROOT=ROOT/"prospection-data"
REF_ROOT=ROOT/"reference-data"
AGENT_ROOT=ROOT/"sourcing-agent"

for path in (RAW_ROOT/"src",REF_ROOT/"src",AGENT_ROOT/"src"):
    sys.path.insert(0,str(path))

from ingest_runtime import connect_fast, ingest_file
from rcvo_data import init_databases
from reference_engine import connect as ref_connect, init_db as init_ref
from http_gateways import HttpRawGateway,HttpReferenceGateway
from agent import AgentConfig,SourcingAgent
from state import AgentState

def load_script(name:str,path:Path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module

RAW_API=load_script("rcvo_raw_sourcing_api",RAW_ROOT/"scripts"/"sourcing_api.py")
REF_API=load_script("rcvo_reference_sourcing_api",REF_ROOT/"scripts"/"sourcing_api.py")

class ApiPair:
    def __init__(self,root:Path,with_reference:bool=True):
        self.root=root
        self.raw_data=root/"raw-data"
        self.ref_db=root/"reference-data"/"rcvo-reference.sqlite"
        self.state_db=root/"agent-state"/"sourcing-agent.sqlite"
        self.raw_token="raw-e2e-token"
        self.ref_token="ref-e2e-token"
        os.environ["E2E_RAW_TOKEN"]=self.raw_token
        os.environ["E2E_REF_TOKEN"]=self.ref_token

        raw_path,_=init_databases(self.raw_data)
        self.raw_path=raw_path
        self.raw_server=RAW_API.Server(("127.0.0.1",0),RAW_API.Handler)
        self.raw_server.data_dir=self.raw_data
        self.raw_server.raw_path=raw_path
        self.raw_server.token_env="E2E_RAW_TOKEN"
        self.raw_server.max_attempts=5
        self.raw_thread=threading.Thread(target=self.raw_server.serve_forever,daemon=True)
        self.raw_thread.start()
        self.raw_url=f"http://127.0.0.1:{self.raw_server.server_port}"

        self.ref_server=None
        self.ref_thread=None
        self.ref_url=None
        if with_reference:
            self.start_reference()

    def start_reference(self):
        if self.ref_server is not None:
            return
        init_ref(self.ref_db)
        self.ref_server=REF_API.Server(("127.0.0.1",0),REF_API.Handler)
        self.ref_server.db=self.ref_db
        self.ref_server.token_env="E2E_REF_TOKEN"
        self.ref_thread=threading.Thread(target=self.ref_server.serve_forever,daemon=True)
        self.ref_thread.start()
        self.ref_url=f"http://127.0.0.1:{self.ref_server.server_port}"

    def stop(self):
        if self.raw_server:
            self.raw_server.shutdown(); self.raw_server.server_close()
        if self.ref_server:
            self.ref_server.shutdown(); self.ref_server.server_close()
        os.environ.pop("E2E_RAW_TOKEN",None)
        os.environ.pop("E2E_REF_TOKEN",None)

    def agent(self,mode="production",ref_url=None):
        return SourcingAgent(
            HttpRawGateway(self.raw_url,self.raw_token,3),
            HttpReferenceGateway(ref_url or self.ref_url,self.ref_token,3),
            AgentState(self.state_db),
            AgentConfig(
                mode=mode,claim_size=100,lease_seconds=60,
                idle_sleep_seconds=0.01,worker_id=f"e2e-{mode}",
            ),
        )

def write_jsonl(path:Path,rows:list[str]):
    path.write_text("\n".join(rows)+"\n",encoding="utf-8")

class PipelineCertificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()

    def test_real_pipeline_merges_and_preserves_provenance(self):
        env=ApiPair(self.root)
        try:
            apollo=self.root/"apollo.jsonl"
            hunter=self.root/"hunter.jsonl"
            write_jsonl(apollo,[
                '{"first_name":"jEAN","last_name":"dupont","email":"JEAN.DUPONT@GARAGE-X.FR","company":"Garage X SAS","website":"https://www.garage-x.fr/","city":"BORDEAUX","job_title":"Vendeur VO","sector":"automobile occasion"}'
            ])
            write_jsonl(hunter,[
                '{"first_name":"Jean","last_name":"DUPONT","email":"jean.dupont@garage-x.fr","company":"Garage X","company_domain":"garage-x.fr","company_city":"Bordeaux","job_title":"Responsable VO","industry":"used vehicles"}'
            ])
            ingest_file(env.raw_data,apollo,source_key="apollo")
            ingest_file(env.raw_data,hunter,source_key="hunter")

            result=env.agent().process_batch()
            self.assertEqual(result["claimed"],2)
            self.assertEqual(result["promoted"],2)
            self.assertEqual(result["failed"],0)
            self.assertEqual(result["rejected"],0)

            with connect_fast(env.raw_path) as conn:
                states=dict(conn.execute(
                    "SELECT processing_state,count(*) FROM raw_records GROUP BY processing_state"
                ).fetchall())
                self.assertEqual(states,{"processed":2})

            with ref_connect(env.ref_db) as conn:
                self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0],"ok")
                self.assertEqual(len(conn.execute("PRAGMA foreign_key_check").fetchall()),0)
                self.assertEqual(conn.execute("SELECT count(*) FROM contacts").fetchone()[0],1)
                self.assertEqual(conn.execute("SELECT count(*) FROM contact_emails").fetchone()[0],1)
                self.assertEqual(conn.execute("SELECT count(*) FROM organizations").fetchone()[0],1)
                self.assertEqual(conn.execute("SELECT count(*) FROM promotion_receipts").fetchone()[0],2)
                sources={r[0] for r in conn.execute("SELECT DISTINCT source_key FROM source_observations")}
                self.assertEqual(sources,{"apollo","hunter"})
                master=conn.execute("SELECT * FROM v_contact_master").fetchone()
                self.assertEqual(master["last_name"],"DUPONT")
                self.assertEqual(master["first_name"],"Jean")
                self.assertEqual(master["primary_email"],"jean.dupont@garage-x.fr")
                self.assertEqual(master["organization_domain"],"garage-x.fr")
                self.assertEqual(master["job_role"],"responsable_vo")
                self.assertEqual(conn.execute("SELECT count(*) FROM v_prospectable_contacts").fetchone()[0],1)

            with AgentState(env.state_db).connect() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM sourcing_decisions").fetchone()[0],2)
                self.assertEqual(conn.execute("SELECT count(*) FROM sourcing_errors").fetchone()[0],0)

            idle=env.agent().process_batch()
            self.assertEqual(idle["claimed"],0)
        finally:
            env.stop()

    def test_simulation_is_non_destructive(self):
        env=ApiPair(self.root)
        try:
            source=self.root/"simulation.jsonl"
            write_jsonl(source,[
                '{"email":"simulation@garage.fr","company":"Garage Simulation","website":"garage.fr","sector":"automobile occasion"}'
            ])
            ingest_file(env.raw_data,source,source_key="simulation-source")
            result=env.agent(mode="simulation").process_batch()
            self.assertEqual(result["claimed"],1)
            self.assertEqual(result["promoted"],1)

            with connect_fast(env.raw_path) as conn:
                state=conn.execute("SELECT processing_state FROM raw_records").fetchone()[0]
                attempts=conn.execute("SELECT attempts FROM raw_record_attempts").fetchone()[0]
            self.assertEqual(state,"pending")
            self.assertEqual(attempts,0)

            with ref_connect(env.ref_db) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM contacts").fetchone()[0],0)
                self.assertEqual(conn.execute("SELECT count(*) FROM promotion_receipts").fetchone()[0],0)
        finally:
            env.stop()

    def test_reference_outage_does_not_lose_raw_and_recovers(self):
        env=ApiPair(self.root,with_reference=False)
        try:
            source=self.root/"retry.jsonl"
            write_jsonl(source,[
                '{"email":"retry@garage.fr","company":"Garage Retry","website":"garage-retry.fr","sector":"automobile occasion"}'
            ])
            ingest_file(env.raw_data,source,source_key="retry-source")

            sock=socket.socket()
            sock.bind(("127.0.0.1",0))
            dead_port=sock.getsockname()[1]
            sock.close()
            bad_url=f"http://127.0.0.1:{dead_port}"

            first=env.agent(ref_url=bad_url).process_batch()
            self.assertEqual(first["failed"],1)
            with connect_fast(env.raw_path) as conn:
                state=conn.execute("SELECT processing_state FROM raw_records").fetchone()[0]
                attempts=conn.execute("SELECT attempts FROM raw_record_attempts").fetchone()[0]
            self.assertEqual(state,"pending")
            self.assertEqual(attempts,1)

            env.start_reference()
            second=env.agent().process_batch()
            self.assertEqual(second["promoted"],1)
            self.assertEqual(second["failed"],0)
            with connect_fast(env.raw_path) as conn:
                state=conn.execute("SELECT processing_state FROM raw_records").fetchone()[0]
            self.assertEqual(state,"processed")
            with ref_connect(env.ref_db) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM contacts").fetchone()[0],1)
                self.assertEqual(conn.execute("SELECT count(*) FROM promotion_receipts").fetchone()[0],1)
        finally:
            env.stop()

if __name__=="__main__":
    unittest.main()
