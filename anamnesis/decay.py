"""Time-based decay and optional archival for search relevance.

Decay: exponential factor applied to RRF scores during search — recent
results rank higher. Half-life is configurable (default 90 days).

Archive: opt-in mechanism to move old, low-importance turns to a separate
table, reducing index size. Only archives turns whose sessions already
have summaries (safety invariant).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

log = logging.getLogger(__name__)


def decay_factor(timestamp_str: str | None, half_life_days: int = 90) -> float:
    """Compute exponential decay factor (0.0–1.0) based on turn age.

    Returns 1.0 for brand-new turns, ~0.5 at half_life, floors at 0.1.
    """
    if not timestamp_str:
        return 0.5
    try:
        ts = datetime.fromisoformat(timestamp_str[:19])
        age_days = (datetime.now() - ts).total_seconds() / 86400
        if age_days < 0:
            return 1.0
        raw = math.exp(-0.693 * age_days / half_life_days)
        return max(raw, 0.1)
    except (ValueError, TypeError):
        return 0.5


def archive_old_turns(
    conn,
    age_days: int = 365,
    importance_threshold: float = 0.3,
    batch_size: int = 1000,
) -> dict:
    """Move old, low-importance turns to archive table.

    Safety: only archives turns from sessions that already have a summary
    in anamnesis_summary_state.
    """
    cutoff = (datetime.now() - timedelta(days=age_days)).isoformat()

    # Find candidate turns
    rows = conn.execute(
        """
        SELECT ht.id, ht.content_session_id, ht.turn_number, ht.role,
               ht.text, ht.timestamp, ht.platform_source, ht.importance
        FROM historical_turns ht
        JOIN anamnesis_summary_state ss
            ON ss.content_session_id = ht.content_session_id
        WHERE ht.timestamp < ?
          AND COALESCE(ht.importance, 0.5) < ?
        ORDER BY ht.timestamp ASC
        LIMIT ?
        """,
        (cutoff, importance_threshold, batch_size),
    ).fetchall()

    if not rows:
        return {"archived": 0}

    archived = 0
    ids_to_delete = []

    for row in rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO anamnesis_archived_turns
              (id, content_session_id, turn_number, role, text,
               timestamp, platform_source, importance, archive_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'age+low_importance')
            """,
            (
                row["id"], row["content_session_id"], row["turn_number"],
                row["role"], row["text"], row["timestamp"],
                row["platform_source"], row["importance"],
            ),
        )
        ids_to_delete.append(row["id"])
        archived += 1

    # Delete from main table (triggers will clean FTS)
    if ids_to_delete:
        placeholders = ",".join("?" * len(ids_to_delete))
        conn.execute(
            f"DELETE FROM historical_turns WHERE id IN ({placeholders})",
            ids_to_delete,
        )
        # Clean embed state
        conn.execute(
            f"DELETE FROM anamnesis_embed_state WHERE turn_id IN ({placeholders})",
            ids_to_delete,
        )

    conn.commit()
    log.info("archived %d turns older than %s with importance < %.2f",
             archived, cutoff, importance_threshold)
    return {"archived": archived, "cutoff": cutoff}
