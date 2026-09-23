from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REF_ROOT=ROOT/"reference-data"
AGENT_ROOT=ROOT/"prospecting-agent"
for p in (REF_ROOT/"src",AGENT_ROOT/"src"):
    sys.path.insert(0,str(p))

from reference_engine import connect,promote
from agent import ProspectingAgent,RuntimeConfig
from reference_gateway import ReferenceHttpGateway
from state import State

def load_script(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module

REF_API=load_script("reference_prospecting_api",REF_ROOT/"scripts"/"sourcing_api.py")

class ProspectingE2E(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.db=self.root/"reference.sqlite"
        self.contact=promote(self.db,{
            "raw_ref":{"source_key":"e2e","raw_batch_uuid":"b","raw_record_id":1},
            "contact":{"first_name":"Jean","last_name":"DUPONT","qualification_status":"qualified","vo_relevance":"confirmed","confidence":0.95},
            "email":{"value":"jean@garage.fr","deliverability_status":"valid","confidence":0.95},
            "organization":{"display_name":"Garage Test","domain":"garage.fr","vo_relevance":"confirmed","confidence":0.95},
            "employment":{"job_title":"Responsable VO","job_role":"responsable_vo","is_current":True,"confidence":0.9}
        })["contact_rcvo_id"]

        os.environ["E2E_SOURCE_TOKEN"]="source-token"
        os.environ["E2E_PROSPECT_TOKEN"]="prospect-token"
        self.server=REF_API.Server(("127.0.0.1",0),REF_API.Handler)
        self.server.db=self.db
        self.server.token_env="E2E_SOURCE_TOKEN"
        self.server.prospecting_token_env="E2E_PROSPECT_TOKEN"
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url=f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close()
        os.environ.pop("E2E_SOURCE_TOKEN",None)
        os.environ.pop("E2E_PROSPECT_TOKEN",None)
        self.tmp.cleanup()

    def test_reference_to_prospecting_agent_to_reference(self):
        campaign={
            "rcvo_id":"CMP-E2E","name":"E2E","status":"running",
            "post_campaign_cooldown_days":180,
            "sequence":[{
                "message_key":"intro","version":1,
                "subject":"Rcvo {first_name}",
                "text":"Bonjour {first_name}, présentation Rcvo {video_url}",
                "html":"<p>Bonjour {first_name}</p><p><a href=\"{video_url}\">Vidéo Rcvo</a></p>",
                "video_url":"https://media.example/rcvo.mp4"
            }]
        }
        mailboxes=[{
            "mailbox_id":"dry-1","address":"sender@rcvo.example","from_name":"Rcvo",
            "transport":"dry_run","daily_cap":100,"hourly_cap":100,
            "recipient_domain_hourly_cap":100,"min_interval_seconds":0,
            "timezone":"Europe/Paris","weekdays":[0,1,2,3,4,5,6],
            "window_start":"00:00","window_end":"23:59"
        }]
        agent=ProspectingAgent(
            ReferenceHttpGateway(self.url,"prospect-token",3),
            State(self.root/"prospecting.sqlite"),
            RuntimeConfig(
                campaign=campaign,mailboxes=mailboxes,
                identity={"name":"Rcvo","postal_address":"Adresse test"},
                public_base_url="http://localhost:9999",
                tracking_secret="e2e-secret",
                claim_size=10,lease_seconds=60,enroll_limit=100,
                worker_id="e2e-prospecting",enforce_send_window=False,
            )
        )
        boot=agent.bootstrap()
        self.assertEqual(boot["enrolled"],1)
        result=agent.process_once()
        self.assertEqual(result["sent"],1)
        self.assertEqual(result["synced"],1)

        with connect(self.db) as conn:
            events=conn.execute(
                "SELECT event_type,sender_mailbox,message_key FROM prospecting_events ORDER BY id"
            ).fetchall()
            kinds=[r["event_type"] for r in events]
            self.assertIn("enrolled",kinds)
            self.assertIn("email_sent",kinds)
            sent=[r for r in events if r["event_type"]=="email_sent"][0]
            self.assertEqual(sent["sender_mailbox"],"sender@rcvo.example")
            self.assertEqual(sent["message_key"],"intro:v1")
            cc=conn.execute("SELECT status,step_index FROM campaign_contacts").fetchone()
            ps=conn.execute("SELECT total_emails_sent,next_eligible_at FROM prospecting_state").fetchone()
            self.assertEqual(cc["status"],"completed")
            self.assertEqual(cc["step_index"],1)
            self.assertEqual(ps["total_emails_sent"],1)
            self.assertIsNotNone(ps["next_eligible_at"])

        self.assertEqual(agent.state.stats()["outbox_synced"],1)

if __name__=="__main__":
    unittest.main()
