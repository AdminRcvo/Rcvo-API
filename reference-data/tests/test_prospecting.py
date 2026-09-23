import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from reference_engine import connect,promote
from prospecting_engine import (
    claim_due,enroll_eligible,record_events,suppress,upsert_campaign
)

class ProspectingReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/"reference.sqlite"
        self.contact=promote(self.db,{
            "raw_ref":{"source_key":"test","raw_batch_uuid":"b","raw_record_id":1},
            "contact":{"first_name":"Jean","last_name":"DUPONT","qualification_status":"usable","vo_relevance":"confirmed","confidence":0.9},
            "email":{"value":"jean@garage.fr","deliverability_status":"valid","confidence":0.9},
            "organization":{"display_name":"Garage","domain":"garage.fr","vo_relevance":"confirmed","confidence":0.9}
        })["contact_rcvo_id"]
    def tearDown(self): self.tmp.cleanup()

    def campaign(self,status="running",starts_at=None,ends_at=None):
        return upsert_campaign(self.db,{
            "rcvo_id":"CMP-TEST","name":"Test","status":status,
            "starts_at":starts_at,"ends_at":ends_at
        })

    def test_enroll_claim_send_and_idempotent_event(self):
        self.campaign()
        enrolled=enroll_eligible(self.db,"CMP-TEST",100)
        self.assertEqual(enrolled["enrolled"],1)
        rows=claim_due(self.db,"CMP-TEST","worker",10,60)
        self.assertEqual(len(rows),1)
        event={
            "event_type":"email_sent","event_key":"send-1",
            "contact_rcvo_id":self.contact,"campaign_rcvo_id":"CMP-TEST",
            "sender_mailbox":"box@example.com","message_key":"intro:v1",
            "provider_message_id":"m1","next_eligible_at":"2099-01-01T00:00:00.000Z",
            "completed":False
        }
        first=record_events(self.db,[event])
        second=record_events(self.db,[event])
        self.assertEqual(first["inserted"],1)
        self.assertEqual(second["duplicate"],1)
        with connect(self.db) as conn:
            cc=conn.execute("SELECT step_index,status FROM campaign_contacts").fetchone()
            ps=conn.execute("SELECT total_emails_sent,next_eligible_at FROM prospecting_state").fetchone()
        self.assertEqual(cc["step_index"],1)
        self.assertEqual(cc["status"],"eligible")
        self.assertEqual(ps["total_emails_sent"],1)

    def test_future_scheduled_campaign_cannot_claim(self):
        future=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(timespec="milliseconds").replace("+00:00","Z")
        self.campaign("scheduled",future,None)
        enroll_eligible(self.db,"CMP-TEST",100)
        self.assertEqual(claim_due(self.db,"CMP-TEST","worker",10,60),[])

    def test_reply_removes_contact_from_future_eligibility(self):
        self.campaign(); enroll_eligible(self.db,"CMP-TEST",100)
        record_events(self.db,[{
            "event_type":"reply_received","event_key":"reply-1",
            "contact_rcvo_id":self.contact,"campaign_rcvo_id":"CMP-TEST"
        }])
        with connect(self.db) as conn:
            count=conn.execute("SELECT count(*) FROM v_prospectable_contacts").fetchone()[0]
            status=conn.execute("SELECT status FROM prospecting_state").fetchone()[0]
        self.assertEqual(status,"responded")
        self.assertEqual(count,0)

    def test_unsubscribe_suppresses_reference(self):
        result=suppress(self.db,{"contact_rcvo_id":self.contact,"reason":"optout","source":"test"})
        self.assertTrue(result["suppressed"])
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM v_prospectable_contacts").fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT status FROM prospecting_state").fetchone()[0],"suppressed")
            self.assertEqual(conn.execute("SELECT deliverability_status FROM contact_emails").fetchone()[0],"unsubscribed")

if __name__=="__main__": unittest.main()
