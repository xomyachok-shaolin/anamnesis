-- Migration 009: add importance score to historical turns for ranking.
ALTER TABLE historical_turns ADD COLUMN importance REAL DEFAULT 0.5;
CREATE INDEX IF NOT EXISTS idx_ht_importance ON historical_turns(importance);
