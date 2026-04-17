import sqlite3
import unittest
from datetime import datetime, timedelta

from anamnesis.decay import decay_factor, archive_old_turns


class DecayFactorTests(unittest.TestCase):
    def test_recent_is_near_one(self):
        ts = datetime.now().isoformat()
        self.assertAlmostEqual(decay_factor(ts, 90), 1.0, places=1)

    def test_half_life_is_half(self):
        ts = (datetime.now() - timedelta(days=90)).isoformat()
        self.assertAlmostEqual(decay_factor(ts, 90), 0.5, places=1)

    def test_double_half_life_is_quarter(self):
        ts = (datetime.now() - timedelta(days=180)).isoformat()
        self.assertAlmostEqual(decay_factor(ts, 90), 0.25, places=1)

    def test_very_old_floors_at_01(self):
        ts = (datetime.now() - timedelta(days=1000)).isoformat()
        self.assertAlmostEqual(decay_factor(ts, 90), 0.1)

    def test_none_returns_05(self):
        self.assertAlmostEqual(decay_factor(None, 90), 0.5)

    def test_invalid_timestamp_returns_05(self):
        self.assertAlmostEqual(decay_factor("not-a-date", 90), 0.5)

    def test_future_timestamp_returns_1(self):
        ts = (datetime.now() + timedelta(days=10)).isoformat()
        self.assertAlmostEqual(decay_factor(ts, 90), 1.0)

    def test_custom_half_life(self):
        ts = (datetime.now() - timedelta(days=30)).isoformat()
        self.assertAlmostEqual(decay_factor(ts, 30), 0.5, places=1)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE historical_turns (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                content_session_id TEXT NOT NULL,
                turn_number INTEGER,
                role TEXT,
                timestamp TEXT,
                platform_source TEXT,
                importance REAL DEFAULT 0.5
            );
            CREATE TABLE anamnesis_archived_turns (
                id INTEGER PRIMARY KEY,
                content_session_id TEXT NOT NULL,
                turn_number INTEGER,
                role TEXT,
                text TEXT,
                timestamp TEXT,
                platform_source TEXT,
                importance REAL,
                archived_at TEXT NOT NULL DEFAULT (datetime('now')),
                archive_reason TEXT
            );
            CREATE TABLE anamnesis_summary_state (
                content_session_id TEXT PRIMARY KEY,
                summarized_at TEXT
            );
            CREATE TABLE anamnesis_embed_state (
                turn_id INTEGER PRIMARY KEY,
                collection TEXT,
                embedded_at TEXT
            );
        """)
        old_ts = (datetime.now() - timedelta(days=400)).isoformat()
        recent_ts = (datetime.now() - timedelta(days=10)).isoformat()

        # Old, low importance, has summary -> should archive
        self.conn.execute(
            "INSERT INTO historical_turns VALUES (1, 'old low', 's1', 1, 'assistant', ?, 'claude', 0.1)",
            (old_ts,),
        )
        # Old, high importance -> should NOT archive
        self.conn.execute(
            "INSERT INTO historical_turns VALUES (2, 'old important', 's1', 2, 'assistant', ?, 'claude', 0.8)",
            (old_ts,),
        )
        # Recent, low importance -> should NOT archive (too recent)
        self.conn.execute(
            "INSERT INTO historical_turns VALUES (3, 'recent low', 's1', 3, 'assistant', ?, 'claude', 0.1)",
            (recent_ts,),
        )
        # Old, low importance, NO summary -> should NOT archive
        self.conn.execute(
            "INSERT INTO historical_turns VALUES (4, 'old no summary', 's2', 1, 'assistant', ?, 'claude', 0.1)",
            (old_ts,),
        )

        # Only s1 has a summary
        self.conn.execute("INSERT INTO anamnesis_summary_state VALUES ('s1', datetime('now'))")
        self.conn.execute("INSERT INTO anamnesis_embed_state VALUES (1, 'col', datetime('now'))")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_archive_moves_correct_turns(self):
        result = archive_old_turns(self.conn, age_days=365, importance_threshold=0.3)
        self.assertEqual(result["archived"], 1)

        # Turn 1 should be archived
        archived = self.conn.execute("SELECT * FROM anamnesis_archived_turns").fetchall()
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0]["id"], 1)

        # Turn 1 should be gone from historical_turns
        remaining = self.conn.execute("SELECT id FROM historical_turns ORDER BY id").fetchall()
        remaining_ids = [r["id"] for r in remaining]
        self.assertNotIn(1, remaining_ids)
        self.assertIn(2, remaining_ids)  # high importance
        self.assertIn(3, remaining_ids)  # too recent
        self.assertIn(4, remaining_ids)  # no summary

        # Embed state cleaned
        es = self.conn.execute("SELECT * FROM anamnesis_embed_state").fetchall()
        self.assertEqual(len(es), 0)

    def test_archive_returns_zero_when_nothing_to_archive(self):
        result = archive_old_turns(self.conn, age_days=9999)
        self.assertEqual(result["archived"], 0)


if __name__ == "__main__":
    unittest.main()
