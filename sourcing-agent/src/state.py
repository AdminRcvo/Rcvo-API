from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA="""
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS agent_meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

INSERT INTO agent_meta(key,value) VALUES ('schema_version','1')
ON CONFLICT(key) DO UPDATE SET value=excluded.value;

CREATE TABLE IF NOT EXISTS sourcing_runs(
    id INTEGER PRIMARY KEY,
    run_uuid TEXT NOT NULL UNIQUE,
    worker_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('simulation','production')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('running','complete','partial','failed')),
    claimed INTEGER NOT NULL DEFAULT 0,
    promoted INTEGER NOT NULL DEFAULT 0,
    enriched INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    details_json TEXT
);

CREATE TABLE IF NOT EXISTS sourcing_decisions(
    id INTEGER PRIMARY KEY,
    run_uuid TEXT NOT NULL,
    raw_record_id INTEGER NOT NULL,
    source_key TEXT,
    decision TEXT NOT NULL,
    contact_rcvo_id TEXT,
    organization_rcvo_id TEXT,
    qualification_status TEXT,
    vo_relevance TEXT,
    match_methods_json TEXT,
    reasons_json TEXT,
    occurred_at TEXT NOT NULL,
    details_json TEXT,
    UNIQUE(run_uuid,raw_record_id)
);

CREATE INDEX IF NOT EXISTS idx_sourcing_decisions_raw
    ON sourcing_decisions(raw_record_id,occurred_at);
CREATE INDEX IF NOT EXISTS idx_sourcing_decisions_contact
    ON sourcing_decisions(contact_rcvo_id,occurred_at);

CREATE TABLE IF NOT EXISTS sourcing_errors(
    id INTEGER PRIMARY KEY,
    run_uuid TEXT NOT NULL,
    raw_record_id INTEGER,
    error_class TEXT NOT NULL,
    error_message TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    details_json TEXT
);

CREATE TABLE IF NOT EXISTS source_profile_versions(
    id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL,
    profile_sha256 TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    activated_at TEXT NOT NULL,
    UNIQUE(source_key,profile_sha256)
);
"""

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")

class AgentState:
    def __init__(self,path:Path):
        self.path=path
        path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def connect(self):
        conn=sqlite3.connect(self.path,timeout=20)
        conn.row_factory=sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def start_run(self,worker_id:str,mode:str) -> str:
        run_uuid=str(uuid.uuid4())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sourcing_runs(run_uuid,worker_id,mode,started_at,status)
                VALUES (?,?,?,?, 'running')
                """,
                (run_uuid,worker_id,mode,now()),
            )
        return run_uuid

    def finish_run(self,run_uuid:str,status:str,counters:dict[str,int],details:Any=None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE sourcing_runs SET completed_at=?,status=?,claimed=?,promoted=?,
                    enriched=?,rejected=?,failed=?,details_json=?
                WHERE run_uuid=?
                """,
                (
                    now(),status,counters.get("claimed",0),counters.get("promoted",0),
                    counters.get("enriched",0),counters.get("rejected",0),
                    counters.get("failed",0),
                    json.dumps(details,ensure_ascii=False,separators=(",",":")) if details is not None else None,
                    run_uuid,
                ),
            )

    def decision(
        self,run_uuid:str,raw_record_id:int,decision:str,
        *,source_key:str|None=None,contact_rcvo_id:str|None=None,
        organization_rcvo_id:str|None=None,qualification_status:str|None=None,
        vo_relevance:str|None=None,match_methods:list[str]|None=None,
        reasons:list[str]|None=None,details:Any=None,
    ) -> None:
        self.decision_many([{
            "run_uuid":run_uuid,"raw_record_id":raw_record_id,"decision":decision,
            "source_key":source_key,"contact_rcvo_id":contact_rcvo_id,
            "organization_rcvo_id":organization_rcvo_id,
            "qualification_status":qualification_status,"vo_relevance":vo_relevance,
            "match_methods":match_methods or [],"reasons":reasons or [],
            "details":details,
        }])

    def decision_many(self,entries:list[dict[str,Any]]) -> None:
        if not entries:
            return
        occurred=now()
        rows=[]
        for e in entries:
            rows.append((
                e["run_uuid"],e["raw_record_id"],e.get("source_key"),e["decision"],
                e.get("contact_rcvo_id"),e.get("organization_rcvo_id"),
                e.get("qualification_status"),e.get("vo_relevance"),
                json.dumps(e.get("match_methods") or [],ensure_ascii=False,separators=(",",":")),
                json.dumps(e.get("reasons") or [],ensure_ascii=False,separators=(",",":")),
                occurred,
                json.dumps(e.get("details"),ensure_ascii=False,separators=(",",":"),default=str)
                    if e.get("details") is not None else None,
            ))
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO sourcing_decisions(
                    run_uuid,raw_record_id,source_key,decision,contact_rcvo_id,
                    organization_rcvo_id,qualification_status,vo_relevance,
                    match_methods_json,reasons_json,occurred_at,details_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )

    def error(self,run_uuid:str,error:Exception|str,raw_record_id:int|None=None,details:Any=None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sourcing_errors(
                    run_uuid,raw_record_id,error_class,error_message,occurred_at,details_json
                ) VALUES (?,?,?,?,?,?)
                """,
                (
                    run_uuid,raw_record_id,
                    error.__class__.__name__ if isinstance(error,Exception) else "Error",
                    str(error)[:4000],now(),
                    json.dumps(details,ensure_ascii=False,separators=(",",":"),default=str) if details is not None else None,
                ),
            )

    def register_profile(self,source_key:str,profile_sha256:str,profile:dict[str,Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO source_profile_versions(
                    source_key,profile_sha256,profile_json,activated_at
                ) VALUES (?,?,?,?)
                """,
                (
                    source_key,profile_sha256,
                    json.dumps(profile,ensure_ascii=False,separators=(",",":"),sort_keys=True),
                    now(),
                ),
            )

    def stats(self) -> dict[str,int]:
        with self.connect() as conn:
            return {
                "runs":int(conn.execute("SELECT count(*) FROM sourcing_runs").fetchone()[0]),
                "decisions":int(conn.execute("SELECT count(*) FROM sourcing_decisions").fetchone()[0]),
                "errors":int(conn.execute("SELECT count(*) FROM sourcing_errors").fetchone()[0]),
                "profile_versions":int(conn.execute("SELECT count(*) FROM source_profile_versions").fetchone()[0]),
            }
