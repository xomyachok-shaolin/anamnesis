import sqlite3
import unittest

from anamnestic.importance import score, backfill


class ScoreTests(unittest.TestCase):
    def test_empty_text(self):
        self.assertAlmostEqual(score("", "assistant"), 0.05)

    def test_none_like_short(self):
        self.assertAlmostEqual(score("ok", "assistant"), 0.10)

    def test_short_user_question_not_penalized(self):
        s = score("Как это работает?", "user")
        self.assertGreater(s, 0.1, "Short user question should not be penalized")

    def test_short_trivial_user_penalized(self):
        self.assertAlmostEqual(score("да", "user"), 0.10)

    def test_code_blocks_increase_score(self):
        text = "Вот код для решения задачи, который нужно рассмотреть:\n```python\ndef hello():\n    print('hello world')\n```\nГотово, используем это."
        s = score(text, "assistant")
        s_no_code = score("Вот простой текст без кода, достаточно длинный для оценки важности текста.", "assistant")
        self.assertGreater(s, s_no_code)

    def test_error_indicators_increase_score(self):
        text = "Traceback (most recent call last):\n  File 'x.py'\nError: division by zero"
        s = score(text, "assistant")
        self.assertGreaterEqual(s, 0.3)

    def test_decision_language_increase_score(self):
        text = "Мы решили использовать PostgreSQL вместо MySQL для нового сервиса, это наш вывод."
        s = score(text, "assistant")
        self.assertGreaterEqual(s, 0.25)

    def test_score_clamped_to_max_1(self):
        # text with everything: long, code, errors, decisions
        text = (
            "Решили " + "x" * 3000 + "\n```\ncode\n```\n```\nmore\n```\n```\nmore\n```\n"
            "Traceback error failed\nConclusion: resolved"
        )
        s = score(text, "assistant")
        self.assertLessEqual(s, 1.0)

    def test_score_never_below_005(self):
        s = score("", "assistant")
        self.assertGreaterEqual(s, 0.05)


class BackfillTests(unittest.TestCase):
    def setUp(self):
        # Use a temp file DB so backfill can close and we can reopen
        import tempfile, os
        self._db_fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(self._db_fd)

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE historical_turns (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                role TEXT NOT NULL,
                content_session_id TEXT,
                turn_number INTEGER,
                timestamp TEXT,
                platform_source TEXT,
                importance REAL DEFAULT 0.5
            );
        """)
        conn.execute(
            "INSERT INTO historical_turns (id, text, role, importance) VALUES (1, 'short', 'assistant', 0.5)"
        )
        conn.execute(
            "INSERT INTO historical_turns (id, text, role, importance) VALUES (2, ?, 'assistant', 0.5)",
            ("Traceback: error in something very important and long enough to not be trivial",),
        )
        conn.execute(
            "INSERT INTO historical_turns (id, text, role, importance) VALUES (3, 'already scored', 'user', 0.9)"
        )
        conn.commit()
        conn.close()

        import anamnestic.db as db_mod
        self._orig_connect = db_mod.connect
        db_path = self._db_path
        def _test_connect():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c
        db_mod.connect = _test_connect

    def tearDown(self):
        import anamnestic.db as db_mod
        db_mod.connect = self._orig_connect
        import os
        os.unlink(self._db_path)

    def test_backfill_updates_default_importance(self):
        result = backfill()
        self.assertEqual(result["scored"], 2)  # only rows with importance=0.5

        import anamnestic.db as db_mod
        conn = db_mod.connect()
        rows = conn.execute(
            "SELECT id, importance FROM historical_turns ORDER BY id"
        ).fetchall()
        conn.close()
        # id=1 short -> 0.1
        self.assertAlmostEqual(rows[0]["importance"], 0.1)
        # id=2 error text -> > 0.2
        self.assertGreater(rows[1]["importance"], 0.2)
        # id=3 not touched (was 0.9, not 0.5)
        self.assertAlmostEqual(rows[2]["importance"], 0.9)


if __name__ == "__main__":
    unittest.main()
