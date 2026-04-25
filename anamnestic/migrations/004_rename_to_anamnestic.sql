-- Migration 004: rename ext_* tables → anamnestic_*.
-- Agent-neutral renaming after the project dropped Claude-centric naming.
--
-- Note: ext_migrations is renamed earlier by the bootstrap in anamnestic.db,
-- so the migration runner can read the applied-migrations list from the
-- correct table name before this migration executes. Only the remaining
-- three ext_* tables are handled here.

ALTER TABLE ext_ingest_state   RENAME TO anamnestic_ingest_state;
ALTER TABLE ext_embed_state    RENAME TO anamnestic_embed_state;
ALTER TABLE ext_audit          RENAME TO anamnestic_audit;

-- Rebuild indexes with new names (they auto-track renamed tables, but keeping
-- their own names in sync with the table names makes introspection sane).

DROP INDEX IF EXISTS idx_ext_ingest_state_mtime;
DROP INDEX IF EXISTS idx_ext_embed_state_coll;
DROP INDEX IF EXISTS idx_ext_audit_at;
DROP INDEX IF EXISTS idx_ext_audit_action;

CREATE INDEX IF NOT EXISTS idx_anamnestic_ingest_state_mtime
    ON anamnestic_ingest_state(mtime_ns);

CREATE INDEX IF NOT EXISTS idx_anamnestic_embed_state_coll
    ON anamnestic_embed_state(collection);

CREATE INDEX IF NOT EXISTS idx_anamnestic_audit_at
    ON anamnestic_audit(at DESC);

CREATE INDEX IF NOT EXISTS idx_anamnestic_audit_action
    ON anamnestic_audit(action, at DESC);
