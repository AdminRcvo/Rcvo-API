#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ingest_runtime import ingest_file

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rows", type=int, default=100000)
    args = p.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "bulk.jsonl"
        with source.open("w", encoding="utf-8") as fh:
            for i in range(args.rows):
                fh.write(json.dumps({
                    "id": i,
                    "email": f"contact{i}@dealer{i % 25000}.fr",
                    "nom": f"NOM{i}",
                    "prenom": f"Prenom{i}",
                    "entreprise": f"Garage {i % 25000}",
                    "ville": f"Ville {i % 5000}",
                    "marche": "vehicule occasion"
                }, separators=(",", ":")) + "\n")
        started = time.perf_counter()
        result = ingest_file(
            root / "data",
            source,
            source_key="end-to-end-benchmark",
            batch_size=25000,
            id_field="id",
        )
        elapsed = time.perf_counter() - started
        print(json.dumps({
            "rows": result["rows"],
            "rejected": result["rejected"],
            "elapsed_seconds": round(elapsed, 3),
            "rows_per_second_end_to_end": round(result["rows"] / elapsed if elapsed else 0),
            "artifact_sha256": result["artifact_sha256"]
        }, indent=2))

if __name__ == "__main__":
    main()
