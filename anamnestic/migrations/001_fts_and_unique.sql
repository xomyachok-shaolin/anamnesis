-- Migration 001: add UNIQUE constraint + FTS5 index on historical_turns.
-- Idempotent: safe to re-run.

-- 1. UNIQUE index (serves both as constraint and lookup).
CREATE UNIQUE INDEX IF NOT EXISTS ux_historical_turns_session_turn
    ON historical_turns(content_session_id, turn_number);

-- 2. FTS5 virtual table mirroring historical_turns.text with unicode61 tokenizer.
CREATE VIRTUAL TABLE IF NOT EXISTS historical_turns_fts USING fts5(
    text,
    content='historical_turns',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- 3. Triggers to keep FTS in sync with base table.
CREATE TRIGGER IF NOT EXISTS historical_turns_ai AFTER INSERT ON historical_turns BEGIN
    INSERT INTO historical_turns_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS historical_turns_ad AFTER DELETE ON historical_turns BEGIN
    INSERT INTO historical_turns_fts(historical_turns_fts, rowid, text)
    VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS historical_turns_au AFTER UPDATE ON historical_turns BEGIN
    INSERT INTO historical_turns_fts(historical_turns_fts, rowid, text)
    VALUES ('delete', old.id, old.text);
    INSERT INTO historical_turns_fts(rowid, text) VALUES (new.id, new.text);
END;

-- 4. Populate FTS with existing rows (no-op if already populated).
INSERT INTO historical_turns_fts(historical_turns_fts) VALUES('rebuild');
