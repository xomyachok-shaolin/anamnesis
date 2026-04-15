"""Incremental embedding: embed historical_turns rows missing from Chroma.

Uses anamnesis_embed_state to skip already-embedded turns. Safe to run on a timer
after ingest.incremental.
"""
import time

from anamnesis.config import (
    CHROMA_COLLECTION,
    CHROMA_DIR,
    EMBED_MODEL,
    FASTEMBED_CACHE,
    local_embed_model_ready,
)
from anamnesis.db import connect

COLL = CHROMA_COLLECTION


def _embedder():
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=EMBED_MODEL, cache_dir=FASTEMBED_CACHE)


def _chroma_col():
    import chromadb
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        return client.get_collection(COLL)
    except Exception:
        return client.create_collection(COLL, metadata={"hnsw:space": "cosine"})


def run(batch_size=64, limit=None, verbose=False):
    conn = connect()
    cur = conn.cursor()

    # Turns not yet in anamnesis_embed_state for this collection
    q = """
        SELECT ht.id, ht.content_session_id, ht.turn_number, ht.role, ht.text,
               ht.timestamp, ht.platform_source,
               s.project, s.custom_title
        FROM historical_turns ht
        LEFT JOIN anamnesis_embed_state es ON es.turn_id = ht.id AND es.collection = ?
        LEFT JOIN sdk_sessions s ON s.content_session_id = ht.content_session_id
        WHERE es.turn_id IS NULL AND length(ht.text) > 15
        ORDER BY ht.id
    """
    if limit:
        q += f" LIMIT {int(limit)}"

    rows = cur.execute(q, (COLL,)).fetchall()
    if not rows:
        conn.close()
        return {"embedded": 0, "elapsed": 0}

    if not local_embed_model_ready():
        conn.close()
        return {
            "embedded": 0,
            "elapsed": 0,
            "error": "embedding model cache is missing",
        }

    try:
        col = _chroma_col()
        emb = _embedder()
        # warmup
        list(emb.embed(["init"]))
    except Exception as exc:
        conn.close()
        return {
            "embedded": 0,
            "elapsed": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }

    t0 = time.time()
    buf_docs, buf_ids, buf_metas, buf_turn_ids = [], [], [], []
    embedded = 0

    def flush():
        nonlocal embedded, buf_docs, buf_ids, buf_metas, buf_turn_ids
        if not buf_docs:
            return
        vectors = list(emb.embed(buf_docs))
        col.add(
            ids=buf_ids,
            documents=buf_docs,
            metadatas=buf_metas,
            embeddings=[v.tolist() for v in vectors],
        )
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        cur.executemany(
            "INSERT OR REPLACE INTO anamnesis_embed_state(turn_id, collection, embedded_at) "
            "VALUES (?, ?, ?)",
            [(tid, COLL, now) for tid in buf_turn_ids],
        )
        embedded += len(buf_docs)
        buf_docs, buf_ids, buf_metas, buf_turn_ids = [], [], [], []

    for r in rows:
        txt = r["text"][:2000].strip()
        if not txt:
            continue
        buf_docs.append(txt)
        buf_ids.append(f"ht-{r['id']}")
        buf_metas.append({
            "session": r["content_session_id"] or "",
            "project": r["project"] or "",
            "title": r["custom_title"] or "",
            "timestamp": r["timestamp"] or "",
            "turn": r["turn_number"] or 0,
            "role": r["role"] or "",
            "source": r["platform_source"] or "",
        })
        buf_turn_ids.append(r["id"])
        if len(buf_docs) >= batch_size:
            flush()
            if verbose and embedded % 1024 == 0:
                print(f"  embedded {embedded}/{len(rows)}")
    flush()
    conn.commit()
    conn.close()
    return {"embedded": embedded, "elapsed": round(time.time() - t0, 1)}


if __name__ == "__main__":
    stats = run(verbose=True)
    print(f"Chroma: embedded={stats['embedded']} in {stats['elapsed']}s")
