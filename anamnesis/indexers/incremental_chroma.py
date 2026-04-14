"""Incremental embedding: embed historical_turns rows missing from Chroma.

Uses ext_embed_state to skip already-embedded turns. Safe to run on a timer
after ingest.incremental.
"""
import os
import sys
import time

from mem_ext.db import connect

DATA = os.path.expanduser("~/.claude-mem")
CHROMA_DIR = f"{DATA}/semantic-chroma"
COLL = "history_turns"
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def _embedder():
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=MODEL, cache_dir=f"{DATA}/fastembed-models")


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
    col = _chroma_col()

    # Turns not yet in ext_embed_state for this collection
    q = """
        SELECT ht.id, ht.content_session_id, ht.turn_number, ht.role, ht.text,
               ht.timestamp, ht.platform_source,
               s.project, s.custom_title
        FROM historical_turns ht
        LEFT JOIN ext_embed_state es ON es.turn_id = ht.id AND es.collection = ?
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

    emb = _embedder()
    # warmup
    list(emb.embed(["init"]))

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
            "INSERT OR REPLACE INTO ext_embed_state(turn_id, collection, embedded_at) "
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
