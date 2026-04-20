"""Extractive session summarization — no LLM required.

Builds structured summaries from historical turns using importance scores,
entity data, and heuristic extraction. Summaries are indexed in FTS and
Chroma for retrieval alongside raw turns.
"""
from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger(__name__)

BATCH_SIZE = 200

_BOILERPLATE_PREFIXES = (
    "# AGENTS.md",
    "# Context from my IDE",
    "<environment_context>",
    "<permissions",
    "<system-reminder>",
)


def _is_boilerplate(text: str) -> bool:
    """Return True if text is boilerplate/system context, not a real user prompt."""
    stripped = text.strip()
    return any(stripped.startswith(p) for p in _BOILERPLATE_PREFIXES)


def summarize_session(conn, content_session_id: str) -> dict | None:
    """Generate an extractive summary for a single session.

    Returns the summary dict or None if session has too few turns.
    """
    # Fetch session metadata
    sess = conn.execute(
        """SELECT content_session_id, memory_session_id, project,
                  custom_title, started_at, completed_at, prompt_counter
           FROM sdk_sessions WHERE content_session_id = ?""",
        (content_session_id,),
    ).fetchone()
    if not sess:
        return None

    memory_id = sess["memory_session_id"]
    if not memory_id:
        return None
    project = sess["project"] or ""

    # Fetch all turns ordered by turn_number
    turns = conn.execute(
        """SELECT id, turn_number, role, text, timestamp,
                  COALESCE(importance, 0.5) AS importance
           FROM historical_turns
           WHERE content_session_id = ?
           ORDER BY turn_number""",
        (content_session_id,),
    ).fetchall()

    if len(turns) < 2:
        return None

    # --- Extract structured fields ---

    # request: first non-boilerplate user prompt
    user_turns = [t for t in turns if t["role"] == "user"]
    real_user_turns = [t for t in user_turns if not _is_boilerplate(t["text"])]
    request = real_user_turns[0]["text"][:1000] if real_user_turns else ""

    # completed: last assistant turn (conclusion)
    assistant_turns = [t for t in turns if t["role"] == "assistant"]
    completed = assistant_turns[-1]["text"][:1000] if assistant_turns else ""

    # investigated + learned: top-importance turns (excl. first/last), skip boilerplate
    middle_turns = [t for t in turns[1:-1] if not _is_boilerplate(t["text"])]
    middle_turns.sort(key=lambda t: t["importance"], reverse=True)
    top_turns = middle_turns[:5]

    investigated_parts = []
    learned_parts = []
    for t in top_turns:
        snippet = t["text"][:500]
        if t["role"] == "user":
            investigated_parts.append(snippet)
        else:
            learned_parts.append(snippet)

    investigated = "\n---\n".join(investigated_parts) if investigated_parts else ""
    learned = "\n---\n".join(learned_parts) if learned_parts else ""

    # files_read / files_edited from entity table
    entities = conn.execute(
        """SELECT DISTINCT e.value
           FROM anamnestic_entities e
           JOIN historical_turns ht ON ht.id = e.turn_id
           WHERE ht.content_session_id = ? AND e.entity_type = 'path'""",
        (content_session_id,),
    ).fetchall()
    files = [e["value"] for e in entities]
    files_str = "\n".join(files[:30]) if files else ""

    # Compose summary_text for FTS/Chroma indexing
    title = sess["custom_title"] or "(no title)"
    summary_text = (
        f"Session: {title}\n"
        f"Project: {project}\n"
        f"Request: {request[:300]}\n"
        f"Investigated: {investigated[:500]}\n"
        f"Learned: {learned[:500]}\n"
        f"Completed: {completed[:300]}\n"
        f"Files: {files_str[:300]}"
    )

    now = datetime.now()
    now_iso = now.isoformat()
    now_epoch = int(now.timestamp())

    # Check if summary already exists for this session
    existing = conn.execute(
        "SELECT id FROM session_summaries WHERE memory_session_id = ? AND prompt_number = 1",
        (memory_id,),
    ).fetchone()

    if existing:
        conn.execute(
            """UPDATE session_summaries SET
                 summary_text = ?, content_session_id = ?,
                 investigated = ?, learned = ?, completed = ?, files_read = ?
               WHERE id = ?""",
            (summary_text, content_session_id,
             investigated, learned, completed, files_str,
             existing["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO session_summaries
                 (memory_session_id, content_session_id, project, request,
                  investigated, learned, completed, files_read,
                  summary_text, prompt_number,
                  created_at, created_at_epoch)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (memory_id, content_session_id, project, request,
             investigated, learned, completed, files_str,
             summary_text, now_iso, now_epoch),
        )

    # Mark as summarized
    conn.execute(
        "INSERT OR REPLACE INTO anamnestic_summary_state(content_session_id) VALUES (?)",
        (content_session_id,),
    )

    return {
        "session": content_session_id,
        "summary_length": len(summary_text),
        "files_found": len(files),
    }


def backfill(limit: int | None = None) -> dict:
    """Summarize sessions that haven't been processed yet."""
    from anamnestic.db import connect

    conn = connect()
    query = """
        SELECT s.content_session_id
        FROM sdk_sessions s
        JOIN (SELECT content_session_id, COUNT(*) AS cnt
              FROM historical_turns GROUP BY content_session_id HAVING cnt >= 2) t
            ON t.content_session_id = s.content_session_id
        LEFT JOIN anamnestic_summary_state ss
            ON ss.content_session_id = s.content_session_id
        WHERE ss.content_session_id IS NULL
          AND instr(s.content_session_id, ':') = 0
        ORDER BY s.started_at_epoch DESC
    """
    if limit:
        query += f" LIMIT {int(limit)}"

    sessions = conn.execute(query).fetchall()
    summarized = 0
    errors = 0

    for row in sessions:
        try:
            result = summarize_session(conn, row["content_session_id"])
            if result:
                summarized += 1
        except Exception as exc:
            log.warning("summary failed for %s: %s", row["content_session_id"], exc)
            errors += 1
        if (summarized + errors) % BATCH_SIZE == 0:
            conn.commit()

    conn.commit()
    conn.close()
    return {"summarized": summarized, "errors": errors}
