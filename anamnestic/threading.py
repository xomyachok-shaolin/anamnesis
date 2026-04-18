"""Session threading: group sessions into continuation chains.

Two sessions belong to the same thread if they share the same project
and the gap between them is below a threshold (default 7 days).

Subagent sessions (content_session_id contains ':') are excluded —
they are already structurally linked to their parent session.

Threading is a batch operation: call compute() after sync, or via
`anamnestic threads` CLI. Results go into anamnestic_session_threads.
"""
from __future__ import annotations

from anamnestic.db import connect

GAP_THRESHOLD_SEC = 7 * 24 * 3600  # 7 days


def compute(gap_sec: int = GAP_THRESHOLD_SEC) -> dict:
    """Recompute all session threads from scratch.

    Returns {threads: int, sessions_linked: int}.
    """
    conn = connect()
    try:
        conn.execute("DELETE FROM anamnestic_session_threads")

        rows = conn.execute(
            """
            SELECT content_session_id, project, started_at_epoch
            FROM sdk_sessions
            WHERE instr(content_session_id, ':') = 0
            ORDER BY project, started_at_epoch
            """
        ).fetchall()

        if not rows:
            conn.commit()
            return {"threads": 0, "sessions_linked": 0}

        thread_id = 0
        thread_order = 0
        prev_project = None
        prev_epoch = None
        inserts = []

        for row in rows:
            sid = row["content_session_id"]
            project = row["project"]
            epoch = row["started_at_epoch"]

            new_thread = (
                project != prev_project
                or prev_epoch is None
                or (epoch - prev_epoch) > gap_sec
            )

            if new_thread:
                thread_id += 1
                thread_order = 0

            thread_order += 1
            inserts.append((sid, thread_id, thread_order))

            prev_project = project
            prev_epoch = epoch

        conn.executemany(
            "INSERT INTO anamnestic_session_threads"
            "(session_id, thread_id, thread_order) VALUES (?, ?, ?)",
            inserts,
        )
        conn.commit()

        solo_threads = conn.execute(
            """
            SELECT COUNT(DISTINCT thread_id) FROM anamnestic_session_threads
            GROUP BY thread_id HAVING COUNT(*) = 1
            """
        ).fetchall()

        return {
            "threads": thread_id,
            "sessions_linked": len(inserts),
            "multi_session_threads": thread_id - len(solo_threads),
        }
    finally:
        conn.close()


def get_thread(session_id: str) -> list[dict]:
    """Return all sessions in the same thread, ordered by thread_order."""
    conn = connect()
    try:
        tid = conn.execute(
            "SELECT thread_id FROM anamnestic_session_threads WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not tid:
            return []

        rows = conn.execute(
            """
            SELECT t.session_id, t.thread_order,
                   s.project, s.platform_source, s.user_prompt,
                   s.started_at, s.custom_title, s.prompt_counter
            FROM anamnestic_session_threads t
            JOIN sdk_sessions s ON s.content_session_id = t.session_id
            WHERE t.thread_id = ?
            ORDER BY t.thread_order
            """,
            (tid["thread_id"],),
        ).fetchall()
        return [
            {
                "session": r["session_id"],
                "order": r["thread_order"],
                "project": r["project"],
                "source": r["platform_source"],
                "prompt": (r["user_prompt"] or "")[:200],
                "started_at": (r["started_at"] or "")[:19],
                "title": r["custom_title"] or "",
                "prompt_count": r["prompt_counter"],
                "is_target": r["session_id"] == session_id,
            }
            for r in rows
        ]
    finally:
        conn.close()


def stats() -> dict:
    conn = connect()
    try:
        total_threads = conn.execute(
            "SELECT COUNT(DISTINCT thread_id) FROM anamnestic_session_threads"
        ).fetchone()[0]
        total_sessions = conn.execute(
            "SELECT COUNT(*) FROM anamnestic_session_threads"
        ).fetchone()[0]
        multi = conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT thread_id FROM anamnestic_session_threads
                GROUP BY thread_id HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        longest = conn.execute(
            """
            SELECT thread_id, COUNT(*) AS n
            FROM anamnestic_session_threads
            GROUP BY thread_id ORDER BY n DESC LIMIT 1
            """
        ).fetchone()
        return {
            "total_threads": total_threads,
            "sessions_threaded": total_sessions,
            "multi_session_threads": multi,
            "longest_thread": {
                "thread_id": longest["thread_id"],
                "length": longest["n"],
            } if longest else None,
        }
    finally:
        conn.close()
