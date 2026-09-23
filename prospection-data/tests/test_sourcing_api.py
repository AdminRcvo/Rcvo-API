import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
sys.path.insert(0,str(ROOT/"scripts"))

from ingest_runtime import ingest_file
from rcvo_data import init_databases
from sourcing_api import Handler,Server

def request(base,path,payload=None,token=None):
    data=None if payload is None else json.dumps(payload).encode()
    headers={"Content-Type":"application/json"}
    if token:
        headers["Authorization"]="Bearer "+token
    req=urllib.request.Request(base+path,data=data,method="POST" if payload is not None else "GET",headers=headers)
    with urllib.request.urlopen(req,timeout=3) as response:
        return response.status,json.loads(response.read().decode())

class RawSourcingApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.data=Path(self.tmp.name)/"data"
        source=Path(self.tmp.name)/"contacts.jsonl"
        source.write_text('{"email":"a@garage.fr","company":"Garage A"}\n',encoding="utf-8")
        ingest_file(self.data,source,source_key="provider-a")
        raw,_=init_databases(self.data)
        os.environ["TEST_RAW_TOKEN"]="secret"
        self.server=Server(("127.0.0.1",0),Handler)
        self.server.data_dir=self.data
        self.server.raw_path=raw
        self.server.token_env="TEST_RAW_TOKEN"
        self.server.max_attempts=5
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base=f"http://127.0.0.1:{self.server.server_port}"
    def tearDown(self):
        self.server.shutdown(); self.server.server_close()
        os.environ.pop("TEST_RAW_TOKEN",None)
        self.tmp.cleanup()

    def test_claim_contains_true_source_and_batch_and_release(self):
        status,data=request(self.base,"/v1/raw/claim",{
            "worker_id":"worker-a","limit":10,"lease_seconds":60
        },"secret")
        self.assertEqual(status,200)
        self.assertEqual(len(data["items"]),1)
        item=data["items"][0]
        self.assertEqual(item["source_key"],"provider-a")
        self.assertTrue(item["raw_batch_uuid"])
        request(self.base,f"/v1/raw/{item['id']}/release",{"worker_id":"worker-a"},"secret")
        _,again=request(self.base,"/v1/raw/claim",{
            "worker_id":"worker-b","limit":10,"lease_seconds":60
        },"secret")
        self.assertEqual(len(again["items"]),1)
        request(self.base,f"/v1/raw/{item['id']}/ack",{"worker_id":"worker-b"},"secret")
        _,stats=request(self.base,"/v1/raw/stats",None,"secret")
        self.assertEqual(stats["queue"]["processed"],1)

    def test_private_routes_require_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            request(self.base,"/v1/raw/claim",{"worker_id":"x"})
        self.assertEqual(ctx.exception.code,401)

if __name__=="__main__":
    unittest.main()
