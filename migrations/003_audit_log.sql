-- Migration 003: audit log of operational actions.
-- Records sync/backup/restore/verify invocations for later forensics.

CREATE TABLE IF NOT EXISTS anamnestic_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL DEFAULT (datetime('now')),
    action TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ok', 'error', 'warn')),
    duration_sec REAL,
    details TEXT  -- JSON payload with per-action fields
);

CREATE INDEX IF NOT EXISTS idx_ext_audit_at ON anamnestic_audit(at DESC);
CREATE INDEX IF NOT EXISTS idx_ext_audit_action ON anamnestic_audit(action, at DESC);
