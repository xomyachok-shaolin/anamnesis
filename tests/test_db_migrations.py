import sqlite3
import unittest

from anamnestic import db


class MigrationSkipTests(unittest.TestCase):
    def test_entity_graph_migration_requires_edges_and_state_tables(self):
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        cur.execute("CREATE TABLE anamnestic_entity_edges (id INTEGER)")

        self.assertFalse(db._should_skip_migration(cur, "012_entity_graph.sql"))

        cur.execute("CREATE TABLE anamnestic_graph_state (content_session_id TEXT)")

        self.assertTrue(db._should_skip_migration(cur, "012_entity_graph.sql"))
        conn.close()


if __name__ == "__main__":
    unittest.main()
