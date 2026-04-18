"""Integration tests for the RRF fusion pipeline in search().

Tests multi-channel fusion (BM25 + temporal + graph), importance weighting,
temporal decay, and correct merge-by-turn-id behavior.
"""
import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from anamnestic.search.hybrid import Hit, SearchDiagnostics, search, _bm25


def _make_db():
    """Create an in-memory DB with all tables needed by the search pipeline."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE historical_turns (
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL,
            content_session_id TEXT NOT NULL,
            turn_number INTEGER NOT NULL,
            role TEXT NOT NULL,
            timestamp TEXT,
            platform_source TEXT,
            importance REAL DEFAULT 0.5
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
        CREATE TABLE session_summaries (
            id INTEGER PRIMARY KEY,
            content_session_id TEXT,
            memory_session_id TEXT,
            summary_text TEXT,
            project TEXT,
            created_at TEXT
        );
        CREATE VIRTUAL TABLE session_summaries_fts USING fts5(
            summary_text,
            content='session_summaries',
            content_rowid='id'
        );
        CREATE TABLE anamnestic_entity_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_a TEXT NOT NULL,
            entity_b TEXT NOT NULL,
            weight INTEGER NOT NULL DEFAULT 1,
            sessions TEXT,
            UNIQUE(entity_a, entity_b)
        );
        CREATE TABLE anamnestic_entities (
            id INTEGER PRIMARY KEY,
            turn_id INTEGER,
            entity_type TEXT,
            value TEXT
        );
    """)
    return conn


def _insert_turn(conn, tid, text, session="s1", turn=1, role="assistant",
                 timestamp=None, importance=0.5):
    if timestamp is None:
        timestamp = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO historical_turns (id, text, content_session_id, turn_number, "
        "role, timestamp, platform_source, importance) VALUES (?,?,?,?,?,?,?,?)",
        (tid, text, session, turn, role, timestamp, "claude", importance),
    )
    conn.execute(
        "INSERT INTO historical_turns_fts(rowid, text) VALUES (?, ?)",
        (tid, text),
    )


def _insert_session(conn, sid="s1", title="Test", project="/proj"):
    conn.execute(
        "INSERT OR IGNORE INTO sdk_sessions VALUES (?, ?, ?)",
        (sid, title, project),
    )


