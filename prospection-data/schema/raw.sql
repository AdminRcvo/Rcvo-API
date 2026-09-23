PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=10000;
PRAGMA wal_autocheckpoint=20000;
PRAGMA cache_size=-131072;
PRAGMA mmap_size=268435456;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

INSERT INTO schema_meta(key, value) VALUES ('schema_version', '2')
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

CREATE TABLE IF NOT EXISTS raw_connector_configs (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES raw_sources(id),
    connector_key TEXT NOT NULL,
    connector_kind TEXT NOT NULL,
    config_json TEXT NOT NULL,
    config_sha256 TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(source_id, connector_key)
);

CREATE TABLE IF NOT EXISTS raw_artifacts (
    id INTEGER PRIMARY KEY,
    artifact_uuid TEXT NOT NULL UNIQUE,
    source_id INTEGER NOT NULL REFERENCES raw_sources(id),
    origin_uri TEXT,
    original_filename TEXT,
    stored_path TEXT,
    media_type TEXT,
    compression TEXT,
    size_bytes INTEGER,
    content_sha256 TEXT NOT NULL,
    etag TEXT,
    last_modified TEXT,
    acquired_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    metadata_json TEXT,
    UNIQUE(source_id, content_sha256)
);
CREATE INDEX IF NOT EXISTS idx_raw_artifacts_source
    ON raw_artifacts(source_id, acquired_at);
CREATE INDEX IF NOT EXISTS idx_raw_artifacts_hash
    ON raw_artifacts(content_sha256);

CREATE TABLE IF NOT EXISTS raw_batch_artifacts (
    batch_id INTEGER NOT NULL REFERENCES raw_batches(id),
    artifact_id INTEGER NOT NULL REFERENCES raw_artifacts(id),
    PRIMARY KEY(batch_id, artifact_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS raw_fetch_runs (
    id INTEGER PRIMARY KEY,
    run_uuid TEXT NOT NULL UNIQUE,
    source_id INTEGER NOT NULL REFERENCES raw_sources(id),
    connector_key TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK(status IN ('running','complete','partial','failed','cancelled','not_modified')),
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    completed_at TEXT,
    requests_made INTEGER NOT NULL DEFAULT 0,
    artifacts_seen INTEGER NOT NULL DEFAULT 0,
    records_persisted INTEGER NOT NULL DEFAULT 0,
    http_status INTEGER,
    error_message TEXT,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_fetch_runs_source
    ON raw_fetch_runs(source_id, started_at);

CREATE TABLE IF NOT EXISTS raw_checkpoints (
    source_id INTEGER NOT NULL REFERENCES raw_sources(id),
    checkpoint_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY(source_id, checkpoint_key)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS raw_record_attempts (
    raw_record_id INTEGER PRIMARY KEY REFERENCES raw_records(id) ON DELETE CASCADE,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_worker_id TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS raw_record_leases (
    raw_record_id INTEGER PRIMARY KEY REFERENCES raw_records(id) ON DELETE CASCADE,
    worker_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    lease_until TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_record_leases_until
    ON raw_record_leases(lease_until);

CREATE TABLE IF NOT EXISTS raw_ingest_failures (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES raw_sources(id),
    batch_id INTEGER REFERENCES raw_batches(id),
    artifact_id INTEGER REFERENCES raw_artifacts(id),
    location TEXT,
    raw_fragment TEXT,
    error_class TEXT NOT NULL,
    error_message TEXT NOT NULL,
    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_ingest_failures_source
    ON raw_ingest_failures(source_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_raw_ingest_failures_batch
    ON raw_ingest_failures(batch_id, occurred_at);

CREATE TABLE IF NOT EXISTS raw_ingest_metrics (
    id INTEGER PRIMARY KEY,
    source_id INTEGER REFERENCES raw_sources(id),
    batch_id INTEGER REFERENCES raw_batches(id),
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    metric_unit TEXT,
    observed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_ingest_metrics_name
    ON raw_ingest_metrics(metric_name, observed_at);

CREATE TABLE IF NOT EXISTS raw_maintenance_events (
    id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    result TEXT,
    details_json TEXT
);
