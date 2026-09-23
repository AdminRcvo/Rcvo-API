import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
sys.path.insert(0,str(ROOT/"scripts"))

from reference_engine import init_db
from sourcing_api import Handler,Server

def request(base,path,payload=None,token=None):
    data=None if payload is None else json.dumps(payload).encode()
    headers={"Content-Type":"application/json"}
    if token:
        headers["Authorization"]="Bearer "+token
    req=urllib.request.Request(base+path,data=data,method="POST" if payload is not None else "GET",headers=headers)
    with urllib.request.urlopen(req,timeout=3) as response:
        return response.status,json.loads(response.read().decode())

def promotion(raw_id,email,contact_ext,company_ext,domain):
    return {
        "raw_ref":{
            "source_key":"provider-a","raw_batch_uuid":"batch-a",
            "raw_record_id":raw_id,"source_record_id":str(raw_id)
        },
        "contact":{
            "external_id":contact_ext,"qualification_status":"usable",
            "vo_relevance":"confirmed","confidence":0.9
        },
        "email":{"value":email,"deliverability_status":"valid","confidence":0.95},
        "organization":{
            "external_id":company_ext,"display_name":"Garage X",
            "domain":domain,"vo_relevance":"confirmed","confidence":0.9
        }
    }

class ReferenceSourcingApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/"reference.sqlite"
        init_db(self.db)
        os.environ["TEST_REF_TOKEN"]="secret"
        self.server=Server(("127.0.0.1",0),Handler)
        self.server.db=self.db
        self.server.token_env="TEST_REF_TOKEN"
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base=f"http://127.0.0.1:{self.server.server_port}"
    def tearDown(self):
        self.server.shutdown(); self.server.server_close()
        os.environ.pop("TEST_REF_TOKEN",None)
        self.tmp.cleanup()

    def test_batch_promotion_then_exact_lookup(self):
        _,result=request(self.base,"/v1/reference/promotions/batch",{
            "promotions":[promotion(1,"a@garage-x.fr","person-a","company-a","garage-x.fr")]
        },"secret")
        self.assertEqual(result["failed"],0)
        self.assertEqual(len(result["results"]),1)
        created=result["results"][0]
        self.assertTrue(created["ok"])

        _,lookup=request(self.base,"/v1/reference/lookup",{
            "source_key":"provider-a",
            "contact_external_id":"person-a",
            "email":"A@GARAGE-X.FR",
            "organization_external_id":"company-a",
            "organization_domain":"https://www.garage-x.fr/"
        },"secret")
        self.assertEqual(lookup["contact_rcvo_id"],created["contact_rcvo_id"])
        self.assertEqual(lookup["organization_rcvo_id"],created["organization_rcvo_id"])
        self.assertIn("exact_email",lookup["methods"])
        self.assertIn("exact_domain",lookup["methods"])
        self.assertEqual(lookup["conflicts"],[])

    def test_conflicting_strong_contact_keys_are_reported(self):
        request(self.base,"/v1/reference/promotions/batch",{
            "promotions":[
                promotion(1,"a@garage-a.fr","person-a","company-a","garage-a.fr"),
                promotion(2,"b@garage-b.fr","person-b","company-b","garage-b.fr")
            ]
        },"secret")
        _,lookup=request(self.base,"/v1/reference/lookup",{
            "source_key":"provider-a",
            "contact_external_id":"person-a",
            "email":"b@garage-b.fr"
        },"secret")
        self.assertTrue(lookup["conflicts"])
        self.assertIsNone(lookup["contact_rcvo_id"])

    def test_private_routes_require_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            request(self.base,"/v1/reference/lookup",{"email":"x@y.fr"})
        self.assertEqual(ctx.exception.code,401)

if __name__=="__main__":
    unittest.main()
