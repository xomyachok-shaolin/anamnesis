-- Migration 013: repair missing columns from partial 010 application.
-- ALTER TABLE ADD COLUMN is a no-op in SQLite if column already exists (raises error),
-- so we guard each with a check in the application layer (_should_skip_migration).
-- This migration only runs if columns are missing despite 010 being recorded.

ALTER TABLE session_summaries ADD COLUMN summary_text TEXT;
ALTER TABLE session_summaries ADD COLUMN content_session_id TEXT;

CREATE TABLE IF NOT EXISTS anamnestic_summary_state (
    content_session_id TEXT PRIMARY KEY,
    summarized_at TEXT NOT NULL DEFAULT (datetime('now'))
);
