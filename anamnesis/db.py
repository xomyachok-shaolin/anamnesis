"""Shared SQLite access + migration runner."""
import sqlite3

from anamnesis.config import DB_PATH, MIGRATIONS_DIR


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


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


def run_migrations():
    conn = connect()
    cur = conn.cursor()
    ensure_migrations_table(cur)
    applied = {r[0] for r in cur.execute("SELECT name FROM anamnesis_migrations").fetchall()}
    to_apply = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.name not in applied)
    for path in to_apply:
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
