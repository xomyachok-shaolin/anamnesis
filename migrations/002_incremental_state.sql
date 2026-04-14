-- Migration 002: support safe incremental ingest.
-- - UNIQUE indexes on derived tables so ON CONFLICT DO NOTHING works.
-- - anamnesis_ingest_state: track last processed file mtime per source
--   to skip unchanged jsonls on repeated runs.

CREATE UNIQUE INDEX IF NOT EXISTS ux_user_prompts_session_prompt
    ON user_prompts(content_session_id, prompt_number);

CREATE UNIQUE INDEX IF NOT EXISTS ux_session_summaries_session_prompt
    ON session_summaries(memory_session_id, prompt_number);

CREATE TABLE IF NOT EXISTS anamnesis_ingest_state (
    source TEXT NOT NULL,
    path TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    turns INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, path)
);

CREATE INDEX IF NOT EXISTS idx_ext_ingest_state_mtime
    ON anamnesis_ingest_state(mtime_ns);

-- Track which historical_turns have been embedded into Chroma.
CREATE TABLE IF NOT EXISTS anamnesis_embed_state (
    turn_id INTEGER PRIMARY KEY,
    collection TEXT NOT NULL,
    embedded_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (turn_id) REFERENCES historical_turns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ext_embed_state_coll
    ON anamnesis_embed_state(collection);
