#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from reference_engine import promote

def make_payload(i:int) -> dict:
    return {
        "raw_ref":{
            "source_key":"benchmark",
            "raw_batch_uuid":"benchmark-batch",
            "raw_record_id":i,
            "source_record_id":f"bench-{i}",
        },
        "contact":{
            "last_name":f"NOM{i}",
            "first_name":f"Prenom{i}",
            "city":f"Ville {i%500}",
            "qualification_status":"usable",
            "vo_relevance":"confirmed",
            "confidence":0.9,
        },
        "email":{
            "value":f"contact{i}@garage{i%5000}.fr",
            "kind":"personal_business",
            "deliverability_status":"valid",
            "confidence":0.95,
        },
        "organization":{
            "display_name":f"Garage {i%5000}",
            "domain":f"garage{i%5000}.fr",
            "city":f"Ville {i%500}",
            "vo_relevance":"confirmed",
            "confidence":0.9,
        },
        "employment":{
            "job_title":"Vendeur VO",
            "job_role":"vendeur_vo",
            "is_current":True,
            "confidence":0.9,
        },
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--rows",type=int,default=2000)
    args=p.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        db=Path(tmp)/"reference.sqlite"
        start=time.perf_counter()
        for i in range(args.rows):
            promote(db,make_payload(i))
        elapsed=time.perf_counter()-start
        print(json.dumps({
            "rows":args.rows,
            "elapsed_seconds":round(elapsed,3),
            "rows_per_second":round(args.rows/elapsed if elapsed else 0),
        },indent=2))

if __name__=="__main__":
    main()