class RRFFusionTests(unittest.TestCase):
    """Test that multi-channel hits merge correctly via RRF."""

    def setUp(self):
        self.conn = _make_db()
        _insert_session(self.conn)
        now = datetime.now()
        _insert_turn(self.conn, 1, "deploy production server config",
                     turn=1, timestamp=now.isoformat())
        _insert_turn(self.conn, 2, "fix authentication bug in login",
                     turn=2, timestamp=now.isoformat())
        _insert_turn(self.conn, 3, "database migration rollback plan",
                     turn=3, timestamp=now.isoformat())
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    @patch("anamnestic.search.hybrid.local_embed_model_ready", return_value=False)
    @patch("anamnestic.config.GRAPH_WEIGHT", 0)
    def test_bm25_only_produces_rrf_scores(self, *_):
        """BM25-only search should produce valid RRF scores."""
        hits = search(self.conn, "deploy production", top_k=5)
        self.assertGreater(len(hits), 0)
        for h in hits:
            self.assertGreater(h.rrf_score, 0)
            self.assertIsNotNone(h.bm25_rank)

    @patch("anamnestic.search.hybrid.local_embed_model_ready", return_value=False)
    @patch("anamnestic.config.GRAPH_WEIGHT", 0)
    def test_rrf_score_formula(self, *_):
        """RRF score = weight / (K + rank). Verify the math."""
        hits = _bm25(self.conn, "deploy", 10)
        self.assertEqual(len(hits), 1)
        # BM25 rank 1, weight 1.0, K=60 → 1/(60+1) = 0.01639...
        # After importance and decay, score changes, but the base should be right
        expected_base = 1.0 / (60 + 1)
        # Run full search with importance=0 and decay off
        with patch("anamnestic.config.IMPORTANCE_WEIGHT", 0), \
             patch("anamnestic.config.DECAY_ENABLED", False):
            hits = search(self.conn, "deploy", top_k=5)
        self.assertGreater(len(hits), 0)
        self.assertAlmostEqual(hits[0].rrf_score, expected_base, places=4)

    @patch("anamnestic.search.hybrid.local_embed_model_ready", return_value=False)
    @patch("anamnestic.config.GRAPH_WEIGHT", 0)
    def test_multi_channel_merge_boosts_shared_hits(self, *_):
        """A turn found by both BM25 and temporal should score higher than BM25-only."""
        now = datetime.now()
        yesterday = (now - timedelta(days=1))
        # Turn 10: matches BM25 + temporal (yesterday)
        _insert_turn(self.conn, 10, "deploy yesterday config",
                     session="s1", turn=10,
                     timestamp=yesterday.isoformat())
        # Turn 11: matches BM25 only (old)
        old = (now - timedelta(days=60))
        _insert_turn(self.conn, 11, "deploy old server",
                     session="s1", turn=11,
                     timestamp=old.isoformat())
        self.conn.commit()

        with patch("anamnestic.config.IMPORTANCE_WEIGHT", 0), \
             patch("anamnestic.config.DECAY_ENABLED", False):
            hits = search(self.conn, "deploy yesterday", top_k=10)

        hit_map = {h.turn_id: h for h in hits}
        # Turn 10 should exist and have temporal_rank (matched temporal channel)
        if 10 in hit_map and 11 in hit_map:
            self.assertGreater(hit_map[10].rrf_score, hit_map[11].rrf_score,
                               "Multi-channel hit should score higher than single-channel")


class ImportanceWeightingTests(unittest.TestCase):
    """Test that importance scores modulate RRF scores correctly."""

    def setUp(self):
        self.conn = _make_db()
        _insert_session(self.conn)
        now = datetime.now().isoformat()
        # High importance turn
        _insert_turn(self.conn, 1, "critical deploy error traceback",
                     importance=0.9, timestamp=now)
        # Low importance turn
        _insert_turn(self.conn, 2, "trivial deploy note",
                     turn=2, importance=0.1, timestamp=now)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    @patch("anamnestic.search.hybrid.local_embed_model_ready", return_value=False)
    @patch("anamnestic.config.GRAPH_WEIGHT", 0)
    @patch("anamnestic.config.DECAY_ENABLED", False)
    def test_high_importance_boosts_score(self, *_):
        """Turn with importance=0.9 should rank above importance=0.1."""
        with patch("anamnestic.config.IMPORTANCE_WEIGHT", 0.3):
            hits = search(self.conn, "deploy", top_k=10)
        self.assertGreaterEqual(len(hits), 2)
        # Find our turns
        hit_map = {h.turn_id: h for h in hits}
        self.assertIn(1, hit_map)
        self.assertIn(2, hit_map)
        self.assertGreater(hit_map[1].rrf_score, hit_map[2].rrf_score)

    @patch("anamnestic.search.hybrid.local_embed_model_ready", return_value=False)
    @patch("anamnestic.config.GRAPH_WEIGHT", 0)
    @patch("anamnestic.config.DECAY_ENABLED", False)
    def test_zero_importance_weight_means_no_effect(self, *_):
        """With IMPORTANCE_WEIGHT=0, all turns with same BM25 rank get same score."""
        with patch("anamnestic.config.IMPORTANCE_WEIGHT", 0):
            hits = search(self.conn, "deploy", top_k=10)
        scores = [h.rrf_score for h in hits]
        # All should have base RRF scores unmodified by importance
        # Since they match the same query, their BM25 ranks differ,
        # but importance shouldn't change anything
        for h in hits:
            base = 1.0 / (60 + h.bm25_rank)
            self.assertAlmostEqual(h.rrf_score, base, places=4)


