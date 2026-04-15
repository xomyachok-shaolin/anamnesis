-- Migration 000: create the standalone base schema expected by anamnesis.
-- Safe on top of an existing claude-mem database because every object is IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS sdk_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_session_id TEXT UNIQUE NOT NULL,
    memory_session_id TEXT UNIQUE,
    project TEXT NOT NULL,
    platform_source TEXT NOT NULL DEFAULT 'claude',
    user_prompt TEXT,
    started_at TEXT NOT NULL,
    started_at_epoch INTEGER NOT NULL,
    completed_at TEXT,
    completed_at_epoch INTEGER,
    status TEXT CHECK(status IN ('active', 'completed', 'failed')) NOT NULL DEFAULT 'active',
    worker_port INTEGER,
    prompt_counter INTEGER DEFAULT 0,
    custom_title TEXT
);

CREATE TABLE IF NOT EXISTS user_prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_session_id TEXT NOT NULL,
    prompt_number INTEGER NOT NULL,
    prompt_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    FOREIGN KEY (content_session_id) REFERENCES sdk_sessions(content_session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS session_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_session_id TEXT NOT NULL,
    project TEXT NOT NULL,
    request TEXT,
    investigated TEXT,
    learned TEXT,
    completed TEXT,
    next_steps TEXT,
    files_read TEXT,
    files_edited TEXT,
    notes TEXT,
    prompt_number INTEGER,
    discovery_tokens INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS historical_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_session_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    timestamp TEXT,
    platform_source TEXT NOT NULL,
    FOREIGN KEY (content_session_id) REFERENCES sdk_sessions(content_session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_historical_turns_session
    ON historical_turns(content_session_id, turn_number);

CREATE INDEX IF NOT EXISTS idx_historical_turns_timestamp
    ON historical_turns(timestamp);
