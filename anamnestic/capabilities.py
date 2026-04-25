"""Runtime capability snapshots for optional retrieval backends."""

from __future__ import annotations

from typing import Any

from anamnestic.config import (
    CHROMA_COLLECTION,
    SEMANTIC_ENABLED,
    SEMANTIC_MODE,
    SEMANTIC_REQUIRED,
    semantic_dependencies_available,
    local_embed_model_ready,
)


def _embedding_counts(conn) -> dict[str, int] | None:
    if conn is None:
        return None
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS indexable,
                SUM(CASE WHEN es.turn_id IS NOT NULL THEN 1 ELSE 0 END) AS embedded
            FROM historical_turns ht
            LEFT JOIN anamnestic_embed_state es
                ON es.turn_id = ht.id AND es.collection = ?
            WHERE length(ht.text) > 15
            """,
            (CHROMA_COLLECTION,),
        ).fetchone()
    except Exception:
        return None
    indexable = int(row["indexable"] or 0)
    embedded = int(row["embedded"] or 0)
    return {
        "indexable": indexable,
        "embedded": embedded,
        "pending": max(indexable - embedded, 0),
    }


def semantic_snapshot(conn=None, include_chroma: bool = False) -> dict[str, Any]:
    """Report semantic capability without changing the user's search workflow.

    In default auto mode this avoids touching Chroma unless explicitly asked.
    SQLite/BM25 can therefore remain healthy even when semantic dependencies or
    the derived vector index are absent.
    """
    counts = _embedding_counts(conn)
    snapshot: dict[str, Any] = {
        "mode": SEMANTIC_MODE,
        "enabled": SEMANTIC_ENABLED,
        "required": SEMANTIC_REQUIRED,
        "status": "unknown",
        "reason": None,
    }
    if counts is not None:
        snapshot.update(counts)

    if not SEMANTIC_ENABLED:
        snapshot.update({
            "status": "disabled",
            "reason": "ANAMNESTIC_SEMANTIC=0",
        })
        return snapshot

    if not semantic_dependencies_available():
        snapshot.update({
            "status": "unavailable",
            "reason": "semantic dependencies are not installed",
        })
        return snapshot

    if not local_embed_model_ready():
        snapshot.update({
            "status": "not_cached",
            "reason": "embedding model cache is missing; run sync to bootstrap it",
        })
        return snapshot

    pending = snapshot.get("pending")
    if pending is not None and pending > 0:
        snapshot.update({
            "status": "pending",
            "reason": "semantic index backfill is incomplete",
        })
    else:
        snapshot.update({"status": "active", "reason": None})

    if include_chroma:
        try:
            from anamnestic.chroma_store import persistent_client

            col = persistent_client().get_collection(CHROMA_COLLECTION)
            chroma_count = col.count()
            snapshot["chroma_count"] = chroma_count
            embedded = snapshot.get("embedded")
            if embedded is not None:
                snapshot["drift_state_vs_chroma"] = embedded - chroma_count
                if embedded != chroma_count:
                    snapshot["status"] = "error"
                    snapshot["reason"] = "embed_state and Chroma counts differ"
        except Exception as exc:
            snapshot["chroma_count"] = None
            snapshot["status"] = "error" if SEMANTIC_REQUIRED else snapshot["status"]
            snapshot["reason"] = f"Chroma unavailable: {type(exc).__name__}: {exc}"

    return snapshot
