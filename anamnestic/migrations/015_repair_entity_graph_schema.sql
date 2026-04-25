-- Migration 015: guarantee full entity graph schema on databases where
-- migration 012 was marked applied while its tables were absent.

CREATE TABLE IF NOT EXISTS anamnestic_entity_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_a TEXT NOT NULL,
    entity_b TEXT NOT NULL,
    weight INTEGER NOT NULL DEFAULT 1,
    sessions TEXT,
    UNIQUE(entity_a, entity_b)
);

CREATE INDEX IF NOT EXISTS idx_entity_edges_a ON anamnestic_entity_edges(entity_a);
CREATE INDEX IF NOT EXISTS idx_entity_edges_b ON anamnestic_entity_edges(entity_b);

CREATE TABLE IF NOT EXISTS anamnestic_graph_state (
    content_session_id TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
