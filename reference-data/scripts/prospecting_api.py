#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from prospecting_store import (
    claim_dispatches,enroll_prospectable,mark_sent,metrics,record_feedback,
    release_dispatch,return_dispatch,upsert_campaign
)
from reference_engine import connect,init_db

class Server(ThreadingHTTPServer):
    db:Path
    token_env:str

class Handler(BaseHTTPRequestHandler):
    server_version="RcvoReferenceProspectingAPI/1.0"

    def log_message(self,fmt,*args):
        return

    def _write(self,status:int,payload:dict):
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
        if not self._auth():
            self._write(401,{"error":"unauthorized"})
            return
        if self.path=="/v1/prospecting/metrics":
            self._write(200,metrics(self.server.db))
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

            if self.path=="/v1/prospecting/enroll":
                campaign=str(body.get("campaign_rcvo_id") or "").strip()
                if not campaign:
                    raise ValueError("campaign_rcvo_id required")
                self._write(200,enroll_prospectable(
                    self.server.db,campaign,int(body.get("limit",10000))
                ))
                return

            if self.path=="/v1/prospecting/claim":
                worker=str(body.get("worker_id") or "").strip()
                if not worker:
                    raise ValueError("worker_id required")
                self._write(200,{"items":claim_dispatches(
                    self.server.db,worker,
                    int(body.get("limit",100)),
                    int(body.get("lease_seconds",300))
                )})
                return

            m=re.fullmatch(r"/v1/prospecting/dispatch/(\d+)/(sent|defer|release)",self.path)
            if m:
                dispatch_id=int(m.group(1)); action=m.group(2)
                worker=str(body.get("worker_id") or "").strip()
                if not worker:
                    raise ValueError("worker_id required")
                if action=="sent":
                    sender=str(body.get("sender_mailbox") or "").strip()
                    if not sender:
                        raise ValueError("sender_mailbox required")
                    self._write(200,mark_sent(
                        self.server.db,dispatch_id,worker,sender,
                        body.get("provider_message_id"),body.get("sent_at")
                    ))
                elif action=="defer":
                    release_dispatch(
                        self.server.db,dispatch_id,worker,
                        str(body.get("reason") or "deferred"),
                        int(body.get("defer_seconds",300))
                    )
                    self._write(200,{"status":"deferred"})
                else:
                    return_dispatch(self.server.db,dispatch_id,worker)
                    self._write(200,{"status":"pending"})
                return

            if self.path=="/v1/prospecting/feedback":
                contact=str(body.get("contact_rcvo_id") or "").strip()
                event=str(body.get("event_type") or "").strip()
                if not contact or not event:
                    raise ValueError("contact_rcvo_id and event_type required")
                record_feedback(
                    self.server.db,contact,event,body.get("occurred_at"),
                    body.get("campaign_rcvo_id"),body.get("provider_message_id"),
                    body.get("email"),body.get("details")
                )
                self._write(200,{"status":"recorded"})
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
    p.add_argument("--token-env",default="RCVO_REFERENCE_PROSPECTING_TOKEN")
    args=p.parse_args()

    init_db(args.db)
    server=Server((args.bind,args.port),Handler)
    server.db=args.db
    server.token_env=args.token_env
    server.serve_forever()

if __name__=="__main__":
    main()
