-- Migration 014: repair databases where 012 was marked applied before
-- anamnestic_graph_state existed.

CREATE TABLE IF NOT EXISTS anamnestic_graph_state (
    content_session_id TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
