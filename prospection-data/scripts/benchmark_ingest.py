#!/usr/bin/env python3
import argparse
import tempfile
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rcvo_data import init_databases, ingest_iterable

def generated_rows(count: int):
    for i in range(count):
        yield {
            "external_id": f"row-{i}",
            "email": f"contact{i}@dealer{i % 20000}.fr",
            "first_name": f"Prenom{i}",
            "last_name": f"NOM{i}",
            "company": f"Garage {i % 20000}",
            "city": f"Ville {i % 5000}",
            "role": "vente automobile occasion",
        }

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rows", type=int, default=100_000)
    p.add_argument("--batch-size", type=int, default=10_000)
    args = p.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        raw_path, _ = init_databases(data_dir)
        start = time.perf_counter()
        result = ingest_iterable(
            raw_path,
            generated_rows(args.rows),
            source_key="benchmark",
            source_name="Synthetic benchmark",
            source_kind="benchmark",
            import_format="generated",
            batch_size=args.batch_size,
            id_field="external_id",
            hash_payload=False,
        )
        elapsed = time.perf_counter() - start
        rate = result["rows"] / elapsed if elapsed else 0
        print(f"rows={result['rows']} elapsed={elapsed:.3f}s rows_per_second={rate:,.0f}")

if __name__ == "__main__":
    main()
