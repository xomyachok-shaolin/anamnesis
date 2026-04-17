-- Migration 010: add summary_text column and rebuild FTS index for session summaries.

-- Combined text column for FTS/Chroma indexing (safe: ALTER ADD is no-op if exists via prior run)
ALTER TABLE session_summaries ADD COLUMN summary_text TEXT;

-- Link to content_session_id for joins with historical_turns
ALTER TABLE session_summaries ADD COLUMN content_session_id TEXT;

-- Track which sessions have been extractively summarized
CREATE TABLE IF NOT EXISTS anamnesis_summary_state (
    content_session_id TEXT PRIMARY KEY,
    summarized_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Drop old FTS (may have different columns from claude-mem) and rebuild on summary_text
DROP TABLE IF EXISTS session_summaries_fts;

CREATE VIRTUAL TABLE IF NOT EXISTS session_summaries_fts USING fts5(
    summary_text,
    content='session_summaries',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- Drop old triggers if any, then recreate
DROP TRIGGER IF EXISTS session_summaries_ai;
DROP TRIGGER IF EXISTS session_summaries_ad;
DROP TRIGGER IF EXISTS session_summaries_au;

CREATE TRIGGER session_summaries_ai AFTER INSERT ON session_summaries BEGIN
    INSERT INTO session_summaries_fts(rowid, summary_text)
    VALUES (new.id, new.summary_text);
END;

CREATE TRIGGER session_summaries_ad AFTER DELETE ON session_summaries BEGIN
    INSERT INTO session_summaries_fts(session_summaries_fts, rowid, summary_text)
    VALUES ('delete', old.id, old.summary_text);
END;

CREATE TRIGGER session_summaries_au AFTER UPDATE OF summary_text ON session_summaries BEGIN
    INSERT INTO session_summaries_fts(session_summaries_fts, rowid, summary_text)
    VALUES ('delete', old.id, old.summary_text);
    INSERT INTO session_summaries_fts(rowid, summary_text)
    VALUES (new.id, new.summary_text);
END;
