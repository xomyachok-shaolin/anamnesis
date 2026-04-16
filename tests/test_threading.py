import sqlite3
import unittest
from unittest.mock import patch, MagicMock

from anamnesis import threading


class _UnclosableConn(sqlite3.Connection):
    def close(self):
        pass  # keep open across multiple mock calls

    def real_close(self):
        super().close()


def _make_conn_with_sessions(sessions):
    """Create an in-memory DB with sdk_sessions and thread tables."""
    conn = sqlite3.connect(":memory:", factory=_UnclosableConn)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE sdk_sessions (
            content_session_id TEXT PRIMARY KEY,
            project TEXT NOT NULL,
            platform_source TEXT DEFAULT 'claude',
            user_prompt TEXT,
            started_at TEXT,
            started_at_epoch INTEGER,
            completed_at TEXT,
            custom_title TEXT,
            prompt_counter INTEGER DEFAULT 0
        );
        CREATE TABLE anamnesis_session_threads (
            session_id TEXT PRIMARY KEY,
            thread_id INTEGER NOT NULL,
            thread_order INTEGER NOT NULL
        );
        CREATE INDEX idx_threads_thread
            ON anamnesis_session_threads(thread_id, thread_order);
    """)
    for s in sessions:
        conn.execute(
            "INSERT INTO sdk_sessions"
            "(content_session_id, project, started_at, started_at_epoch, user_prompt, custom_title)"
            "VALUES (?, ?, ?, ?, ?, ?)",
            (s["id"], s["project"], "", s["epoch"], s.get("prompt", ""), s.get("title", "")),
        )
    conn.commit()
    return conn


class ThreadingTests(unittest.TestCase):
    def test_same_project_close_sessions_form_one_thread(self):
        sessions = [
            {"id": "a", "project": "proj1", "epoch": 1000},
            {"id": "b", "project": "proj1", "epoch": 2000},
            {"id": "c", "project": "proj1", "epoch": 3000},
        ]
        conn = _make_conn_with_sessions(sessions)
        with patch.object(threading, "connect", return_value=conn):
            result = threading.compute(gap_sec=86400)
        self.assertEqual(result["threads"], 1)
        self.assertEqual(result["sessions_linked"], 3)

    def test_different_projects_form_separate_threads(self):
        sessions = [
            {"id": "a", "project": "proj1", "epoch": 1000},
            {"id": "b", "project": "proj2", "epoch": 2000},
        ]
        conn = _make_conn_with_sessions(sessions)
        with patch.object(threading, "connect", return_value=conn):
            result = threading.compute(gap_sec=86400)
        self.assertEqual(result["threads"], 2)

    def test_large_gap_splits_thread(self):
        day = 86400
        sessions = [
            {"id": "a", "project": "proj1", "epoch": 0},
            {"id": "b", "project": "proj1", "epoch": 3 * day},
            {"id": "c", "project": "proj1", "epoch": 20 * day},  # > 7 day gap
        ]
        conn = _make_conn_with_sessions(sessions)
        with patch.object(threading, "connect", return_value=conn):
            result = threading.compute(gap_sec=7 * day)
        self.assertEqual(result["threads"], 2)

    def test_subagent_sessions_excluded(self):
        sessions = [
            {"id": "parent", "project": "proj1", "epoch": 1000},
            {"id": "parent:agent1", "project": "proj1", "epoch": 1500},
            {"id": "other", "project": "proj1", "epoch": 2000},
        ]
        conn = _make_conn_with_sessions(sessions)
        with patch.object(threading, "connect", return_value=conn):
            result = threading.compute(gap_sec=86400)
        self.assertEqual(result["sessions_linked"], 2)  # parent + other, not subagent

    def test_get_thread_returns_ordered_chain(self):
        sessions = [
            {"id": "a", "project": "proj1", "epoch": 1000, "title": "first"},
            {"id": "b", "project": "proj1", "epoch": 2000, "title": "second"},
            {"id": "c", "project": "proj1", "epoch": 3000, "title": "third"},
        ]
        conn = _make_conn_with_sessions(sessions)
        with patch.object(threading, "connect", return_value=conn):
            threading.compute(gap_sec=86400)
        with patch.object(threading, "connect", return_value=conn):
            chain = threading.get_thread("b")
        self.assertEqual(len(chain), 3)
        self.assertEqual(chain[0]["session"], "a")
        self.assertEqual(chain[1]["session"], "b")
        self.assertTrue(chain[1]["is_target"])
        self.assertEqual(chain[2]["session"], "c")


if __name__ == "__main__":
    unittest.main()
