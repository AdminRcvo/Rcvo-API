from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RAW_ROOT=ROOT/"prospection-data"
REF_ROOT=ROOT/"reference-data"
SOURCING_ROOT=ROOT/"sourcing-agent"
PROSPECTING_ROOT=ROOT/"prospecting-agent"

def load_script(name:str,path:Path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module

def clear_generic_modules():
    for name in (
        "agent","state","contracts","deliverability","mailer","tokens",
        "reference_gateway","http_gateways","normalization","profiles",
        "config","transport","inbound",
    ):
        sys.modules.pop(name,None)

class FourBrickCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.raw_data=self.root/"raw-data"
        self.ref_db=self.root/"reference.sqlite"

        clear_generic_modules()
        for p in (RAW_ROOT/"src",REF_ROOT/"src",SOURCING_ROOT/"src"):
            sys.path.insert(0,str(p))

        from rcvo_data import init_databases
        from reference_engine import init_db
        self.init_databases=init_databases
        self.raw_path,_=init_databases(self.raw_data)
        init_db(self.ref_db)

        self.raw_token="raw-four-brick"
        self.source_token="source-four-brick"
        self.prospect_token="prospect-four-brick"
        os.environ["E2E_RAW_TOKEN"]=self.raw_token
        os.environ["E2E_SOURCE_TOKEN"]=self.source_token
        os.environ["E2E_PROSPECT_TOKEN"]=self.prospect_token

        raw_api=load_script("four_brick_raw_api",RAW_ROOT/"scripts"/"sourcing_api.py")
        ref_api=load_script("four_brick_ref_api",REF_ROOT/"scripts"/"sourcing_api.py")

        self.raw_server=raw_api.Server(("127.0.0.1",0),raw_api.Handler)
        self.raw_server.data_dir=self.raw_data
        self.raw_server.raw_path=self.raw_path
        self.raw_server.token_env="E2E_RAW_TOKEN"
        self.raw_server.max_attempts=5
        self.raw_thread=threading.Thread(target=self.raw_server.serve_forever,daemon=True)
        self.raw_thread.start()
        self.raw_url=f"http://127.0.0.1:{self.raw_server.server_port}"

        self.ref_server=ref_api.Server(("127.0.0.1",0),ref_api.Handler)
        self.ref_server.db=self.ref_db
        self.ref_server.token_env="E2E_SOURCE_TOKEN"
        self.ref_server.prospecting_token_env="E2E_PROSPECT_TOKEN"
        self.ref_thread=threading.Thread(target=self.ref_server.serve_forever,daemon=True)
        self.ref_thread.start()
        self.ref_url=f"http://127.0.0.1:{self.ref_server.server_port}"

    def tearDown(self):
        self.raw_server.shutdown(); self.raw_server.server_close()
        self.ref_server.shutdown(); self.ref_server.server_close()
        for key in ("E2E_RAW_TOKEN","E2E_SOURCE_TOKEN","E2E_PROSPECT_TOKEN"):
            os.environ.pop(key,None)
        clear_generic_modules()
        self.tmp.cleanup()

    def test_source_to_raw_to_sourcing_to_reference_to_prospecting_and_metrics(self):
        from ingest_runtime import ingest_file
        source=self.root/"provider.jsonl"
        source.write_text(
            '{"first_name":"jEAN","last_name":"dupont",'
            '"email":"JEAN.DUPONT@GARAGE-X.FR",'
            '"company":"Garage X SAS","website":"https://garage-x.fr",'
            '"city":"Bordeaux","job_title":"Responsable VO",'
            '"sector":"automobile occasion"}\n',
            encoding="utf-8",
        )
        ingest_file(self.raw_data,source,source_key="provider-e2e")

        from agent import AgentConfig,SourcingAgent
        from http_gateways import HttpRawGateway,HttpReferenceGateway
        from state import AgentState

        sourcing=SourcingAgent(
            HttpRawGateway(self.raw_url,self.raw_token,3),
            HttpReferenceGateway(self.ref_url,self.source_token,3),
            AgentState(self.root/"sourcing-state.sqlite"),
            AgentConfig(
                mode="production",claim_size=100,lease_seconds=60,
                worker_id="four-brick-sourcing",
            ),
        )
        sourced=sourcing.process_batch()
        self.assertEqual(sourced["claimed"],1)
        self.assertEqual(sourced["promoted"],1)
        self.assertEqual(sourced["failed"],0)

        from reference_engine import connect
        with connect(self.ref_db) as conn:
            master=conn.execute("SELECT * FROM v_contact_master").fetchone()
            self.assertEqual(master["first_name"],"Jean")
            self.assertEqual(master["last_name"],"DUPONT")
            self.assertEqual(master["primary_email"],"jean.dupont@garage-x.fr")
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM v_prospectable_contacts"
            ).fetchone()[0],1)

        clear_generic_modules()
        sys.path.insert(0,str(PROSPECTING_ROOT/"src"))
        from agent import ProspectingAgent,RuntimeConfig
        from reference_gateway import ReferenceHttpGateway
        from state import State

        campaign={
            "rcvo_id":"CMP-FOUR-BRICK","name":"Four brick candidate","status":"running",
            "post_campaign_cooldown_days":180,
            "sequence":[{
                "message_key":"intro","version":1,
                "subject":"Rcvo {first_name}",
                "text":"Bonjour {first_name}, présentation Rcvo {video_url}",
                "html":"<p>Bonjour {first_name}</p><p><a href=\"{video_url}\">Vidéo Rcvo</a></p>",
                "video_url":"https://media.example/rcvo.mp4",
            }],
        }
        mailboxes=[{
            "mailbox_id":"dry-1","address":"sender@rcvo.example","from_name":"Rcvo",
            "transport":"dry_run","daily_cap":200,"hourly_cap":30,
            "recipient_domain_hourly_cap":8,"min_interval_seconds":0,
            "timezone":"Europe/Paris","weekdays":[0,1,2,3,4,5,6],
            "window_start":"00:00","window_end":"23:59",
        }]
        reference=ReferenceHttpGateway(self.ref_url,self.prospect_token,3)
        state=State(self.root/"prospecting-state.sqlite")
        prospecting=ProspectingAgent(
            reference,state,
            RuntimeConfig(
                campaign=campaign,mailboxes=mailboxes,
                identity={"name":"Rcvo","postal_address":"Adresse test"},
                public_base_url="http://localhost:9999",
                tracking_secret="four-brick-secret",
                claim_size=100,lease_seconds=60,enroll_limit=100,
                worker_id="four-brick-prospecting",enforce_send_window=False,
            ),
        )
        boot=prospecting.bootstrap()
        self.assertEqual(boot["enrolled"],1)

        self.assertEqual(state.control()["outbound_state"],"off")
        state.set_control("on","production")
        sent=prospecting.process_once()
        self.assertEqual(sent["sent"],1)
        self.assertEqual(sent["synced"],1)

        reference.events([{
            "event_type":"video_viewed",
            "event_key":"video:four-brick:1",
            "contact_rcvo_id":master["rcvo_id"],
            "campaign_rcvo_id":"CMP-FOUR-BRICK",
            "message_key":"intro",
            "result":"play",
            "source":"four_brick_test",
        }])

        dashboard=reference.dashboard()
        self.assertEqual(dashboard["contacts"]["total"],1)
        self.assertEqual(dashboard["prospecting"]["emails_sent"],1)
        self.assertEqual(dashboard["prospecting"]["video_views"],1)
        self.assertEqual(dashboard["prospecting"]["spam_complaints"],0)
        self.assertEqual(state.admin_snapshot()["control"]["outbound_state"],"on")

if __name__=="__main__":
    unittest.main()
