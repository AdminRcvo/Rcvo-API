#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from prospecting_engine import (
    claim_due,enroll_eligible,record_events,register_message_version,
    release_claim,suppress,upsert_campaign
)
from reference_engine import connect,init_db

class Server(ThreadingHTTPServer):
    db:Path
    token_env:str

class Handler(BaseHTTPRequestHandler):
    server_version="RcvoReferenceProspectingAPI/2.0"

    def log_message(self,fmt,*args):
        return

    def _write(self,status:int,payload):
        body=json.dumps(payload,ensure_ascii=False,separators=(",",":"),default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self)->bool:
        expected=os.getenv(self.server.token_env)
        return bool(expected and self.headers.get("Authorization")=="Bearer "+expected)

    def _body(self,max_bytes:int=20*1024*1024)->dict:
        length=int(self.headers.get("Content-Length","0"))
        if length<=0:
            return {}
        if length>max_bytes:
            raise ValueError("request body too large")
        value=json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value,dict):
            raise ValueError("JSON object required")
        return value

    def do_GET(self):
        if self.path=="/health":
            try:
                with connect(self.server.db) as conn:
                    check=conn.execute("PRAGMA quick_check").fetchone()[0]
                self._write(200,{"status":"ok" if check=="ok" else "degraded","quick_check":check})
            except Exception as exc:
                self._write(503,{"status":"error","error":str(exc)})
            return
        self._write(404,{"error":"not_found"})

    def do_POST(self):
        if not self._auth():
            self._write(401,{"error":"unauthorized"})
            return
        try:
            body=self._body()

            if self.path=="/v1/prospecting/campaigns/upsert":
                self._write(200,upsert_campaign(self.server.db,body))
                return

            if self.path=="/v1/prospecting/messages/register":
                self._write(200,register_message_version(self.server.db,body))
                return

            if self.path=="/v1/prospecting/enroll":
                campaign=str(body.get("campaign_rcvo_id") or "").strip()
                if not campaign:
                    raise ValueError("campaign_rcvo_id required")
                self._write(200,enroll_eligible(
                    self.server.db,campaign,int(body.get("limit",5000))
                ))
                return

            if self.path=="/v1/prospecting/claim":
                campaign=str(body.get("campaign_rcvo_id") or "").strip()
                worker=str(body.get("worker_id") or "").strip()
                if not campaign or not worker:
                    raise ValueError("campaign_rcvo_id and worker_id required")
                self._write(200,{"contacts":claim_due(
                    self.server.db,campaign,worker,
                    int(body.get("limit",200)),
                    int(body.get("lease_seconds",300))
                )})
                return

            if self.path=="/v1/prospecting/release":
                release_claim(
                    self.server.db,
                    int(body["campaign_contact_id"]),
                    str(body.get("worker_id") or ""),
                    next_eligible_at=body.get("next_eligible_at"),
                    error=bool(body.get("error",False)),
                )
                self._write(200,{"status":"released"})
                return

            if self.path=="/v1/prospecting/events/batch":
                events=body.get("events")
                if not isinstance(events,list):
                    raise ValueError("events array required")
                self._write(200,record_events(self.server.db,events))
                return

            if self.path=="/v1/prospecting/suppress":
                self._write(200,suppress(self.server.db,body))
                return

            self._write(404,{"error":"not_found"})
        except RuntimeError as exc:
            self._write(409,{"error":"conflict","message":str(exc)})
        except Exception as exc:
            self._write(400,{"error":"bad_request","message":str(exc)})

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",type=Path,default=ROOT/"data"/"rcvo-reference.sqlite")
    p.add_argument("--bind",default="127.0.0.1")
    p.add_argument("--port",type=int,default=8093)
    p.add_argument("--token-env",default="RCVO_REFERENCE_PROSPECTION_TOKEN")
    args=p.parse_args()

    init_db(args.db)
    server=Server((args.bind,args.port),Handler)
    server.db=args.db
    server.token_env=args.token_env
    server.serve_forever()

if __name__=="__main__":
    main()
