import sqlite3
import unittest

from anamnestic.search.hybrid import _bm25, _fts_query

DEMO_PROJECT = "/workspace/projects/Тестовый проект (демо)"
DEMO_TITLE = "Тестовый проект"
DEMO_QUERY = f'"{DEMO_TITLE}" OR "{DEMO_PROJECT}"'


class HybridSearchTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE historical_turns (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                content_session_id TEXT NOT NULL,
                turn_number INTEGER NOT NULL,
                role TEXT NOT NULL,
                timestamp TEXT,
                platform_source TEXT
            );

            CREATE TABLE sdk_sessions (
                content_session_id TEXT PRIMARY KEY,
                custom_title TEXT,
                project TEXT
            );

            CREATE VIRTUAL TABLE historical_turns_fts USING fts5(
                text,
                content='historical_turns',
                content_rowid='id'
            );
            """
        )
        self.conn.execute(
            """
            INSERT INTO sdk_sessions (content_session_id, custom_title, project)
            VALUES (?, ?, ?)
            """,
            (
                "sess-1",
                "Demo automation",
                DEMO_PROJECT,
            ),
        )
        text = f'Проект "{DEMO_TITLE}" лежит в "{DEMO_PROJECT}".'
        self.conn.execute(
            """
            INSERT INTO historical_turns
                (id, text, content_session_id, turn_number, role, timestamp, platform_source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (1, text, "sess-1", 1, "user", "2026-04-15T00:00:00", "codex"),
        )
        self.conn.execute(
            "INSERT INTO historical_turns_fts(rowid, text) VALUES (?, ?)",
            (1, text),
        )

    def tearDown(self):
        self.conn.close()

    def test_fts_query_filters_boolean_operators_from_tokens(self):
        fts_expr = _fts_query(DEMO_QUERY)
        self.assertIsNotNone(fts_expr)
        self.assertNotIn(" OR OR ", fts_expr)
        self.assertNotIn("AND OR", fts_expr)
        self.assertNotIn("NOT OR", fts_expr)

    def test_bm25_handles_boolean_query_with_path_without_syntax_error(self):
        hits = _bm25(self.conn, DEMO_QUERY, 10)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].turn_id, 1)

    def test_bm25_returns_empty_for_operator_only_query(self):
        self.assertEqual(_bm25(self.conn, "OR NOT AND", 10), [])


if __name__ == "__main__":
    unittest.main()
