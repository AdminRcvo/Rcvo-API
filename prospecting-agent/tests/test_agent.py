import tempfile
import unittest
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from agent import ProspectingAgent,RuntimeConfig
from deliverability import Gate,health_gate
from inbound import classify
from mailer import build_message
from state import State

class FakeReference:
    def __init__(self):
        self.events_seen=[]
        self.suppressions=[]
        self.fail_events=0
        self.fail_suppress=0
        self.claimed=False
        self.completed=False
        self.step=0
        self.contact={
            "campaign_contact_id":1,
            "step_index":0,
            "send_attempts":0,
            "contact_id":1,
            "rcvo_id":"CNT-TEST",
            "first_name":"Jean",
            "last_name":"DUPONT",
            "primary_email":"jean.dupont@garage.fr",
            "organization_name":"Garage Test",
            "organization_domain":"garage.fr",
        }
    def health(self): return {"status":"ok"}
    def upsert_campaign(self,payload): return payload
    def register_message(self,payload): return payload
    def enroll(self,campaign_rcvo_id,limit): return {"enrolled":1,"skipped":0}
    def claim(self,campaign_rcvo_id,worker_id,limit,lease_seconds):
        if self.completed or self.claimed: return []
        self.claimed=True
        row=dict(self.contact); row["step_index"]=self.step
        return [row]
    def release(self,campaign_contact_id,worker_id,*,next_eligible_at=None,error=False):
        self.claimed=False
        if error: self.completed=True
    def events(self,events):
        if self.fail_events:
            self.fail_events-=1
            raise ConnectionError("reference unavailable")
        self.events_seen.extend(events)
        for e in events:
            if e["event_type"] in ("email_sent","followup_sent"):
                self.claimed=False
                self.step+=1
                if e.get("completed"): self.completed=True
        return {"inserted":len(events),"duplicate":0,"failed":0}
    def suppress(self,payload):
        if self.fail_suppress:
            self.fail_suppress-=1
            raise ConnectionError("reference unavailable")
        self.suppressions.append(payload)
        return {"suppressed":True}

def configs():
    campaign={
        "rcvo_id":"CMP-TEST","name":"Test","status":"running",
        "post_campaign_cooldown_days":180,
        "sequence":[{
            "message_key":"intro","version":1,
            "subject":"Bonjour {first_name}",
            "text":"Présentation Rcvo {video_url}",
            "html":"<p>Présentation Rcvo <a href=\"{video_url}\">vidéo</a></p>",
            "video_url":"https://media.example/video.mp4"
        }]
    }
    mailbox={
        "mailbox_id":"box-1","address":"sender@example.com","from_name":"Rcvo",
        "transport":"dry_run","daily_cap":100,"hourly_cap":100,
        "recipient_domain_hourly_cap":100,"min_interval_seconds":0,
        "timezone":"Europe/Paris","weekdays":[0,1,2,3,4,5,6],
        "window_start":"00:00","window_end":"23:59",
        "pause_on_any_complaint":True
    }
    return campaign,[mailbox],{"name":"Rcvo","postal_address":"Adresse test"}

class ProspectingAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.state=State(Path(self.tmp.name)/"state.sqlite")
        self.ref=FakeReference()
        campaign,mailboxes,identity=configs()
        self.agent=ProspectingAgent(
            self.ref,self.state,
            RuntimeConfig(
                campaign=campaign,mailboxes=mailboxes,identity=identity,
                public_base_url="http://localhost:8080",
                tracking_secret="test-secret",
                claim_size=10,lease_seconds=60,enroll_limit=100,
                worker_id="test-worker",enforce_send_window=False,
            )
        )
        self.agent.bootstrap()
    def tearDown(self): self.tmp.cleanup()

    def test_send_syncs_once_with_outbox(self):
        result=self.agent.process_once()
        self.assertEqual(result["sent"],1)
        self.assertEqual(self.state.stats()["outbox_synced"],1)
        self.assertEqual(len([e for e in self.ref.events_seen if e["event_type"]=="email_sent"]),1)
        again=self.agent.process_once()
        self.assertEqual(again["sent"],0)
        self.assertEqual(self.state.stats()["outbox_synced"],1)

    def test_reference_failure_after_send_does_not_resend(self):
        self.ref.fail_events=1
        first=self.agent.process_once()
        self.assertEqual(first["sent"],1)
        self.assertEqual(self.state.stats()["outbox_sent_unsynced"],1)
        second=self.agent.process_once()
        self.assertEqual(second["sent"],0)
        self.assertEqual(self.state.stats()["outbox_synced"],1)
        self.assertEqual(len([e for e in self.ref.events_seen if e["event_type"]=="email_sent"]),1)

    def test_unsubscribe_is_durable_during_reference_outage(self):
        self.ref.fail_suppress=1
        result=self.agent.request_unsubscribe("CNT-TEST","CMP-TEST")
        self.assertTrue(result["accepted"])
        self.assertEqual(self.state.stats()["suppressions_unsynced"],1)
        self.agent.sync_suppressions()
        self.assertEqual(self.state.stats()["suppressions_unsynced"],0)
        self.assertEqual(self.ref.suppressions[-1]["reason"],"optout")

    def test_spam_complaint_pauses_mailbox(self):
        self.agent.process_once()
        with self.state.connect() as conn:
            msg=conn.execute("SELECT message_id FROM outbox").fetchone()[0]
        self.agent.handle_feedback({
            "event_type":"spam_complaint","message_id":msg,"event_key":"complaint-1"
        })
        with self.state.connect() as conn:
            status=conn.execute("SELECT status FROM mailboxes WHERE mailbox_id='box-1'").fetchone()[0]
        self.assertEqual(status,"health_paused")
        self.assertTrue(any(e["event_type"]=="spam_complaint" for e in self.ref.events_seen))

    def test_mail_contains_visible_and_one_click_unsubscribe(self):
        msg=build_message(
            mailbox={"address":"sender@example.com","from_name":"Rcvo"},
            to_email="a@b.fr",subject="Test",text_body="Bonjour",html_body="<p>Bonjour</p>",
            unsubscribe_url="https://rcvo.example/unsubscribe/x",
            message_id="<id@example.com>",identity_name="Rcvo",identity_address="Adresse Rcvo"
        )
        self.assertIn("https://rcvo.example/unsubscribe/x",msg["List-Unsubscribe"])
        self.assertEqual(msg["List-Unsubscribe-Post"],"List-Unsubscribe=One-Click")
        text=msg.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("Se désinscrire" if False else "ne plus recevoir",text.lower())
        self.assertIn("Adresse Rcvo",text)

    def test_inbound_reply_and_dsn_are_classified(self):
        reply=(
            b"From: prospect@garage.fr\r\n"
            b"To: sender@example.com\r\n"
            b"Subject: Re: Rcvo\r\n"
            b"In-Reply-To: <abc@example.com>\r\n"
            b"\r\nMerci"
        )
        event=classify(reply)
        self.assertEqual(event["event_type"],"reply_received")
        self.assertEqual(event["message_id"],"<abc@example.com>")

        dsn=(
            b"From: MAILER-DAEMON@example.net\r\n"
            b"Subject: Delivery Status Notification\r\n"
            b"Content-Type: multipart/report; report-type=delivery-status; boundary=x\r\n"
            b"\r\n--x\r\nContent-Type: text/plain\r\n\r\nFailed\r\n"
            b"--x\r\nContent-Type: message/delivery-status\r\n\r\n"
            b"Final-Recipient: rfc822; bad@example.com\r\n"
            b"Action: failed\r\nStatus: 5.1.1\r\n\r\n"
            b"--x\r\nContent-Type: message/rfc822\r\n\r\n"
            b"Message-ID: <abc@example.com>\r\nFrom: sender@example.com\r\nTo: bad@example.com\r\n\r\n"
            b"--x--\r\n"
        )
        bounce=classify(dsn)
        self.assertEqual(bounce["event_type"],"bounce")
        self.assertEqual(bounce["message_id"],"<abc@example.com>")

    def test_complaint_health_gate_blocks(self):
        gate=health_gate({"pause_on_any_complaint":True},{"sent":1000,"spam_complaint":1})
        self.assertFalse(gate.allowed)

if __name__=="__main__": unittest.main()
