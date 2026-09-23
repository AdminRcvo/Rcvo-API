#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from agent import AgentConfig,SourcingAgent
from contracts import RawItem
from fake_gateways import MemoryRawGateway,MemoryReferenceGateway
from state import AgentState

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--rows",type=int,default=10000)
    args=p.parse_args()
    items=[
        RawItem(
            id=i,batch_id=1,raw_batch_uuid="bench-batch",source_key="benchmark",
            source_record_id=f"b-{i}",source_row_number=i,captured_at=None,
            payload={
                "first_name":f"Prenom{i}","last_name":f"NOM{i}",
                "email":f"contact{i}@garage{i%2500}.fr",
                "company":f"Garage {i%2500}",
                "website":f"garage{i%2500}.fr",
                "job_title":"Vendeur VO","sector":"automobile occasion"
            }
        ) for i in range(args.rows)
    ]
    raw=MemoryRawGateway(items)
    ref=MemoryReferenceGateway()
    with tempfile.TemporaryDirectory() as tmp:
        state=AgentState(Path(tmp)/"agent.sqlite")
        agent=SourcingAgent(raw,ref,state,AgentConfig(
            mode="production",claim_size=args.rows,worker_id="benchmark"
        ))
        started=time.perf_counter()
        result=agent.process_batch()
        elapsed=time.perf_counter()-started
        print(
            f"rows={result['promoted']} elapsed={elapsed:.3f}s "
            f"rows_per_second={(result['promoted']/elapsed if elapsed else 0):,.0f}"
        )

if __name__=="__main__":
    main()
