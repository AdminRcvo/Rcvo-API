from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from formats import ParsedRecord, iter_file_records
from rcvo_data import init_databases, utc_now

@dataclass
class Artifact:
    id: int
    path: Path
    sha256: str
    size_bytes: int
    already_ingested: bool

def connect_fast(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=60)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-131072")
    conn.execute("PRAGMA mmap_size=268435456")
    conn.execute("PRAGMA wal_autocheckpoint=20000")
    return conn

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
            source_key, source_name, source_kind, provider_url, acquisition_mode,
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) if metadata else None,
            utc_now(),
        ),
    )
    return int(conn.execute("SELECT id FROM raw_sources WHERE source_key=?", (source_key,)).fetchone()[0])

def _hash_and_copy(source: Path, artifact_root: Path) -> tuple[Path, str, int]:
    artifact_root.mkdir(parents=True, exist_ok=True)
    tmp = artifact_root / (".incoming-" + uuid.uuid4().hex)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as src, tmp.open("wb") as dst:
        while True:
            chunk = src.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            dst.write(chunk)
    sha = digest.hexdigest()
    suffix = "".join(source.suffixes[-2:]) if len(source.suffixes) >= 2 else source.suffix
    final_dir = artifact_root / "sha256" / sha[:2]
    final_dir.mkdir(parents=True, exist_ok=True)
    final = final_dir / (sha + suffix.lower())
    if final.exists():
        tmp.unlink(missing_ok=True)
    else:
        os.replace(tmp, final)
    return final, sha, size

def store_bytes(data: bytes, artifact_root: Path, suffix: str = "") -> tuple[Path, str, int]:
    sha = hashlib.sha256(data).hexdigest()
    final_dir = artifact_root / "sha256" / sha[:2]
    final_dir.mkdir(parents=True, exist_ok=True)
    final = final_dir / (sha + suffix.lower())
    if not final.exists():
        tmp = final_dir / (".incoming-" + uuid.uuid4().hex)
        tmp.write_bytes(data)
        os.replace(tmp, final)
    return final, sha, len(data)

