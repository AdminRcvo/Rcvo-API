#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

from run_agent import build_agent

class HealthHandler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args): return
    def do_GET(self):
        if self.path not in {"/health","/ready"}:
            self.send_response(404); self.end_headers(); return
        try:
            data=self.server.agent.health()
            status=200 if data.get("status")=="ok" else 503
        except Exception as exc:
            data={"status":"error","error":str(exc)}
            status=503
        body=json.dumps(data,ensure_ascii=False,separators=(",",":")).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--state-db",type=Path,default=Path("/data/sourcing-agent.sqlite"))
    p.add_argument("--profiles-dir",type=Path,default=Path("/app/profiles"))
    p.add_argument("--raw-url")
    p.add_argument("--reference-url")
    p.add_argument("--mode",choices=["simulation","production"],default=os.getenv("RCVO_SOURCING_MODE","production"))
    p.add_argument("--claim-size",type=int,default=int(os.getenv("RCVO_SOURCING_CLAIM_SIZE","500")))
    p.add_argument("--lease-seconds",type=int,default=int(os.getenv("RCVO_SOURCING_LEASE_SECONDS","300")))
    p.add_argument("--idle-sleep",type=float,default=float(os.getenv("RCVO_SOURCING_IDLE_SLEEP","5")))
    p.add_argument("--http-timeout",type=float,default=float(os.getenv("RCVO_SOURCING_HTTP_TIMEOUT","30")))
    p.add_argument("--worker-id")
    p.add_argument("--bind",default="0.0.0.0")
    p.add_argument("--port",type=int,default=int(os.getenv("PORT","8080")))
    args=p.parse_args()

    agent=build_agent(args)
    server=ThreadingHTTPServer((args.bind,args.port),HealthHandler)
    server.agent=agent
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    agent.run_forever()

if __name__=="__main__":
    main()
