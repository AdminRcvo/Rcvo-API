PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
PRAGMA wal_autocheckpoint=10000;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1')
ON CONFLICT(key) DO UPDATE SET value=excluded.value;

CREATE TABLE IF NOT EXISTS raw_sources (
    id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    source_name TEXT,
    source_kind TEXT NOT NULL DEFAULT 'generic',
    provider_url TEXT,
    acquisition_mode TEXT,
    terms_snapshot TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS raw_batches (
    id INTEGER PRIMARY KEY,
    batch_uuid TEXT NOT NULL UNIQUE,
    source_id INTEGER NOT NULL REFERENCES raw_sources(id),
    batch_label TEXT,
    original_filename TEXT,
    import_format TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK(status IN ('open','complete','partial','failed','cancelled')),
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    completed_at TEXT,
    rows_received INTEGER NOT NULL DEFAULT 0,
    rows_persisted INTEGER NOT NULL DEFAULT 0,
    cursor_json TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS raw_records (
    id INTEGER PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES raw_batches(id),
    source_record_id TEXT,
    source_row_number INTEGER,
    captured_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT,
    processing_state TEXT NOT NULL DEFAULT 'pending'
        CHECK(processing_state IN ('pending','claimed','processed','rejected','error')),
    processed_at TEXT,
    processing_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_raw_records_pending
    ON raw_records(processing_state, id);
CREATE INDEX IF NOT EXISTS idx_raw_records_batch
    ON raw_records(batch_id, id);
CREATE INDEX IF NOT EXISTS idx_raw_batches_source
    ON raw_batches(source_id, started_at);

CREATE TABLE IF NOT EXISTS raw_batch_events (
    id INTEGER PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES raw_batches(id),
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    details_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_raw_batch_events_batch
    ON raw_batch_events(batch_id, occurred_at);