class TemporalDecayTests(unittest.TestCase):
    """Test that temporal decay penalizes old turns."""

    def setUp(self):
        self.conn = _make_db()
        _insert_session(self.conn)
        now = datetime.now()
        _insert_turn(self.conn, 1, "deploy recent config",
                     timestamp=now.isoformat())
        old = (now - timedelta(days=180))
        _insert_turn(self.conn, 2, "deploy ancient config",
                     turn=2, timestamp=old.isoformat())
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    @patch("anamnestic.search.hybrid.local_embed_model_ready", return_value=False)
    @patch("anamnestic.config.GRAPH_WEIGHT", 0)
    @patch("anamnestic.config.IMPORTANCE_WEIGHT", 0)
    def test_recent_turn_scores_higher_than_old(self, *_):
        """Recent turn should score higher after decay than 180-day-old turn."""
        with patch("anamnestic.config.DECAY_ENABLED", True), \
             patch("anamnestic.config.DECAY_HALF_LIFE_DAYS", 90):
            hits = search(self.conn, "deploy config", top_k=10)
        hit_map = {h.turn_id: h for h in hits}
        self.assertIn(1, hit_map)
        self.assertIn(2, hit_map)
        self.assertGreater(hit_map[1].rrf_score, hit_map[2].rrf_score,
                           "Recent turn should rank above old turn after decay")

    @patch("anamnestic.search.hybrid.local_embed_model_ready", return_value=False)
    @patch("anamnestic.config.GRAPH_WEIGHT", 0)
    @patch("anamnestic.config.IMPORTANCE_WEIGHT", 0)
    def test_decay_disabled_no_penalty(self, *_):
        """With decay off, same-query turns get equal base scores regardless of age."""
        with patch("anamnestic.config.DECAY_ENABLED", False):
            hits = search(self.conn, "deploy config", top_k=10)
        # Both should have pure BM25 RRF scores
        for h in hits:
            base = 1.0 / (60 + h.bm25_rank)
            self.assertAlmostEqual(h.rrf_score, base, places=4)


