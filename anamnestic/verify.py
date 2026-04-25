"""Integrity and consistency checks."""
from anamnestic.config import CHROMA_COLLECTION, SEMANTIC_ENABLED, SEMANTIC_REQUIRED
from anamnestic.db import connect


def _chroma_available() -> bool:
    if not SEMANTIC_ENABLED:
        return False
    try:
        import chromadb  # noqa: F401
        return True
    except ImportError:
        return False


def _chroma_count() -> int | None:
    if not SEMANTIC_ENABLED:
        return None
    try:
        from anamnestic.chroma_store import persistent_client

        client = persistent_client()
        col = client.get_collection(CHROMA_COLLECTION)
        return col.count()
    except Exception as e:
        return None


def run() -> dict:
    conn = connect()
    cur = conn.cursor()

    issues = []
    checks = {}

    # 1. SQLite integrity
    row = cur.execute("PRAGMA integrity_check").fetchone()
    checks["sqlite_integrity"] = row[0] if row else "unknown"
    if checks["sqlite_integrity"] != "ok":
        issues.append(f"sqlite integrity_check: {checks['sqlite_integrity']}")

    # 2. FTS integrity
    try:
        cur.execute("INSERT INTO historical_turns_fts(historical_turns_fts) VALUES('integrity-check')")
        checks["fts_integrity"] = "ok"
    except Exception as e:
        checks["fts_integrity"] = f"error: {e}"
        issues.append(f"FTS integrity: {e}")

    # 3. Counts
    turns = cur.execute("SELECT COUNT(*) FROM historical_turns").fetchone()[0]
    embedded = cur.execute(
        "SELECT COUNT(*) FROM anamnestic_embed_state WHERE collection=?",
        (CHROMA_COLLECTION,),
    ).fetchone()[0]
    from anamnestic.capabilities import semantic_snapshot

    semantic = semantic_snapshot(conn, include_chroma=SEMANTIC_REQUIRED)
    chroma = semantic.get("chroma_count")
    checks["semantic"] = semantic

    checks["turns"] = turns
    checks["embed_state_rows"] = embedded
    checks["chroma_count"] = chroma

    # 4. Drift: embed_state vs chroma
    if SEMANTIC_REQUIRED and chroma is None and _chroma_available():
        issues.append("chroma collection unreachable")
    elif chroma is not None and chroma != embedded:
        issues.append(
            f"drift: embed_state has {embedded} rows, Chroma has {chroma}"
        )
    checks["drift_state_vs_chroma"] = (
        embedded - chroma if chroma is not None else None
    )

    # 5. Orphaned embed_state rows (turn was deleted but embed_state remains)
    orphan_embed = cur.execute(
        """
        SELECT COUNT(*) FROM anamnestic_embed_state es
        LEFT JOIN historical_turns ht ON ht.id = es.turn_id
        WHERE ht.id IS NULL
        """
    ).fetchone()[0]
    checks["orphan_embed_state"] = orphan_embed
    if orphan_embed:
        issues.append(f"{orphan_embed} orphaned anamnestic_embed_state rows")

    # 6. Orphaned user_prompts (missing sdk_sessions parent)
    orphan_prompts = cur.execute(
        """
        SELECT COUNT(*) FROM user_prompts up
        LEFT JOIN sdk_sessions s ON s.content_session_id = up.content_session_id
        WHERE s.id IS NULL
        """
    ).fetchone()[0]
    checks["orphan_user_prompts"] = orphan_prompts
    if orphan_prompts:
        issues.append(f"{orphan_prompts} orphaned user_prompts rows")

    # 7. Missing embeddings for long-enough turns
    missing_embed = cur.execute(
        """
        SELECT COUNT(*) FROM historical_turns ht
        LEFT JOIN anamnestic_embed_state es ON es.turn_id = ht.id AND es.collection = ?
        WHERE es.turn_id IS NULL AND length(ht.text) > 15
        """,
        (CHROMA_COLLECTION,),
    ).fetchone()[0]
    checks["missing_embeddings"] = missing_embed
    if SEMANTIC_REQUIRED and missing_embed > 100 and _chroma_available():
        issues.append(f"{missing_embed} turns pending embedding")

    conn.close()

    return {
        "healthy": len(issues) == 0,
        "issues": issues,
        "checks": checks,
    }


if __name__ == "__main__":
    import json
    report = run()
    print(json.dumps(report, indent=2, ensure_ascii=False))
