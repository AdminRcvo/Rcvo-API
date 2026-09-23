#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

from agent import AgentConfig,SourcingAgent
from http_gateways import HttpRawGateway,HttpReferenceGateway
from profiles import ProfileRegistry
from state import AgentState

def build_agent(args):
    raw_url=args.raw_url or os.getenv("RCVO_RAW_BASE_URL")
    ref_url=args.reference_url or os.getenv("RCVO_REFERENCE_BASE_URL")
    raw_token=os.getenv("RCVO_RAW_SOURCING_TOKEN")
    ref_token=os.getenv("RCVO_REFERENCE_SOURCING_TOKEN")
    missing=[
        name for name,value in [
            ("RCVO_RAW_BASE_URL",raw_url),
            ("RCVO_REFERENCE_BASE_URL",ref_url),
            ("RCVO_RAW_SOURCING_TOKEN",raw_token),
            ("RCVO_REFERENCE_SOURCING_TOKEN",ref_token),
        ] if not value
    ]
    if missing:
        raise SystemExit("Missing configuration: "+", ".join(missing))

    state=AgentState(args.state_db)
    return SourcingAgent(
        HttpRawGateway(raw_url,raw_token,args.http_timeout),
        HttpReferenceGateway(ref_url,ref_token,args.http_timeout),
        state,
        AgentConfig(
            mode=args.mode,
            claim_size=args.claim_size,
            lease_seconds=args.lease_seconds,
            idle_sleep_seconds=args.idle_sleep,
            worker_id=args.worker_id,
        ),
        ProfileRegistry(getattr(args,"profiles_dir",ROOT/"profiles")),
    )

def main():
    p=argparse.ArgumentParser(prog="rcvo-sourcing-agent")
    p.add_argument("--state-db",type=Path,default=ROOT/"data"/"sourcing-agent.sqlite")
    p.add_argument("--profiles-dir",type=Path,default=ROOT/"profiles")
    p.add_argument("--raw-url")
    p.add_argument("--reference-url")
    p.add_argument("--mode",choices=["simulation","production"],default=os.getenv("RCVO_SOURCING_MODE","production"))
    p.add_argument("--claim-size",type=int,default=int(os.getenv("RCVO_SOURCING_CLAIM_SIZE","500")))
    p.add_argument("--lease-seconds",type=int,default=int(os.getenv("RCVO_SOURCING_LEASE_SECONDS","300")))
    p.add_argument("--idle-sleep",type=float,default=float(os.getenv("RCVO_SOURCING_IDLE_SLEEP","5")))
    p.add_argument("--http-timeout",type=float,default=float(os.getenv("RCVO_SOURCING_HTTP_TIMEOUT","30")))
    p.add_argument("--worker-id")
    p.add_argument("--once",action="store_true")
    p.add_argument("--health",action="store_true")
    args=p.parse_args()

    agent=build_agent(args)
    if args.health:
        print(json.dumps(agent.health(),ensure_ascii=False,indent=2))
        return
    if args.once:
        print(json.dumps(agent.process_batch(),ensure_ascii=False,indent=2))
        return
    agent.run_forever()

if __name__=="__main__":
    main()
