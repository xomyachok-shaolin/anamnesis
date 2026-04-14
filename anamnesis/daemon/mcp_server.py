#!/usr/bin/env python3
"""
stdio MCP server exposing hybrid memory search to Claude Code.

Tools:
  - mem_search(query, top_k=10, role=any, mode=hybrid) → list of hits
  - mem_get_turn(turn_id, context=2) → full turn + N surrounding turns
  - mem_get_session(session_id, max_turns=50) → session overview
  - mem_stats() → corpus statistics

Run standalone for smoke test:
  python -m anamnesis.daemon.mcp_server

Claude Code config (add to ~/.claude.json or via `claude mcp add`):
  {
    "mcpServers": {
      "anamnesis": {
        "command": "$HOME/.claude-mem/semantic-env/bin/python",
        "args": ["-m", "anamnesis.daemon.mcp_server"],
        "env": {"PYTHONPATH": "$HOME/projects/claude-anamnesis"}
      }
    }
  }
"""
from __future__ import annotations

import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from anamnesis.db import connect
from anamnesis.search.hybrid import (
    _embedder,
    _chroma_col,
    search as hybrid_search,
    format_hit,
    _bm25,
    _semantic,
)

# Preload heavy resources at module import: embedder (~220 MB model) + Chroma.
# Done once per stdio process; subsequent tool calls are <100ms.
_EMB = None
_COL = None


def _init():
    global _EMB, _COL
    if _EMB is None:
        print("[anamnesis] loading embedder + Chroma...", file=sys.stderr)
        _EMB = _embedder()
        _COL = _chroma_col()
        # warm up: first embed call compiles the ONNX graph
        list(_EMB.embed(["warmup"]))
        print("[anamnesis] ready", file=sys.stderr)


mcp = FastMCP("anamnesis")


@mcp.tool()
def mem_search(
    query: str,
    top_k: int = 10,
    role: str = "any",
    mode: str = "hybrid",
) -> dict[str, Any]:
    """Search historical Claude Code + subagent + Codex sessions.

    Args:
        query: free-form natural language query (Russian/English).
        top_k: number of hits to return (1..50).
        role: 'user' | 'assistant' | 'any' — filter by turn role.
        mode: 'hybrid' (BM25+semantic RRF, default) | 'semantic' | 'bm25'.

    Returns:
        {hits: [{rank, rrf_score, text, snippet, session, turn, role, timestamp, source, title, project, turn_id}]}
    """
    _init()
    top_k = max(1, min(top_k, 50))
    rl = role if role in ("user", "assistant") else None
    conn = connect()

    if mode == "hybrid":
        hits = hybrid_search(conn, query, top_k=top_k, pool=50, role=rl)
    elif mode == "semantic":
        hits = _semantic(_EMB, _COL, query, top_k, role=rl)
    elif mode == "bm25":
        hits = _bm25(conn, query, top_k)
    else:
        raise ValueError(f"unknown mode: {mode}")

    out = []
    for rank, h in enumerate(hits, 1):
        out.append({
            "rank": rank,
            "rrf_score": round(h.rrf_score, 4) if h.rrf_score else None,
            "bm25_rank": h.bm25_rank,
            "sem_rank": h.sem_rank,
            "turn_id": h.turn_id,
            "session": h.meta.get("session", ""),
            "turn": h.meta.get("turn"),
            "role": h.meta.get("role", ""),
            "timestamp": (h.meta.get("timestamp") or "")[:19],
            "source": h.meta.get("source", ""),
            "title": h.meta.get("title", ""),
            "project": h.meta.get("project", ""),
            "snippet": (h.text or "")[:400],
        })
    conn.close()
    return {"query": query, "mode": mode, "total": len(out), "hits": out}


