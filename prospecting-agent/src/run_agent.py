#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os
from pathlib import Path

from service import build_agent

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(prog="rcvo-prospecting-agent")
    p.add_argument("--state-db",type=Path,default=ROOT/"data"/"prospecting-agent.sqlite")
    p.add_argument("--campaign",type=Path,default=ROOT/"config"/"campaign.json")
    p.add_argument("--mailboxes",type=Path,default=ROOT/"config"/"mailboxes.json")
    p.add_argument("--identity",type=Path,default=ROOT/"config"/"identity.json")
    p.add_argument("--reference-url")
    p.add_argument("--public-base-url")
    p.add_argument("--claim-size",type=int,default=100)
    p.add_argument("--lease-seconds",type=int,default=300)
    p.add_argument("--enroll-limit",type=int,default=5000)
    p.add_argument("--http-timeout",type=float,default=30)
    p.add_argument("--worker-id")
    p.add_argument("--ignore-send-window",action="store_true")
    p.add_argument("--bootstrap",action="store_true")
    p.add_argument("--once",action="store_true")
    p.add_argument("--health",action="store_true")
    args=p.parse_args()
    agent=build_agent(args)
    if args.bootstrap: print(json.dumps(agent.bootstrap(),ensure_ascii=False,indent=2))
    if args.health: print(json.dumps({"reference":agent.reference.health(),"state":agent.state.stats()},ensure_ascii=False,indent=2))
    if args.once: print(json.dumps(agent.process_once(),ensure_ascii=False,indent=2))
    if not any((args.bootstrap,args.health,args.once)): print(json.dumps(agent.process_once(),ensure_ascii=False,indent=2))

if __name__=="__main__": main()
