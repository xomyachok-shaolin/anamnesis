import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from anamnesis.search.temporal import detect_time_range, temporal_search


class DetectTimeRangeTests(unittest.TestCase):
    """Test temporal expression parsing for EN and RU."""

    @patch("anamnesis.search.temporal._now")
    def test_yesterday_ru(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 18, 14, 30)
        r = detect_time_range("что делали вчера")
        self.assertIsNotNone(r)
        self.assertTrue(r[0].startswith("2026-04-17"))
        self.assertTrue(r[1].startswith("2026-04-17"))

    @patch("anamnesis.search.temporal._now")
    def test_yesterday_en(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 18, 14, 30)
        r = detect_time_range("what happened yesterday")
        self.assertIsNotNone(r)
        self.assertTrue(r[0].startswith("2026-04-17"))

    @patch("anamnesis.search.temporal._now")
    def test_today_ru(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 18, 14, 30)
        r = detect_time_range("поиск сегодня")
        self.assertIsNotNone(r)
        self.assertTrue(r[0].startswith("2026-04-18"))
        self.assertTrue(r[1].startswith("2026-04-18"))

    @patch("anamnesis.search.temporal._now")
    def test_last_week_ru(self, mock_now):
        # Friday April 18 2026
        mock_now.return_value = datetime(2026, 4, 18, 14, 30)
        r = detect_time_range("на прошлой неделе")
        self.assertIsNotNone(r)
        start = datetime.fromisoformat(r[0])
        end = datetime.fromisoformat(r[1])
        self.assertLess(start, mock_now.return_value)
        self.assertLess(end, mock_now.return_value)

    @patch("anamnesis.search.temporal._now")
    def test_last_week_en(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 18, 14, 30)
        r = detect_time_range("last week changes")
        self.assertIsNotNone(r)

    @patch("anamnesis.search.temporal._now")
    def test_n_days_ago_en(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 18, 14, 30)
        r = detect_time_range("3 days ago")
        self.assertIsNotNone(r)
        self.assertTrue(r[0].startswith("2026-04-15"))

    @patch("anamnesis.search.temporal._now")
    def test_n_days_ago_ru(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 18, 14, 30)
        r = detect_time_range("5 дней назад")
        self.assertIsNotNone(r)
        self.assertTrue(r[0].startswith("2026-04-13"))

    @patch("anamnesis.search.temporal._now")
    def test_month_name_ru(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 18, 14, 30)
        r = detect_time_range("в марте")
        self.assertIsNotNone(r)
        self.assertTrue(r[0].startswith("2026-03-01"))
        self.assertTrue(r[1].startswith("2026-03-31"))

    @patch("anamnesis.search.temporal._now")
    def test_month_name_en(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 18, 14, 30)
        r = detect_time_range("in march")
        self.assertIsNotNone(r)
        self.assertTrue(r[0].startswith("2026-03-01"))

    def test_no_temporal_signal(self):
        self.assertIsNone(detect_time_range("how to configure nginx"))
        self.assertIsNone(detect_time_range("MCP server настройка"))
        self.assertIsNone(detect_time_range(""))


class TemporalSearchTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
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
        """)
        self.conn.execute(
            "INSERT INTO sdk_sessions VALUES ('s1', 'Test Session', '/proj')"
        )
        # Insert turns across different dates
        for i, ts in enumerate([
            "2026-04-15T10:00:00",
            "2026-04-16T11:00:00",
            "2026-04-17T12:00:00",
            "2026-04-18T13:00:00",
        ], 1):
            self.conn.execute(
                "INSERT INTO historical_turns VALUES (?, ?, 's1', ?, 'assistant', ?, 'claude')",
                (i, f"Turn {i} on {ts[:10]}", i, ts),
            )

    def tearDown(self):
        self.conn.close()

    def test_temporal_search_returns_hits_in_range(self):
        hits = temporal_search(
            self.conn, ("2026-04-16T00:00:00", "2026-04-17T23:59:59"), k=10
        )
        self.assertEqual(len(hits), 2)
        # Most recent first
        self.assertEqual(hits[0].temporal_rank, 1)
        self.assertIn("04-17", hits[0].meta["timestamp"])

    def test_temporal_search_empty_range(self):
        hits = temporal_search(
            self.conn, ("2026-01-01T00:00:00", "2026-01-02T00:00:00"), k=10
        )
        self.assertEqual(len(hits), 0)

    def test_temporal_search_respects_k(self):
        hits = temporal_search(
            self.conn, ("2026-04-01T00:00:00", "2026-04-30T23:59:59"), k=2
        )
        self.assertEqual(len(hits), 2)


if __name__ == "__main__":
    unittest.main()