@mcp.tool()
def mem_get_turn(turn_id: int, context: int = 2) -> dict[str, Any]:
    """Fetch a specific turn with N surrounding turns from the same session.

    Args:
        turn_id: id of the target turn (from mem_search results).
        context: number of turns before/after to include (0..10).
    """
    _init()
    context = max(0, min(context, 10))
    conn = connect()

    target = conn.execute(
        "SELECT * FROM historical_turns WHERE id = ?", (turn_id,)
    ).fetchone()
    if not target:
        conn.close()
        return {"error": f"turn_id {turn_id} not found"}
    sid = target["content_session_id"]
    tn = target["turn_number"]

    rows = conn.execute(
        """
        SELECT id, turn_number, role, text, timestamp
        FROM historical_turns
        WHERE content_session_id = ?
          AND turn_number BETWEEN ? AND ?
        ORDER BY turn_number
        """,
        (sid, tn - context, tn + context),
    ).fetchall()

    sess = conn.execute(
        "SELECT custom_title, project, platform_source FROM sdk_sessions "
        "WHERE content_session_id = ?",
        (sid,),
    ).fetchone()

    conn.close()
    return {
        "session": sid,
        "title": sess["custom_title"] if sess else "",
        "project": sess["project"] if sess else "",
        "source": sess["platform_source"] if sess else "",
        "target_turn": tn,
        "turns": [
            {
                "id": r["id"],
                "turn": r["turn_number"],
                "role": r["role"],
                "timestamp": r["timestamp"][:19] if r["timestamp"] else "",
                "text": r["text"],
                "is_target": r["turn_number"] == tn,
            }
            for r in rows
        ],
    }


@mcp.tool()
def mem_get_session(session_id: str, max_turns: int = 50) -> dict[str, Any]:
    """Get overview of a session: metadata + first N turns."""
    conn = connect()
    sess = conn.execute(
        """
        SELECT content_session_id, memory_session_id, project, platform_source,
               user_prompt, started_at, completed_at, prompt_counter, custom_title
        FROM sdk_sessions WHERE content_session_id = ?
        """,
        (session_id,),
    ).fetchone()
    if not sess:
        conn.close()
        return {"error": f"session {session_id} not found"}

    turns = conn.execute(
        """
        SELECT id, turn_number, role, timestamp, substr(text, 1, 500) AS snippet
        FROM historical_turns
        WHERE content_session_id = ?
        ORDER BY turn_number
        LIMIT ?
        """,
        (session_id, max_turns),
    ).fetchall()
    total = conn.execute(
        "SELECT COUNT(*) FROM historical_turns WHERE content_session_id = ?",
        (session_id,),
    ).fetchone()[0]
    conn.close()
    return {
        "session": session_id,
        "title": sess["custom_title"] or "",
        "project": sess["project"],
        "source": sess["platform_source"],
        "started_at": sess["started_at"],
        "completed_at": sess["completed_at"],
        "prompt_count": sess["prompt_counter"],
        "total_turns": total,
        "turns_shown": len(turns),
        "turns": [
            {
                "id": r["id"],
                "turn": r["turn_number"],
                "role": r["role"],
                "timestamp": r["timestamp"][:19] if r["timestamp"] else "",
                "snippet": r["snippet"],
            }
            for r in turns
        ],
    }


@mcp.tool()
def mem_stats() -> dict[str, Any]:
    """Corpus statistics: totals and per-source/per-project breakdowns."""
    conn = connect()
    totals = {
        "sessions": conn.execute("SELECT COUNT(*) FROM sdk_sessions").fetchone()[0],
        "user_prompts": conn.execute("SELECT COUNT(*) FROM user_prompts").fetchone()[0],
        "turns": conn.execute("SELECT COUNT(*) FROM historical_turns").fetchone()[0],
    }
    by_source = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT platform_source, COUNT(*) FROM historical_turns GROUP BY platform_source"
        ).fetchall()
    }
    by_role = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT role, COUNT(*) FROM historical_turns GROUP BY role"
        ).fetchall()
    }
    top_projects = [
        {"project": r[0], "sessions": r[1]}
        for r in conn.execute(
            """
            SELECT project, COUNT(*) n FROM sdk_sessions
            GROUP BY project ORDER BY n DESC LIMIT 10
            """
        ).fetchall()
    ]
    conn.close()
    return {
        "totals": totals,
        "turns_by_source": by_source,
        "turns_by_role": by_role,
        "top_projects": top_projects,
    }


if __name__ == "__main__":
    mcp.run()
