import sqlite3
import sys
import unittest
from types import ModuleType
from unittest.mock import patch


class _FastMCPStub:
    def __init__(self, *_args, **_kwargs):
        pass

    def tool(self):
        def decorator(func):
            return func
        return decorator


mcp_module = ModuleType("mcp")
mcp_server_module = ModuleType("mcp.server")
fastmcp_module = ModuleType("mcp.server.fastmcp")
fastmcp_module.FastMCP = _FastMCPStub
sys.modules.setdefault("mcp", mcp_module)
sys.modules.setdefault("mcp.server", mcp_server_module)
sys.modules.setdefault("mcp.server.fastmcp", fastmcp_module)

from anamnesis.daemon import mcp_server


class _FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class MCPPServerTests(unittest.TestCase):
    def test_mem_search_returns_friendly_fts_error(self):
        conn = _FakeConn()
        with (
            patch.object(mcp_server, "connect", return_value=conn),
            patch.object(
                mcp_server,
                "_bm25",
                side_effect=sqlite3.OperationalError('fts5: syntax error near "OR"'),
            ),
        ):
            result = mcp_server.mem_search('a OR b', mode="bm25")

        self.assertEqual(result["total"], 0)
        self.assertEqual(result["hits"], [])
        self.assertEqual(result["error"], 'fts5: syntax error near "OR"')
        self.assertIn("plain-language query", result["hint"])
        self.assertTrue(conn.closed)

    def test_mem_search_returns_friendly_error_for_unknown_mode(self):
        conn = _FakeConn()
        with patch.object(mcp_server, "connect", return_value=conn):
            result = mcp_server.mem_search("test", mode="weird")

        self.assertEqual(result["total"], 0)
        self.assertEqual(result["hits"], [])
        self.assertEqual(result["error"], "unknown mode: weird")
        self.assertIn("hybrid", result["hint"])
        self.assertTrue(conn.closed)

    def test_mem_search_attaches_coverage_on_empty_hits(self):
        conn = _FakeConn()
        coverage = {"n_turns": 42, "date_range_indexed": ["2025-01-01", "2026-04-15"]}
        with (
            patch.object(mcp_server, "connect", return_value=conn),
            patch.object(mcp_server, "_bm25", return_value=[]),
            patch.object(mcp_server, "_safe_coverage", return_value=coverage),
        ):
            result = mcp_server.mem_search("nothing here", mode="bm25")

        self.assertEqual(result["total"], 0)
        self.assertEqual(result["hits"], [])
        self.assertEqual(result["searched"], coverage)
        self.assertNotIn("error", result)
        self.assertTrue(conn.closed)

    def test_mem_search_bm25_does_not_load_embedder(self):
        conn = _FakeConn()
        with (
            patch.object(mcp_server, "connect", return_value=conn),
            patch.object(mcp_server, "_bm25", return_value=[]),
            patch.object(mcp_server, "_init", side_effect=AssertionError("_init should not be called")),
        ):
            result = mcp_server.mem_search("test", mode="bm25")

        self.assertEqual(result["total"], 0)
        self.assertEqual(result["hits"], [])
        self.assertNotIn("error", result)
        self.assertTrue(conn.closed)


if __name__ == "__main__":
    unittest.main()
