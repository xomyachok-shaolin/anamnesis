-- Migration 008: session threading.
--
-- Groups sessions into threads: consecutive sessions in the same project
-- with temporal proximity form a thread (continuation chain).
-- Subagent sessions (csid contains ':') are excluded — they are already
-- linked to their parent by convention.

CREATE TABLE IF NOT EXISTS anamnestic_session_threads (
    session_id TEXT PRIMARY KEY,
    thread_id INTEGER NOT NULL,
    thread_order INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_threads_thread
    ON anamnestic_session_threads(thread_id, thread_order);
