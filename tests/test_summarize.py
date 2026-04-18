import sqlite3
import unittest

from anamnestic.summarize import summarize_session


class SummarizeSessionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE sdk_sessions (
                content_session_id TEXT PRIMARY KEY,
                memory_session_id TEXT,
                project TEXT,
                custom_title TEXT,
                started_at TEXT,
                completed_at TEXT,
                prompt_counter INTEGER
            );
            CREATE TABLE historical_turns (
                id INTEGER PRIMARY KEY,
                content_session_id TEXT NOT NULL,
                turn_number INTEGER NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                timestamp TEXT,
                platform_source TEXT,
                importance REAL DEFAULT 0.5
            );
            CREATE TABLE session_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_session_id TEXT NOT NULL,
                content_session_id TEXT,
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
                summary_text TEXT,
                created_at TEXT NOT NULL,
                created_at_epoch INTEGER NOT NULL
            );
            CREATE TABLE anamnestic_summary_state (
                content_session_id TEXT PRIMARY KEY,
                summarized_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE anamnestic_entities (
                id INTEGER PRIMARY KEY,
                turn_id INTEGER,
                entity_type TEXT,
                value TEXT
            );
        """)

        self.conn.execute(
            """INSERT INTO sdk_sessions VALUES
               ('s1', 'mem-1', '/proj/test', 'Test Session', '2026-04-15', '2026-04-15', 3)"""
        )
        turns = [
            (1, "s1", 1, "user", "Как настроить nginx для reverse proxy?", "2026-04-15T10:00:00", 0.25),
            (2, "s1", 2, "assistant", "Вот конфигурация nginx для reverse proxy:\n```\nserver { ... }\n```", "2026-04-15T10:01:00", 0.45),
            (3, "s1", 3, "user", "А как добавить SSL?", "2026-04-15T10:02:00", 0.25),
            (4, "s1", 4, "assistant", "Решили использовать certbot. Вот итоговая конфигурация.", "2026-04-15T10:03:00", 0.35),
        ]
        for t in turns:
            self.conn.execute(
                "INSERT INTO historical_turns (id, content_session_id, turn_number, role, text, timestamp, importance) VALUES (?,?,?,?,?,?,?)",
                t,
            )
        # Entity
        self.conn.execute(
            "INSERT INTO anamnestic_entities VALUES (1, 2, 'path', '/etc/nginx/nginx.conf')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_summarize_produces_summary(self):
        result = summarize_session(self.conn, "s1")
        self.conn.commit()
        self.assertIsNotNone(result)
        self.assertEqual(result["session"], "s1")
        self.assertGreater(result["summary_length"], 0)
        self.assertEqual(result["files_found"], 1)

    def test_summary_stored_in_table(self):
        summarize_session(self.conn, "s1")
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM session_summaries WHERE memory_session_id = 'mem-1'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertIn("nginx", row["request"])
        self.assertIn("certbot", row["completed"])
        self.assertIn("/etc/nginx", row["files_read"])
        self.assertIsNotNone(row["summary_text"])
        self.assertGreater(len(row["summary_text"]), 50)

    def test_summary_state_recorded(self):
        summarize_session(self.conn, "s1")
        self.conn.commit()
        state = self.conn.execute(
            "SELECT * FROM anamnestic_summary_state WHERE content_session_id = 's1'"
        ).fetchone()
        self.assertIsNotNone(state)

    def test_returns_none_for_empty_session(self):
        self.conn.execute("INSERT INTO sdk_sessions VALUES ('s2', 'mem-2', '/p', 'Empty', '', '', 0)")
        result = summarize_session(self.conn, "s2")
        self.assertIsNone(result)

    def test_returns_none_for_single_turn(self):
        self.conn.execute("INSERT INTO sdk_sessions VALUES ('s3', 'mem-3', '/p', 'One', '', '', 1)")
        self.conn.execute(
            "INSERT INTO historical_turns (id, content_session_id, turn_number, role, text) VALUES (10, 's3', 1, 'user', 'hello')"
        )
        result = summarize_session(self.conn, "s3")
        self.assertIsNone(result)

    def test_returns_none_for_missing_memory_id(self):
        self.conn.execute("INSERT INTO sdk_sessions VALUES ('s4', NULL, '/p', 'No mem', '', '', 1)")
        self.conn.execute(
            "INSERT INTO historical_turns (id, content_session_id, turn_number, role, text) VALUES (20, 's4', 1, 'user', 'a')"
        )
        self.conn.execute(
            "INSERT INTO historical_turns (id, content_session_id, turn_number, role, text) VALUES (21, 's4', 2, 'assistant', 'b')"
        )
        result = summarize_session(self.conn, "s4")
        self.assertIsNone(result)

    def test_idempotent_update(self):
        summarize_session(self.conn, "s1")
        self.conn.commit()
        # Run again — should UPDATE, not duplicate
        summarize_session(self.conn, "s1")
        self.conn.commit()
        count = self.conn.execute(
            "SELECT COUNT(*) FROM session_summaries WHERE memory_session_id = 'mem-1'"
        ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
