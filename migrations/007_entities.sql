-- Migration 007: entity extraction sidecar.
--
-- Stores structured entities (file paths, URLs) extracted from turn text
-- via deterministic regexes. Enables scoped queries like "what did we do
-- with config.py?" that neither BM25 nor semantic search handle well.

CREATE TABLE IF NOT EXISTS anamnestic_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id INTEGER NOT NULL REFERENCES historical_turns(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,  -- 'path', 'url'
    value TEXT NOT NULL,
    UNIQUE(turn_id, entity_type, value)
);

CREATE INDEX IF NOT EXISTS idx_entities_value
    ON anamnestic_entities(value);
CREATE INDEX IF NOT EXISTS idx_entities_type_value
    ON anamnestic_entities(entity_type, value);
CREATE INDEX IF NOT EXISTS idx_entities_turn
    ON anamnestic_entities(turn_id);

-- Tracks which turns have been processed for entity extraction (idempotent).
CREATE TABLE IF NOT EXISTS anamnestic_entity_state (
    turn_id INTEGER PRIMARY KEY
);
