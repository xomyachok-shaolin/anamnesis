-- Migration 012: entity co-occurrence graph for graph-based retrieval.

CREATE TABLE IF NOT EXISTS anamnesis_entity_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_a TEXT NOT NULL,
    entity_b TEXT NOT NULL,
    weight INTEGER NOT NULL DEFAULT 1,
    sessions TEXT,
    UNIQUE(entity_a, entity_b)
);

CREATE INDEX IF NOT EXISTS idx_entity_edges_a ON anamnesis_entity_edges(entity_a);
CREATE INDEX IF NOT EXISTS idx_entity_edges_b ON anamnesis_entity_edges(entity_b);

CREATE TABLE IF NOT EXISTS anamnesis_graph_state (
    content_session_id TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
