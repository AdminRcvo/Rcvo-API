#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Any

ROOT = Path(__file__).resolve().parents[1]
RAW_SCHEMA = ROOT / "schema" / "raw.sql"
REFERENCE_SCHEMA = ROOT / "schema" / "reference.sql"

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA wal_autocheckpoint=10000")
    return conn

def apply_schema(db_path: Path, schema_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(schema_path.read_text(encoding="utf-8"))

def init_databases(data_dir: Path) -> tuple[Path, Path]:
    data_dir.mkdir(parents=True, exist_ok=True)
    raw_path = data_dir / "raw.sqlite"
    reference_path = data_dir / "reference.sqlite"
    apply_schema(raw_path, RAW_SCHEMA)
    apply_schema(reference_path, REFERENCE_SCHEMA)
    return raw_path, reference_path

def ensure_source(
    conn: sqlite3.Connection,
    source_key: str,
    source_name: str | None = None,
    source_kind: str = "generic",
    provider_url: str | None = None,
    acquisition_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    conn.execute(
        """
        INSERT INTO raw_sources(
            source_key, source_name, source_kind, provider_url,
            acquisition_mode, metadata_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_key) DO UPDATE SET
            source_name=COALESCE(excluded.source_name, raw_sources.source_name),
            source_kind=excluded.source_kind,
            provider_url=COALESCE(excluded.provider_url, raw_sources.provider_url),
            acquisition_mode=COALESCE(excluded.acquisition_mode, raw_sources.acquisition_mode),
            metadata_json=COALESCE(excluded.metadata_json, raw_sources.metadata_json),
            updated_at=excluded.updated_at
        """,
        (
            source_key,
            source_name,
            source_kind,
            provider_url,
            acquisition_mode,
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) if metadata else None,
            utc_now(),
        ),
    )
    return int(conn.execute("SELECT id FROM raw_sources WHERE source_key=?", (source_key,)).fetchone()[0])

def create_batch(
    conn: sqlite3.Connection,
    source_id: int,
    import_format: str,
    batch_label: str | None,
    original_filename: str | None,
    metadata: dict[str, Any] | None,
) -> tuple[int, str]:
    batch_uuid = str(uuid.uuid4())
    cur = conn.execute(
        """
        INSERT INTO raw_batches(
            batch_uuid, source_id, batch_label, original_filename,
            import_format, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            batch_uuid,
            source_id,
            batch_label,
            original_filename,
            import_format,
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) if metadata else None,
        ),
    )
    conn.execute(
        "INSERT INTO raw_batch_events(batch_id,event_type,details_json) VALUES (?, 'started', NULL)",
        (cur.lastrowid,),
    )
    return int(cur.lastrowid), batch_uuid

def payload_tuple(
    batch_id: int,
    row_number: int,
    payload: Any,
    captured_at: str,
    id_field: str | None,
    hash_payload: bool,
) -> tuple[Any, ...]:
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    source_record_id = None
    if id_field and isinstance(payload, dict):
        value = payload.get(id_field)
        if value is not None and str(value).strip():
            source_record_id = str(value).strip()
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest() if hash_payload else None
    return (
        batch_id,
        source_record_id,
        row_number,
        captured_at,
        payload_json,
        payload_hash,
    )

def ingest_iterable(
    raw_path: Path,
    rows: Iterable[Any],
    *,
    source_key: str,
    import_format: str,
    source_name: str | None = None,
    source_kind: str = "generic",
    provider_url: str | None = None,
    acquisition_mode: str | None = None,
    original_filename: str | None = None,
    batch_label: str | None = None,
    batch_size: int = 10_000,
    id_field: str | None = None,
    hash_payload: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    conn = connect(raw_path)
    batch_id = None
    batch_uuid = None
    persisted = 0
    try:
        with conn:
            source_id = ensure_source(
                conn,
                source_key=source_key,
                source_name=source_name,
                source_kind=source_kind,
                provider_url=provider_url,
                acquisition_mode=acquisition_mode,
                metadata=metadata,
            )
            batch_id, batch_uuid = create_batch(
                conn,
                source_id=source_id,
                import_format=import_format,
                batch_label=batch_label,
                original_filename=original_filename,
                metadata=metadata,
            )

        captured_at = utc_now()
        pending: list[tuple[Any, ...]] = []
        for row_number, payload in enumerate(rows, start=1):
            pending.append(
                payload_tuple(
                    batch_id,
                    row_number,
                    payload,
                    captured_at,
                    id_field,
                    hash_payload,
                )
            )
            if len(pending) >= batch_size:
                with conn:
                    conn.executemany(
                        """
                        INSERT INTO raw_records(
                            batch_id, source_record_id, source_row_number,
                            captured_at, payload_json, payload_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        pending,
                    )
                persisted += len(pending)
                pending.clear()

        if pending:
            with conn:
                conn.executemany(
                    """
                    INSERT INTO raw_records(
                        batch_id, source_record_id, source_row_number,
                        captured_at, payload_json, payload_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    pending,
                )
            persisted += len(pending)

        with conn:
            conn.execute(
                """
                UPDATE raw_batches
                SET status='complete',
                    completed_at=?,
                    rows_received=?,
                    rows_persisted=?
                WHERE id=?
                """,
                (utc_now(), persisted, persisted, batch_id),
            )
            conn.execute(
                """
                INSERT INTO raw_batch_events(batch_id,event_type,details_json)
                VALUES (?, 'completed', ?)
                """,
                (batch_id, json.dumps({"rows": persisted}, separators=(",", ":"))),
            )

        return {"batch_id": batch_id, "batch_uuid": batch_uuid, "rows": persisted}
    except Exception as exc:
        if batch_id is not None:
            try:
                with conn:
                    conn.execute(
                        """
                        UPDATE raw_batches
                        SET status=?, completed_at=?, rows_received=?, rows_persisted=?
                        WHERE id=?
                        """,
                        ("partial" if persisted else "failed", utc_now(), persisted, persisted, batch_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO raw_batch_events(batch_id,event_type,details_json)
                        VALUES (?, 'failed', ?)
                        """,
                        (batch_id, json.dumps({"error": str(exc)}, ensure_ascii=False, separators=(",", ":"))),
                    )
            except Exception:
                pass
        raise
    finally:
        conn.close()

def iter_csv(path: Path, delimiter: str | None = None) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        if delimiter is None:
            sample = fh.read(32_768)
            fh.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                reader = csv.DictReader(fh, dialect=dialect)
            except csv.Error:
                reader = csv.DictReader(fh, delimiter=",")
        else:
            reader = csv.DictReader(fh, delimiter=delimiter)
        for row in reader:
            yield dict(row)

def iter_jsonl(path: Path) -> Iterator[Any]:
    with path.open("r", encoding="utf-8-sig") as fh:
        for line_number, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc

def parse_metadata(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("--metadata must be a JSON object")
    return parsed

def print_stats(data_dir: Path) -> None:
    raw_path = data_dir / "raw.sqlite"
    reference_path = data_dir / "reference.sqlite"
    if not raw_path.exists() or not reference_path.exists():
        raise SystemExit("Databases not initialized. Run: init")

    with connect(raw_path) as conn:
        total_raw = conn.execute("SELECT count(*) FROM raw_records").fetchone()[0]
        pending = conn.execute(
            "SELECT count(*) FROM raw_records WHERE processing_state='pending'"
        ).fetchone()[0]
        batches = conn.execute("SELECT count(*) FROM raw_batches").fetchone()[0]
        sources = conn.execute("SELECT count(*) FROM raw_sources").fetchone()[0]

    with connect(reference_path) as conn:
        contacts = conn.execute("SELECT count(*) FROM contacts").fetchone()[0]
        organizations = conn.execute("SELECT count(*) FROM organizations").fetchone()[0]
        emails = conn.execute("SELECT count(*) FROM contact_emails").fetchone()[0]
        events = conn.execute("SELECT count(*) FROM prospecting_events").fetchone()[0]

    print(
        json.dumps(
            {
                "raw": {
                    "sources": sources,
                    "batches": batches,
                    "records": total_raw,
                    "pending": pending,
                },
                "reference": {
                    "organizations": organizations,
                    "contacts": contacts,
                    "emails": emails,
                    "prospecting_events": events,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rcvo-data")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=ROOT / "data",
        help="Directory containing raw.sqlite and reference.sqlite",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create/upgrade both SQLite databases")
    sub.add_parser("stats", help="Display raw/reference counters")

    def add_ingest_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--source", required=True, help="Stable source key, e.g. apollo-2026-10")
        p.add_argument("--source-name")
        p.add_argument("--source-kind", default="generic")
        p.add_argument("--provider-url")
        p.add_argument("--acquisition-mode")
        p.add_argument("--file", type=Path, required=True)
        p.add_argument("--batch-label")
        p.add_argument("--batch-size", type=int, default=10_000)
        p.add_argument("--id-field")
        p.add_argument("--hash", action="store_true", dest="hash_payload")
        p.add_argument("--metadata", help="JSON object stored with the source/batch")

    csv_p = sub.add_parser("ingest-csv", help="Rapidly ingest an arbitrary CSV into RAW")
    add_ingest_args(csv_p)
    csv_p.add_argument("--delimiter", help="CSV delimiter; auto-detected by default")

    jsonl_p = sub.add_parser("ingest-jsonl", help="Rapidly ingest JSONL into RAW")
    add_ingest_args(jsonl_p)

    return parser

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "init":
        raw_path, ref_path = init_databases(args.data_dir)
        print(json.dumps({"raw": str(raw_path), "reference": str(ref_path)}, indent=2))
        return 0

    if args.command == "stats":
        print_stats(args.data_dir)
        return 0

    raw_path, _ = init_databases(args.data_dir)
    metadata = parse_metadata(args.metadata)

    if args.command == "ingest-csv":
        rows = iter_csv(args.file, delimiter=args.delimiter)
        result = ingest_iterable(
            raw_path,
            rows,
            source_key=args.source,
            import_format="csv",
            source_name=args.source_name,
            source_kind=args.source_kind,
            provider_url=args.provider_url,
            acquisition_mode=args.acquisition_mode,
            original_filename=args.file.name,
            batch_label=args.batch_label,
            batch_size=args.batch_size,
            id_field=args.id_field,
            hash_payload=args.hash_payload,
            metadata=metadata,
        )
    elif args.command == "ingest-jsonl":
        result = ingest_iterable(
            raw_path,
            iter_jsonl(args.file),
            source_key=args.source,
            import_format="jsonl",
            source_name=args.source_name,
            source_kind=args.source_kind,
            provider_url=args.provider_url,
            acquisition_mode=args.acquisition_mode,
            original_filename=args.file.name,
            batch_label=args.batch_label,
            batch_size=args.batch_size,
            id_field=args.id_field,
            hash_payload=args.hash_payload,
            metadata=metadata,
        )
    else:
        raise AssertionError(args.command)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
