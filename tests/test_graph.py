import sqlite3
import unittest

from anamnesis.graph import graph_search


class GraphSearchTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE anamnesis_entity_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_a TEXT NOT NULL,
                entity_b TEXT NOT NULL,
                weight INTEGER NOT NULL DEFAULT 1,
                sessions TEXT,
                UNIQUE(entity_a, entity_b)
            );
            CREATE TABLE anamnesis_entities (
                id INTEGER PRIMARY KEY,
                turn_id INTEGER,
                entity_type TEXT,
                value TEXT
            );
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

        # Build a small graph: A -- B -- C
        #                            \-- D (low weight, below MIN_EDGE_WEIGHT)
        self.conn.execute(
            "INSERT INTO anamnesis_entity_edges (entity_a, entity_b, weight) VALUES ('/path/a', '/path/b', 5)"
        )
        self.conn.execute(
            "INSERT INTO anamnesis_entity_edges (entity_a, entity_b, weight) VALUES ('/path/b', '/path/c', 3)"
        )
        # weight=1 is below MIN_EDGE_WEIGHT=2, so this edge should be pruned
        self.conn.execute(
            "INSERT INTO anamnesis_entity_edges (entity_a, entity_b, weight) VALUES ('/path/b', '/path/d', 1)"
        )

        # Turns mentioning these entities
        self.conn.execute("INSERT INTO sdk_sessions VALUES ('s1', 'Test', '/proj')")
        self.conn.execute(
            "INSERT INTO historical_turns VALUES (1, 'turn about B', 's1', 1, 'assistant', '2026-04-15T10:00:00', 'claude')"
        )
        self.conn.execute(
            "INSERT INTO historical_turns VALUES (2, 'turn about C', 's1', 2, 'assistant', '2026-04-15T11:00:00', 'claude')"
        )
        self.conn.execute(
            "INSERT INTO historical_turns VALUES (3, 'turn about D', 's1', 3, 'assistant', '2026-04-15T12:00:00', 'claude')"
        )
        self.conn.execute("INSERT INTO anamnesis_entities VALUES (1, 1, 'path', '/path/b')")
        self.conn.execute("INSERT INTO anamnesis_entities VALUES (2, 2, 'path', '/path/c')")
        self.conn.execute("INSERT INTO anamnesis_entities VALUES (3, 3, 'path', '/path/d')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_graph_search_finds_neighbors(self):
        # Query entity A -> should find B (hop 1), then C (hop 2)
        # D is pruned because its edge weight (1) < MIN_EDGE_WEIGHT (2)
        hits = graph_search(self.conn, ["/path/a"], max_hops=2, k=10)
        self.assertGreater(len(hits), 0)
        turn_ids = {h.turn_id for h in hits}
        self.assertIn(1, turn_ids)  # B
        self.assertIn(2, turn_ids)  # C
        self.assertNotIn(3, turn_ids)  # D pruned (weight=1 < MIN_EDGE_WEIGHT)

    def test_graph_search_hop_1_only(self):
        # A -> B at hop 1 only
        hits = graph_search(self.conn, ["/path/a"], max_hops=1, k=10)
        turn_ids = {h.turn_id for h in hits}
        self.assertIn(1, turn_ids)  # B is neighbor of A
        # C and D should NOT appear (they need hop 2)

    def test_graph_search_empty_entities(self):
        hits = graph_search(self.conn, [], max_hops=2, k=10)
        self.assertEqual(len(hits), 0)

    def test_graph_search_unknown_entity(self):
        hits = graph_search(self.conn, ["/nonexistent"], max_hops=2, k=10)
        self.assertEqual(len(hits), 0)

    def test_graph_rank_assigned(self):
        hits = graph_search(self.conn, ["/path/a"], max_hops=2, k=10)
        for i, h in enumerate(hits, 1):
            self.assertEqual(h.graph_rank, i)

    def test_graph_search_respects_k(self):
        hits = graph_search(self.conn, ["/path/a"], max_hops=2, k=1)
        self.assertEqual(len(hits), 1)

    def test_low_weight_edges_pruned(self):
        """Edges with weight < MIN_EDGE_WEIGHT should not be traversed."""
        # D is only reachable via weight=1 edge from B
        hits = graph_search(self.conn, ["/path/b"], max_hops=1, k=10)
        turn_ids = {h.turn_id for h in hits}
        # C is reachable (weight=3), D is not (weight=1)
        self.assertIn(2, turn_ids)   # C
        self.assertNotIn(3, turn_ids)  # D pruned

    def test_idf_normalization_favors_rare_entities(self):
        """Rare entities (low degree) should rank above common ones."""
        # Add a hub entity connected to many things (high degree)
        self.conn.execute(
            "INSERT INTO anamnesis_entity_edges (entity_a, entity_b, weight) "
            "VALUES ('/path/hub', '/path/e1', 3)"
        )
        for i in range(2, 20):
            self.conn.execute(
                "INSERT INTO anamnesis_entity_edges (entity_a, entity_b, weight) "
                f"VALUES ('/path/hub', '/path/noise{i}', 2)"
            )
        # Add a rare entity with same weight but low degree
        self.conn.execute(
            "INSERT INTO anamnesis_entity_edges (entity_a, entity_b, weight) "
            "VALUES ('/path/rare_src', '/path/e1', 3)"
        )
        # e1 is reachable from both hub (high degree) and rare_src (low degree)
        # Turn for e1
        self.conn.execute(
            "INSERT INTO historical_turns VALUES (10, 'turn about e1', 's1', 10, "
            "'assistant', '2026-04-15T10:00:00', 'claude')"
        )
        self.conn.execute(
            "INSERT INTO anamnesis_entities VALUES (10, 10, 'path', '/path/e1')"
        )
        self.conn.commit()

        # Search from rare_src should find e1
        hits_rare = graph_search(self.conn, ["/path/rare_src"], max_hops=1, k=10)
        self.assertTrue(any(h.turn_id == 10 for h in hits_rare))


if __name__ == "__main__":
    unittest.main()
