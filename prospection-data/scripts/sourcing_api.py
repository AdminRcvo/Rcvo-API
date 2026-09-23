#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from ingest_runtime import connect_fast
from raw_queue import ack_record, claim_records, fail_record, reject_record, release_record, queue_stats
from rcvo_data import init_databases

class Server(ThreadingHTTPServer):
    data_dir:Path
    raw_path:Path
    token_env:str
    max_attempts:int

class Handler(BaseHTTPRequestHandler):
    server_version="RcvoRawSourcingAPI/1.0"

    def log_message(self,fmt,*args):
        return

    def _write(self,status:int,payload:dict):
        body=json.dumps(payload,ensure_ascii=False,separators=(",",":"),default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self) -> bool:
        expected=os.getenv(self.server.token_env)
        return bool(expected and self.headers.get("Authorization")=="Bearer "+expected)

    def _body(self) -> dict:
        length=int(self.headers.get("Content-Length","0"))
        if length<=0:
            return {}
        if length>1024*1024:
            raise ValueError("request body too large")
        value=json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value,dict):
            raise ValueError("JSON object required")
        return value

    def do_GET(self):
        if self.path=="/health":
            try:
                with connect_fast(self.server.raw_path) as conn:
                    check=conn.execute("PRAGMA quick_check").fetchone()[0]
                self._write(200,{"status":"ok" if check=="ok" else "degraded","quick_check":check})
            except Exception as exc:
                self._write(503,{"status":"error","error":str(exc)})
            return
        if not self._auth():
            self._write(401,{"error":"unauthorized"})
            return
        if self.path=="/v1/raw/stats":
            self._write(200,{"queue":queue_stats(self.server.raw_path)})
            return
        self._write(404,{"error":"not_found"})

    def do_POST(self):
        if not self._auth():
            self._write(401,{"error":"unauthorized"})
            return
        try:
            body=self._body()
            if self.path=="/v1/raw/claim":
                worker=str(body.get("worker_id","")).strip()
                if not worker:
                    raise ValueError("worker_id required")
                limit=max(1,min(int(body.get("limit",500)),5000))
                lease=max(30,min(int(body.get("lease_seconds",300)),3600))
                items=claim_records(
                    self.server.raw_path,worker,limit=limit,lease_seconds=lease
                )
                self._write(200,{"items":items})
                return

            m=re.fullmatch(r"/v1/raw/(\d+)/(release|ack|fail|reject)",self.path)
            if not m:
                self._write(404,{"error":"not_found"})
                return
            record_id=int(m.group(1)); action=m.group(2)
            worker=str(body.get("worker_id","")).strip()
            if not worker:
                raise ValueError("worker_id required")
            if action=="release":
                release_record(self.server.raw_path,record_id,worker)
                self._write(200,{"state":"pending"})
            elif action=="ack":
                ack_record(self.server.raw_path,record_id,worker)
                self._write(200,{"state":"processed"})
            elif action=="fail":
                state=fail_record(
                    self.server.raw_path,record_id,worker,
                    str(body.get("error","processing failed")),
                    max_attempts=self.server.max_attempts,
                )
                self._write(200,{"state":state})
            else:
                reject_record(
                    self.server.raw_path,record_id,worker,
                    str(body.get("reason","rejected")),
                )
                self._write(200,{"state":"rejected"})
        except RuntimeError as exc:
            self._write(409,{"error":"lease_conflict","message":str(exc)})
        except Exception as exc:
            self._write(400,{"error":"bad_request","message":str(exc)})

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",type=Path,default=ROOT/"data")
    p.add_argument("--bind",default="127.0.0.1")
    p.add_argument("--port",type=int,default=8091)
    p.add_argument("--token-env",default="RCVO_RAW_SOURCING_TOKEN")
    p.add_argument("--max-attempts",type=int,default=5)
    args=p.parse_args()

    raw_path,_=init_databases(args.data_dir)
    server=Server((args.bind,args.port),Handler)
    server.data_dir=args.data_dir
    server.raw_path=raw_path
    server.token_env=args.token_env
    server.max_attempts=args.max_attempts
    server.serve_forever()

if __name__=="__main__":
    main()