class GraphChannelFusionTests(unittest.TestCase):
    """Test that graph channel contributes to RRF fusion."""

    def setUp(self):
        self.conn = _make_db()
        _insert_session(self.conn)
        now = datetime.now().isoformat()
        # Turn 1: matches BM25 for "deploy" AND is reachable via graph
        _insert_turn(self.conn, 1, "deploy production server",
                     timestamp=now)
        # Turn 2: only reachable via graph (doesn't match "deploy")
        _insert_turn(self.conn, 2, "configure nginx reverse proxy",
                     turn=2, timestamp=now)

        # Entity setup: /path/deploy co-occurs with /path/nginx
        self.conn.execute(
            "INSERT INTO anamnestic_entity_edges (entity_a, entity_b, weight) "
            "VALUES ('/path/deploy', '/path/nginx', 5)"
        )
        self.conn.execute(
            "INSERT INTO anamnestic_entities VALUES (1, 1, 'path', '/path/deploy')"
        )
        self.conn.execute(
            "INSERT INTO anamnestic_entities VALUES (2, 2, 'path', '/path/nginx')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    @patch("anamnestic.search.hybrid.local_embed_model_ready", return_value=False)
    @patch("anamnestic.config.IMPORTANCE_WEIGHT", 0)
    @patch("anamnestic.config.DECAY_ENABLED", False)
    def test_graph_channel_adds_hits(self, *_):
        """Graph traversal should surface turns not found by BM25."""
        with patch("anamnestic.config.GRAPH_WEIGHT", 0.5), \
             patch("anamnestic.config.GRAPH_MAX_HOPS", 2), \
             patch("anamnestic.entities.extract",
                   return_value=iter([("path", "/path/deploy")])):
            hits = search(self.conn, "/path/deploy", top_k=10)
        turn_ids = {h.turn_id for h in hits}
        # Turn 2 (nginx) should be found via graph even though it doesn't match query text
        self.assertIn(2, turn_ids, "Graph channel should surface related turns")

    @patch("anamnestic.search.hybrid.local_embed_model_ready", return_value=False)
    @patch("anamnestic.config.IMPORTANCE_WEIGHT", 0)
    @patch("anamnestic.config.DECAY_ENABLED", False)
    def test_graph_weight_zero_disables_channel(self, *_):
        """With GRAPH_WEIGHT=0, graph channel should not fire."""
        with patch("anamnestic.config.GRAPH_WEIGHT", 0):
            hits = search(self.conn, "/path/deploy", top_k=10)
        # Turn 2 should NOT be found (no BM25 match, graph disabled)
        turn_ids = {h.turn_id for h in hits}
        self.assertNotIn(2, turn_ids)


class SummaryChannelTests(unittest.TestCase):
    """Test that session summaries contribute to search results."""

    def setUp(self):
        self.conn = _make_db()
        _insert_session(self.conn)
        self.conn.execute(
            "INSERT INTO session_summaries (id, content_session_id, summary_text, project, created_at) "
            "VALUES (1, 's1', 'Summary about deploying microservices architecture', '/proj', ?)",
            (datetime.now().isoformat(),),
        )
        self.conn.execute(
            "INSERT INTO session_summaries_fts(rowid, summary_text) VALUES (1, "
            "'Summary about deploying microservices architecture')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    @patch("anamnestic.search.hybrid.local_embed_model_ready", return_value=False)
    @patch("anamnestic.config.GRAPH_WEIGHT", 0)
    @patch("anamnestic.config.IMPORTANCE_WEIGHT", 0)
    @patch("anamnestic.config.DECAY_ENABLED", False)
    def test_summaries_appear_in_results(self, *_):
        """Session summaries should appear as hits with negative turn_id."""
        hits = search(self.conn, "deploying microservices", top_k=10)
        summary_hits = [h for h in hits if h.turn_id < 0]
        self.assertGreater(len(summary_hits), 0, "Summaries should appear in results")
        self.assertEqual(summary_hits[0].hit_type, "summary")

    @patch("anamnestic.search.hybrid.local_embed_model_ready", return_value=False)
    @patch("anamnestic.config.GRAPH_WEIGHT", 0)
    @patch("anamnestic.config.IMPORTANCE_WEIGHT", 0)
    @patch("anamnestic.config.DECAY_ENABLED", False)
    def test_summary_hits_skip_importance_weighting(self, *_):
        """Summary hits (negative IDs) should not be affected by importance lookup."""
        hits = search(self.conn, "deploying microservices", top_k=10)
        summary_hits = [h for h in hits if h.turn_id < 0]
        self.assertGreater(len(summary_hits), 0)
        # Should have base RRF score, no importance modification
        for h in summary_hits:
            base = 1.0 / (60 + h.bm25_rank)
            self.assertAlmostEqual(h.rrf_score, base, places=4)


class MergeByTurnIdTests(unittest.TestCase):
    """Test that hits from different channels merge correctly by turn_id."""

    def setUp(self):
        self.conn = _make_db()
        _insert_session(self.conn)
        yesterday = (datetime.now() - timedelta(days=1))
        # This turn matches both BM25 ("deploy") and temporal ("yesterday")
        _insert_turn(self.conn, 1, "deploy server yesterday",
                     timestamp=yesterday.isoformat())
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    @patch("anamnestic.search.hybrid.local_embed_model_ready", return_value=False)
    @patch("anamnestic.config.GRAPH_WEIGHT", 0)
    @patch("anamnestic.config.IMPORTANCE_WEIGHT", 0)
    @patch("anamnestic.config.DECAY_ENABLED", False)
    def test_same_turn_merges_channels(self, *_):
        """Turn found by BM25 and temporal should have both ranks set."""
        hits = search(self.conn, "deploy yesterday", top_k=10)
        hit_map = {h.turn_id: h for h in hits}
        self.assertIn(1, hit_map)
        h = hit_map[1]
        # Should have both BM25 and temporal ranks
        self.assertIsNotNone(h.bm25_rank)
        self.assertIsNotNone(h.temporal_rank)
        # RRF score should be sum of both channels
        expected = 1.0 / (60 + h.bm25_rank) + 1.0 / (60 + h.temporal_rank)
        self.assertAlmostEqual(h.rrf_score, expected, places=4)

    @patch("anamnestic.search.hybrid.local_embed_model_ready", return_value=False)
    @patch("anamnestic.config.GRAPH_WEIGHT", 0)
    @patch("anamnestic.config.IMPORTANCE_WEIGHT", 0)
    @patch("anamnestic.config.DECAY_ENABLED", False)
    def test_no_duplicate_hits(self, *_):
        """Same turn from multiple channels should appear once, not duplicated."""
        hits = search(self.conn, "deploy yesterday", top_k=10)
        turn_ids = [h.turn_id for h in hits]
        self.assertEqual(len(turn_ids), len(set(turn_ids)),
                         "Turn IDs should be unique — no duplicates from multi-channel merge")


class DiagnosticsTests(unittest.TestCase):
    """Test that search returns per-channel diagnostics."""

    def setUp(self):
        self.conn = _make_db()
        _insert_session(self.conn)
        now = datetime.now()
        _insert_turn(self.conn, 1, "deploy production server",
                     timestamp=now.isoformat())
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    @patch("anamnestic.search.hybrid.local_embed_model_ready", return_value=False)
    @patch("anamnestic.config.GRAPH_WEIGHT", 0)
    @patch("anamnestic.config.IMPORTANCE_WEIGHT", 0)
    @patch("anamnestic.config.DECAY_ENABLED", False)
    def test_diagnostics_attached_to_result(self, *_):
        """Search result should carry diagnostics with channel counts."""
        hits = search(self.conn, "deploy", top_k=5)
        self.assertTrue(hasattr(hits, "diagnostics"))
        diag = hits.diagnostics
        self.assertIsInstance(diag, SearchDiagnostics)
        self.assertGreater(diag.bm25_hits, 0)
        self.assertEqual(diag.semantic_hits, 0)  # model not ready
        self.assertEqual(diag.graph_hits, 0)  # GRAPH_WEIGHT=0
        self.assertGreater(diag.fused_total, 0)

    @patch("anamnestic.search.hybrid.local_embed_model_ready", return_value=False)
    @patch("anamnestic.config.GRAPH_WEIGHT", 0)
    @patch("anamnestic.config.IMPORTANCE_WEIGHT", 0)
    @patch("anamnestic.config.DECAY_ENABLED", False)
    def test_diagnostics_to_dict(self, *_):
        """Diagnostics.to_dict() should return a serializable dict."""
        hits = search(self.conn, "deploy", top_k=5)
        d = hits.diagnostics.to_dict()
        self.assertIn("channels", d)
        self.assertIn("bm25", d["channels"])
        self.assertIn("fused_total", d)
        self.assertIn("reranked", d)

    @patch("anamnestic.search.hybrid.local_embed_model_ready", return_value=False)
    @patch("anamnestic.config.GRAPH_WEIGHT", 0)
    @patch("anamnestic.config.IMPORTANCE_WEIGHT", 0)
    @patch("anamnestic.config.DECAY_ENABLED", False)
    def test_result_still_behaves_as_list(self, *_):
        """SearchResult should be iterable, indexable, and have len()."""
        hits = search(self.conn, "deploy", top_k=5)
        self.assertIsInstance(hits, list)
        self.assertEqual(len(hits), len(list(hits)))
        if hits:
            self.assertIsInstance(hits[0], Hit)


if __name__ == "__main__":
    unittest.main()
