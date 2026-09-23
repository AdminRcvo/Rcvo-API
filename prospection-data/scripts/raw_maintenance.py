#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))

from ingest_runtime import connect_fast
from rcvo_data import init_databases, utc_now

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", type=Path, default=ROOT / "data")
    p.add_argument("--full", action="store_true")
    args = p.parse_args()

    raw_path, ref_path = init_databases(args.data_dir)
    report = {}
    for name, path in [("raw", raw_path), ("reference", ref_path)]:
        with connect_fast(path) as conn:
            started = utc_now()
            check = conn.execute("PRAGMA quick_check").fetchone()[0]
            if args.full:
                check = conn.execute("PRAGMA integrity_check").fetchone()[0]
            conn.execute("PRAGMA optimize")
            conn.execute("ANALYZE")
            checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            report[name] = {
                "path": str(path),
                "check": check,
                "wal_checkpoint": checkpoint,
                "size_bytes": path.stat().st_size if path.exists() else 0,
                "started_at": started,
                "completed_at": utc_now(),
            }
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
