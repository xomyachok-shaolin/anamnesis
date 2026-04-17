-- Migration 011: archive table for consolidated old turns.
CREATE TABLE IF NOT EXISTS anamnesis_archived_turns (
    id INTEGER PRIMARY KEY,
    content_session_id TEXT NOT NULL,
    turn_number INTEGER,
    role TEXT,
    text TEXT,
    timestamp TEXT,
    platform_source TEXT,
    importance REAL,
    archived_at TEXT NOT NULL DEFAULT (datetime('now')),
    archive_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_archived_turns_timestamp
    ON anamnesis_archived_turns(timestamp);

CREATE INDEX IF NOT EXISTS idx_archived_turns_session
    ON anamnesis_archived_turns(content_session_id);