def register_artifact(
    raw_path: Path,
    *,
    source_key: str,
    stored_path: Path,
    sha256: str,
    size_bytes: int,
    origin_uri: str | None,
    original_filename: str | None,
    source_name: str | None = None,
    source_kind: str = "generic",
    provider_url: str | None = None,
    acquisition_mode: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Artifact:
    media_type = mimetypes.guess_type(original_filename or stored_path.name)[0]
    with connect_fast(raw_path) as conn:
        source_id = ensure_source(
            conn, source_key, source_name, source_kind, provider_url, acquisition_mode, metadata
        )
        row = conn.execute(
            "SELECT id, stored_path FROM raw_artifacts WHERE source_id=? AND content_sha256=?",
            (source_id, sha256),
        ).fetchone()
        if row:
            artifact_id = int(row[0])
            existing_path = Path(row[1]) if row[1] else stored_path
        else:
            cur = conn.execute(
                """
                INSERT INTO raw_artifacts(
                    artifact_uuid, source_id, origin_uri, original_filename, stored_path,
                    media_type, size_bytes, content_sha256, etag, last_modified, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()), source_id, origin_uri, original_filename, str(stored_path),
                    media_type, size_bytes, sha256, etag, last_modified,
                    json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) if metadata else None,
                ),
            )
            artifact_id = int(cur.lastrowid)
            existing_path = stored_path
        done = conn.execute(
            """
            SELECT 1
            FROM raw_batch_artifacts ba
            JOIN raw_batches b ON b.id=ba.batch_id
            WHERE ba.artifact_id=? AND b.status='complete'
            LIMIT 1
            """,
            (artifact_id,),
        ).fetchone() is not None
    return Artifact(artifact_id, existing_path, sha256, size_bytes, done)

def _flush(
    conn: sqlite3.Connection,
    valid: list[tuple[Any, ...]],
    failures: list[tuple[Any, ...]],
) -> tuple[int, int]:
    inserted = 0
    failed = 0
    if valid:
        conn.executemany(
            """
            INSERT INTO raw_records(
                batch_id, source_record_id, source_row_number,
                captured_at, payload_json, payload_sha256
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            valid,
        )
        inserted = len(valid)
        valid.clear()
    if failures:
        conn.executemany(
            """
            INSERT INTO raw_ingest_failures(
                source_id, batch_id, artifact_id, location, raw_fragment,
                error_class, error_message, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            failures,
        )
        failed = len(failures)
        failures.clear()
    return inserted, failed

def ingest_parsed_records(
    raw_path: Path,
    records: Iterable[ParsedRecord],
    *,
    source_key: str,
    import_format: str,
    artifact_id: int | None = None,
    source_name: str | None = None,
    source_kind: str = "generic",
    provider_url: str | None = None,
    acquisition_mode: str | None = None,
    original_filename: str | None = None,
    batch_label: str | None = None,
    batch_size: int = 25000,
    id_field: str | None = None,
    hash_payload: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    conn = connect_fast(raw_path)
    started = time.perf_counter()
    batch_id = None
    batch_uuid = str(uuid.uuid4())
    persisted = 0
    rejected = 0
    received = 0
    try:
        with conn:
            source_id = ensure_source(
                conn, source_key, source_name, source_kind, provider_url, acquisition_mode, metadata
            )
            cur = conn.execute(
                """
                INSERT INTO raw_batches(
                    batch_uuid, source_id, batch_label, original_filename,
                    import_format, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_uuid, source_id, batch_label, original_filename, import_format,
                    json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) if metadata else None,
                ),
            )
            batch_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO raw_batch_events(batch_id,event_type) VALUES (?, 'started')",
                (batch_id,),
            )
            if artifact_id is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO raw_batch_artifacts(batch_id,artifact_id) VALUES (?,?)",
                    (batch_id, artifact_id),
                )

        valid: list[tuple[Any, ...]] = []
        failures: list[tuple[Any, ...]] = []
        captured_at = utc_now()
        for row_number, record in enumerate(records, start=1):
            received += 1
            if record.error is not None or record.payload is None:
                failures.append(
                    (
                        source_id, batch_id, artifact_id, record.location, record.raw_fragment,
                        "parse_error", record.error or "record payload is null", None,
                    )
                )
            else:
                payload_json = json.dumps(
                    record.payload, ensure_ascii=False, separators=(",", ":"), default=str
                )
                source_record_id = None
                if id_field and isinstance(record.payload, dict):
                    value = record.payload.get(id_field)
                    if value is not None and str(value).strip():
                        source_record_id = str(value).strip()
                payload_hash = (
                    hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
                    if hash_payload else None
                )
                valid.append(
                    (
                        batch_id, source_record_id, row_number, captured_at, payload_json, payload_hash
                    )
                )
            if len(valid) + len(failures) >= batch_size:
                with conn:
                    a, b = _flush(conn, valid, failures)
                persisted += a
                rejected += b

        if valid or failures:
            with conn:
                a, b = _flush(conn, valid, failures)
            persisted += a
            rejected += b

        elapsed = max(time.perf_counter() - started, 0.000001)
        with conn:
            conn.execute(
                """
                UPDATE raw_batches
                SET status='complete', completed_at=?, rows_received=?, rows_persisted=?
                WHERE id=?
                """,
                (utc_now(), received, persisted, batch_id),
            )
            conn.execute(
                """
                INSERT INTO raw_batch_events(batch_id,event_type,details_json)
                VALUES (?, 'completed', ?)
                """,
                (
                    batch_id,
                    json.dumps(
                        {"received": received, "persisted": persisted, "rejected": rejected},
                        separators=(",", ":"),
                    ),
                ),
            )
            conn.executemany(
                """
                INSERT INTO raw_ingest_metrics(source_id,batch_id,metric_name,metric_value,metric_unit)
                VALUES (?,?,?,?,?)
                """,
                [
                    (source_id, batch_id, "rows_received", received, "rows"),
                    (source_id, batch_id, "rows_persisted", persisted, "rows"),
                    (source_id, batch_id, "rows_rejected", rejected, "rows"),
                    (source_id, batch_id, "elapsed_seconds", elapsed, "seconds"),
                    (source_id, batch_id, "rows_per_second", persisted / elapsed, "rows/s"),
                ],
            )
        return {
            "batch_id": batch_id,
            "batch_uuid": batch_uuid,
            "received": received,
            "rows": persisted,
            "rejected": rejected,
            "elapsed_seconds": elapsed,
            "rows_per_second": persisted / elapsed,
        }
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
                        ("partial" if persisted else "failed", utc_now(), received, persisted, batch_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO raw_batch_events(batch_id,event_type,details_json)
                        VALUES (?, 'failed', ?)
                        """,
                        (batch_id, json.dumps({"error": str(exc)}, ensure_ascii=False)),
                    )
            except Exception:
                pass
        raise
    finally:
        conn.close()

def ingest_file(
    data_dir: Path,
    file_path: Path,
    *,
    source_key: str,
    source_name: str | None = None,
    source_kind: str = "provider_export",
    provider_url: str | None = None,
    acquisition_mode: str | None = "file",
    fmt: str = "auto",
    delimiter: str | None = None,
    records_path: str | None = None,
    xml_record_tag: str | None = None,
    sheet: str | None = None,
    batch_size: int = 25000,
    id_field: str | None = None,
    hash_payload: bool = False,
    force: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_path, _ = init_databases(data_dir)
    stored, sha, size = _hash_and_copy(file_path, data_dir / "artifacts")
    artifact = register_artifact(
        raw_path,
        source_key=source_key,
        stored_path=stored,
        sha256=sha,
        size_bytes=size,
        origin_uri=str(file_path.resolve()),
        original_filename=file_path.name,
        source_name=source_name,
        source_kind=source_kind,
        provider_url=provider_url,
        acquisition_mode=acquisition_mode,
        metadata=metadata,
    )
    if artifact.already_ingested and not force:
        return {
            "artifact_id": artifact.id,
            "artifact_sha256": artifact.sha256,
            "skipped": True,
            "reason": "identical artifact already completely ingested",
        }
    result = ingest_parsed_records(
        raw_path,
        iter_file_records(
            artifact.path, fmt, delimiter, records_path, xml_record_tag, sheet
        ),
        source_key=source_key,
        import_format=fmt,
        artifact_id=artifact.id,
        source_name=source_name,
        source_kind=source_kind,
        provider_url=provider_url,
        acquisition_mode=acquisition_mode,
        original_filename=file_path.name,
        batch_label=file_path.name,
        batch_size=batch_size,
        id_field=id_field,
        hash_payload=hash_payload,
        metadata=metadata,
    )
    result.update(
        {
            "artifact_id": artifact.id,
            "artifact_sha256": artifact.sha256,
            "stored_path": str(artifact.path),
            "skipped": False,
        }
    )
    return result

def ingest_directory(
    data_dir: Path,
    directory: Path,
    *,
    source_key: str,
    recursive: bool = True,
    force: bool = False,
    **kwargs,
) -> dict[str, Any]:
    from formats import is_supported_path
    iterator = directory.rglob("*") if recursive else directory.glob("*")
    files = sorted(p for p in iterator if p.is_file() and is_supported_path(p))
    results = []
    for path in files:
        results.append(
            ingest_file(
                data_dir,
                path,
                source_key=source_key,
                force=force,
                **kwargs,
            )
        )
    return {
        "files_seen": len(files),
        "files_ingested": sum(1 for r in results if not r.get("skipped")),
        "files_skipped": sum(1 for r in results if r.get("skipped")),
        "rows": sum(int(r.get("rows", 0)) for r in results),
        "rejected": sum(int(r.get("rejected", 0)) for r in results),
        "results": results,
    }

def ingest_bytes(
    data_dir: Path,
    data: bytes,
    *,
    source_key: str,
    original_filename: str,
    origin_uri: str | None = None,
    source_name: str | None = None,
    source_kind: str = "http_api",
    provider_url: str | None = None,
    acquisition_mode: str | None = "http",
    etag: str | None = None,
    last_modified: str | None = None,
    force: bool = False,
    **kwargs,
) -> dict[str, Any]:
    raw_path, _ = init_databases(data_dir)
    suffix = "".join(Path(original_filename).suffixes[-2:])
    stored, sha, size = store_bytes(data, data_dir / "artifacts", suffix=suffix)
    artifact = register_artifact(
        raw_path,
        source_key=source_key,
        stored_path=stored,
        sha256=sha,
        size_bytes=size,
        origin_uri=origin_uri,
        original_filename=original_filename,
        source_name=source_name,
        source_kind=source_kind,
        provider_url=provider_url,
        acquisition_mode=acquisition_mode,
        etag=etag,
        last_modified=last_modified,
        metadata=kwargs.get("metadata"),
    )
    if artifact.already_ingested and not force:
        return {
            "artifact_id": artifact.id,
            "artifact_sha256": artifact.sha256,
            "skipped": True,
            "reason": "identical artifact already completely ingested",
            "rows": 0,
            "rejected": 0,
        }
    result = ingest_parsed_records(
        raw_path,
        iter_file_records(
            artifact.path,
            kwargs.pop("fmt", "auto"),
            kwargs.pop("delimiter", None),
            kwargs.pop("records_path", None),
            kwargs.pop("xml_record_tag", None),
            kwargs.pop("sheet", None),
        ),
        source_key=source_key,
        artifact_id=artifact.id,
        original_filename=original_filename,
        source_name=source_name,
        source_kind=source_kind,
        provider_url=provider_url,
        acquisition_mode=acquisition_mode,
        import_format=kwargs.pop("import_format", "auto"),
        **kwargs,
    )
    result.update(
        {
            "artifact_id": artifact.id,
            "artifact_sha256": artifact.sha256,
            "stored_path": str(artifact.path),
            "skipped": False,
        }
    )
    return result
