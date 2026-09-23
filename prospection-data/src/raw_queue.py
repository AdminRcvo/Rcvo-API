from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ingest_runtime import connect_fast

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def claim_records(
    raw_path: Path,
    worker_id: str,
    *,
    limit: int = 1000,
    lease_seconds: int = 300,
) -> list[dict[str, Any]]:
    if limit < 1:
        return []
    now_dt = datetime.now(timezone.utc)
    now = _iso(now_dt)
    lease_until = _iso(now_dt + timedelta(seconds=lease_seconds))
    conn = connect_fast(raw_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT r.id, r.batch_id, b.batch_uuid AS raw_batch_uuid,
                   s.source_key, r.source_record_id, r.source_row_number,
                   r.captured_at, r.payload_json
            FROM raw_records r
            JOIN raw_batches b ON b.id=r.batch_id
            JOIN raw_sources s ON s.id=b.source_id
            LEFT JOIN raw_record_leases l ON l.raw_record_id=r.id
            WHERE r.processing_state IN ('pending','claimed')
              AND (l.raw_record_id IS NULL OR l.lease_until <= ?)
            ORDER BY r.id
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()
        ids = [int(r["id"]) for r in rows]
        for record_id in ids:
            conn.execute(
                """
                INSERT INTO raw_record_attempts(raw_record_id,attempts,last_worker_id,updated_at)
                VALUES (?,1,?,?)
                ON CONFLICT(raw_record_id) DO UPDATE SET
                    attempts=raw_record_attempts.attempts+1,
                    last_worker_id=excluded.last_worker_id,
                    updated_at=excluded.updated_at
                """,
                (record_id, worker_id, now),
            )
            conn.execute(
                """
                INSERT INTO raw_record_leases(raw_record_id,worker_id,claimed_at,lease_until)
                VALUES (?,?,?,?)
                ON CONFLICT(raw_record_id) DO UPDATE SET
                    worker_id=excluded.worker_id,
                    claimed_at=excluded.claimed_at,
                    lease_until=excluded.lease_until
                """,
                (record_id, worker_id, now, lease_until),
            )
            conn.execute(
                "UPDATE raw_records SET processing_state='claimed' WHERE id=?",
                (record_id,),
            )
        conn.commit()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def _assert_owner(conn: sqlite3.Connection, record_id: int, worker_id: str) -> None:
    row = conn.execute(
        "SELECT worker_id FROM raw_record_leases WHERE raw_record_id=?",
        (record_id,),
    ).fetchone()
    if not row or row[0] != worker_id:
        raise RuntimeError("record lease is not owned by this worker")

def extend_lease(
    raw_path: Path,
    record_id: int,
    worker_id: str,
    *,
    lease_seconds: int = 300,
) -> None:
    until = _iso(datetime.now(timezone.utc) + timedelta(seconds=lease_seconds))
    with connect_fast(raw_path) as conn:
        _assert_owner(conn, record_id, worker_id)
        conn.execute(
            "UPDATE raw_record_leases SET lease_until=? WHERE raw_record_id=?",
            (until, record_id),
        )

def release_record(raw_path: Path, record_id: int, worker_id: str) -> None:
    with connect_fast(raw_path) as conn:
        _assert_owner(conn, record_id, worker_id)
        conn.execute(
            "UPDATE raw_records SET processing_state='pending' WHERE id=?",
            (record_id,),
        )
        conn.execute(
            """
            UPDATE raw_record_attempts
            SET attempts=CASE WHEN attempts>0 THEN attempts-1 ELSE 0 END,
                updated_at=?
            WHERE raw_record_id=?
            """,
            (_iso(datetime.now(timezone.utc)), record_id),
        )
        conn.execute("DELETE FROM raw_record_leases WHERE raw_record_id=?", (record_id,))

def ack_record(raw_path: Path, record_id: int, worker_id: str) -> None:
    now = _iso(datetime.now(timezone.utc))
    with connect_fast(raw_path) as conn:
        _assert_owner(conn, record_id, worker_id)
        conn.execute(
            """
            UPDATE raw_records
            SET processing_state='processed', processed_at=?, processing_error=NULL
            WHERE id=?
            """,
            (now, record_id),
        )
        conn.execute("DELETE FROM raw_record_leases WHERE raw_record_id=?", (record_id,))

def fail_record(
    raw_path: Path,
    record_id: int,
    worker_id: str,
    error: str,
    *,
    max_attempts: int = 5,
) -> str:
    now = _iso(datetime.now(timezone.utc))
    with connect_fast(raw_path) as conn:
        _assert_owner(conn, record_id, worker_id)
        attempts = conn.execute(
            "SELECT attempts FROM raw_record_attempts WHERE raw_record_id=?",
            (record_id,),
        ).fetchone()
        count = int(attempts[0]) if attempts else 1
        state = "error" if count >= max_attempts else "pending"
        conn.execute(
            """
            UPDATE raw_records
            SET processing_state=?, processing_error=?, processed_at=?
            WHERE id=?
            """,
            (state, error[:4000], now if state == "error" else None, record_id),
        )
        conn.execute(
            """
            UPDATE raw_record_attempts
            SET last_error=?, updated_at=?
            WHERE raw_record_id=?
            """,
            (error[:4000], now, record_id),
        )
        conn.execute("DELETE FROM raw_record_leases WHERE raw_record_id=?", (record_id,))
        return state

def reject_record(raw_path: Path, record_id: int, worker_id: str, reason: str) -> None:
    now = _iso(datetime.now(timezone.utc))
    with connect_fast(raw_path) as conn:
        _assert_owner(conn, record_id, worker_id)
        conn.execute(
            """
            UPDATE raw_records
            SET processing_state='rejected', processing_error=?, processed_at=?
            WHERE id=?
            """,
            (reason[:4000], now, record_id),
        )
        conn.execute("DELETE FROM raw_record_leases WHERE raw_record_id=?", (record_id,))

def queue_stats(raw_path: Path) -> dict[str, int]:
    with connect_fast(raw_path) as conn:
        return {
            state: int(count)
            for state, count in conn.execute(
                "SELECT processing_state,count(*) FROM raw_records GROUP BY processing_state"
            )
        }
