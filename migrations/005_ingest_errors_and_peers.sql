-- Migration 005: capture per-file ingest errors and add cross-sync state.

-- Per-file ingest errors. Replaces the silent counter-only tracking.
CREATE TABLE IF NOT EXISTS anamnesis_ingest_errors (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    at            TEXT    NOT NULL DEFAULT (datetime('now')),
    source        TEXT    NOT NULL,
    path          TEXT    NOT NULL,
    error_class   TEXT    NOT NULL,
    error_message TEXT    NOT NULL,
    resolved_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingest_errors_at        ON anamnesis_ingest_errors(at DESC);
CREATE INDEX IF NOT EXISTS idx_ingest_errors_source    ON anamnesis_ingest_errors(source);
CREATE INDEX IF NOT EXISTS idx_ingest_errors_unresolved
    ON anamnesis_ingest_errors(path)
    WHERE resolved_at IS NULL;

-- Cross-host sync state — per-peer last-success / last-failure metadata.
CREATE TABLE IF NOT EXISTS anamnesis_peer_state (
    peer            TEXT PRIMARY KEY,
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_error      TEXT,
    duration_sec    REAL,
    files_pulled    INTEGER DEFAULT 0,
    files_pushed    INTEGER DEFAULT 0
);
