from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from service import Handler,Server
from state import State

class FakeReference:
    def health(self):
        return {"status":"ok"}
    def dashboard(self):
        return {
            "contacts":{"total":1234,"active":1200,"prospectable":900,"suppressed":34,"campaign_enrollments":700},
            "prospecting":{"emails_sent":456,"initial_emails_sent":400,"followups_sent":56,"video_views":78,"replies":12,"delivered":430,"soft_bounces":3,"hard_bounces":2,"spam_complaints":0,"unsubscribes":5,"delivery_deferred":4,"delivery_rejected":1,"events_total":991},
            "campaigns":{"running":1}
        }

class FakeAgent:
    def __init__(self,state):
        self.state=state
        self.reference=FakeReference()

def request(base,path,method="GET",payload=None,token=None):
    data=None if payload is None else json.dumps(payload).encode()
    headers={"Content-Type":"application/json"}
    if token:
        headers["Authorization"]="Bearer "+token
    req=urllib.request.Request(base+path,data=data,method=method,headers=headers)
    with urllib.request.urlopen(req,timeout=3) as response:
        body=response.read()
        return response.status,json.loads(body.decode()) if body else {}

class AdminControlApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        state=State(Path(self.tmp.name)/"agent.sqlite")
        self.server=Server(("127.0.0.1",0),Handler)
        self.server.agent=FakeAgent(state)
        self.server.feedback_token="feedback-secret"
        self.server.admin_token="admin-secret"
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base=f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_control_defaults_off_and_requires_admin_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            request(self.base,"/v1/admin/prospecting/control")
        self.assertEqual(ctx.exception.code,401)

        _,control=request(
            self.base,"/v1/admin/prospecting/control",token="admin-secret"
        )
        self.assertEqual(control["outbound_state"],"off")
        self.assertEqual(control["mode"],"production")

    def test_admin_can_start_pause_stop_and_choose_mode(self):
        _,started=request(
            self.base,"/v1/admin/prospecting/control","POST",
            {"action":"start","mode":"pilot"},"admin-secret"
        )
        self.assertEqual(started["outbound_state"],"on")
        self.assertEqual(started["mode"],"pilot")

        _,paused=request(
            self.base,"/v1/admin/prospecting/control","POST",
            {"action":"pause"},"admin-secret"
        )
        self.assertEqual(paused["outbound_state"],"paused")

        _,stopped=request(
            self.base,"/v1/admin/prospecting/control","POST",
            {"action":"stop","mode":"production"},"admin-secret"
        )
        self.assertEqual(stopped["outbound_state"],"off")
        self.assertEqual(stopped["mode"],"production")

    def test_dashboard_combines_agent_and_reference_metrics(self):
        _,data=request(
            self.base,"/v1/admin/prospecting/dashboard",token="admin-secret"
        )
        self.assertEqual(data["agent"]["control"]["outbound_state"],"off")
        self.assertEqual(data["reference"]["contacts"]["total"],1234)
        self.assertEqual(data["reference"]["prospecting"]["emails_sent"],456)
        self.assertEqual(data["reference"]["prospecting"]["video_views"],78)
        self.assertEqual(data["health"]["reference"]["status"],"ok")

if __name__=="__main__":
    unittest.main()
