"""Shared SQLite access + migration runner."""
import functools
import sqlite3
import time

from anamnesis.config import DB_PATH, MIGRATIONS_DIR

# SQLite busy timeout: how long a single call will wait for a competing writer
# before returning SQLITE_BUSY. 30s comfortably covers claude-mem's AI writes.
BUSY_TIMEOUT_MS = 30_000

# Explicit retry wrapper for write operations. `connect()` already sets a busy
# timeout, so most contention is absorbed there; this is an extra safety net
# for bursts during which claude-mem holds the write lock for >30s.
RETRY_ATTEMPTS = 5
RETRY_BASE_DELAY_SEC = 0.25


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_MS / 1000.0)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def retry_on_busy(fn):
    """Decorator: retry an operation on SQLITE_BUSY/LOCKED with jittered backoff."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        import random
        last = None
        for attempt in range(RETRY_ATTEMPTS):
            try:
                return fn(*args, **kwargs)
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "database is locked" not in msg and "busy" not in msg:
                    raise
                last = e
                delay = RETRY_BASE_DELAY_SEC * (2 ** attempt) * (0.8 + 0.4 * random.random())
                time.sleep(delay)
        raise last  # type: ignore[misc]
    return wrapper


def ensure_migrations_table(cur):
    # Bootstrap: if a DB from the pre-rename era has `ext_migrations`, carry
    # its records over by renaming the table. Migration 004 renames the
    # remaining ext_* tables; this inline step is needed before it can read
    # the applied-migrations list.
    old = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ext_migrations'"
    ).fetchone()
    new = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='anamnesis_migrations'"
    ).fetchone()
    if old and not new:
        cur.execute("ALTER TABLE ext_migrations RENAME TO anamnesis_migrations")
    elif not new:
        cur.execute(
            """
            CREATE TABLE anamnesis_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )


def _table_exists(cur, name: str) -> bool:
    row = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _should_skip_migration(cur, name: str) -> bool:
    if name != "004_rename_to_anamnesis.sql":
        return False

    legacy_tables = ("ext_ingest_state", "ext_embed_state", "ext_audit")
    renamed_tables = (
        "anamnesis_ingest_state",
        "anamnesis_embed_state",
        "anamnesis_audit",
    )
    has_legacy = any(_table_exists(cur, table) for table in legacy_tables)
    has_renamed = all(_table_exists(cur, table) for table in renamed_tables)
    return not has_legacy and has_renamed


def run_migrations():
    conn = connect()
    cur = conn.cursor()
    ensure_migrations_table(cur)
    applied = {r[0] for r in cur.execute("SELECT name FROM anamnesis_migrations").fetchall()}
    to_apply = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.name not in applied)
    for path in to_apply:
        if _should_skip_migration(cur, path.name):
            cur.execute("INSERT INTO anamnesis_migrations(name) VALUES (?)", (path.name,))
            conn.commit()
            continue
        sql = path.read_text()
        print(f"Applying {path.name}...")
        cur.executescript(sql)
        cur.execute("INSERT INTO anamnesis_migrations(name) VALUES (?)", (path.name,))
        conn.commit()
    conn.close()
    return [p.name for p in to_apply]


if __name__ == "__main__":
    applied = run_migrations()
    if applied:
        print(f"Applied: {', '.join(applied)}")
    else:
        print("All migrations already applied.")
