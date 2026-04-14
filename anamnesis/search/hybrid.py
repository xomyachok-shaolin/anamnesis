"""Hybrid search: BM25 (SQLite FTS5) + semantic (Chroma) → Reciprocal Rank Fusion.

RRF formula:  score(d) = Σ_r  1 / (K + rank_r(d))
where K is a constant (60 per Cormack et al. 2009), r iterates over rankers.
"""
import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Iterable

DATA = os.path.expanduser("~/.claude-mem")
CHROMA_DIR = f"{DATA}/semantic-chroma"
COLL = "history_turns"
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
RRF_K = 60

# FTS5 unicode61 splits on non-alphanumeric; quoting a term with "." etc makes it a phrase.
_token_re = re.compile(r"[\w]+", re.UNICODE)
_phrase_re = re.compile(r"""["']?[\w./:-]{3,}["']?""", re.UNICODE)
_fts_reserved = {"AND", "OR", "NOT", "NEAR"}


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _fts_tokens(q: str) -> list[str]:
    return _unique(
        t
        for t in _token_re.findall(q)
        if len(t) >= 2 and t.upper() not in _fts_reserved
    )


def _quote_phrase(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _fts_phrases(q: str) -> list[str]:
    phrases = []
    for raw in _phrase_re.findall(q):
        term = raw.strip("\"'")
        if len(term) < 3 or term.upper() in _fts_reserved:
            continue
        if not any(ch in term for ch in "./:-"):
            continue
        phrases.append(term)
    return _unique(phrases)


def _fts_query(q: str) -> str | None:
    """Turn a free-form query into an FTS5 MATCH expression.

    Strategy: tokenize into words; keep words with length ≥ 2; OR them together;
    also include original phrase as "..." if it contains punctuation indicative
    of an exact token (dots, dashes, colons, slashes) — so file paths,
    identifiers, IPs and CVE IDs stay searchable as phrases.
    """
    tokens = _fts_tokens(q)
    parts = []
    if tokens:
        # OR-combine single tokens
        parts.append(" OR ".join(tokens))
    # phrase form for strings containing punctuation
    for ph in _fts_phrases(q):
        parts.append(_quote_phrase(ph))
    parts = _unique(parts)
    return " OR ".join(parts) if parts else None


def _embedder():
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=MODEL, cache_dir=f"{DATA}/fastembed-models")


def _chroma_col():
    import chromadb
    return chromadb.PersistentClient(path=CHROMA_DIR).get_collection(COLL)


@dataclass
class Hit:
    turn_id: int
    text: str
    meta: dict = field(default_factory=dict)
    bm25_rank: int | None = None
    sem_rank: int | None = None
    rrf_score: float = 0.0


def _run_bm25_query(conn, fts_expr: str, k: int):
    return conn.execute(
        """
        SELECT ht.id, ht.text, ht.content_session_id, ht.turn_number,
               ht.role, ht.timestamp, ht.platform_source,
               s.custom_title, s.project,
               bm25(historical_turns_fts) AS score
        FROM historical_turns_fts
        JOIN historical_turns ht ON ht.id = historical_turns_fts.rowid
        LEFT JOIN sdk_sessions s ON s.content_session_id = ht.content_session_id
        WHERE historical_turns_fts MATCH ?
        ORDER BY score ASC
        LIMIT ?
        """,
        (fts_expr, k),
    ).fetchall()


def _is_fts_syntax_error(exc: sqlite3.Error) -> bool:
    msg = str(exc).lower()
    return "fts5" in msg and "syntax error" in msg


def _bm25(conn, q: str, k: int) -> list[Hit]:
    fts_expr = _fts_query(q)
    if not fts_expr:
        return []
    try:
        rows = _run_bm25_query(conn, fts_expr, k)
    except sqlite3.OperationalError as exc:
        if not _is_fts_syntax_error(exc):
            raise
        # FTS syntax errors: fall back to sanitized raw tokens only.
        safe = " OR ".join(_fts_tokens(q))
        if not safe or safe == fts_expr:
            return []
        try:
            rows = _run_bm25_query(conn, safe, k)
        except sqlite3.OperationalError as fallback_exc:
            if _is_fts_syntax_error(fallback_exc):
                return []
            raise

    hits = []
    for rank, row in enumerate(rows, 1):
        hits.append(
            Hit(
                turn_id=row["id"],
                text=row["text"],
                meta={
                    "session": row["content_session_id"],
                    "turn": row["turn_number"],
                    "role": row["role"],
                    "timestamp": row["timestamp"],
                    "source": row["platform_source"],
                    "title": row["custom_title"] or "",
                    "project": row["project"] or "",
                },
                bm25_rank=rank,
            )
        )
    return hits


def _semantic(emb, col, q: str, k: int, role: str | None = None) -> list[Hit]:
    vec = list(emb.embed([q]))[0].tolist()
    where = {"role": role} if role in ("user", "assistant") else None
    res = col.query(query_embeddings=[vec], n_results=k, where=where)
    if not res["ids"] or not res["ids"][0]:
        return []
    hits = []
    for rank, (hid, doc, md) in enumerate(
        zip(res["ids"][0], res["documents"][0], res["metadatas"][0]),
        1,
    ):
        tid = int(hid.split("-")[-1]) if hid.startswith("ht-") else -1
        hits.append(
            Hit(
                turn_id=tid,
                text=doc,
                meta={
                    "session": md.get("session", ""),
                    "turn": md.get("turn", 0),
                    "role": md.get("role", ""),
                    "timestamp": md.get("timestamp", ""),
                    "source": md.get("source", ""),
                    "title": md.get("title", ""),
                    "project": md.get("project", ""),
                },
                sem_rank=rank,
            )
        )
    return hits


def search(
    conn,
    query: str,
    top_k: int = 10,
    pool: int = 50,
    role: str | None = None,
    bm25_weight: float = 1.0,
    sem_weight: float = 1.0,
) -> list[Hit]:
    """Hybrid search. Returns fused top-k Hits."""
    bm25_hits = _bm25(conn, query, pool)
    emb = _embedder()
    col = _chroma_col()
    sem_hits = _semantic(emb, col, query, pool, role=role)

    by_id: dict[int, Hit] = {}
    for h in bm25_hits:
        by_id[h.turn_id] = h
        h.rrf_score += bm25_weight / (RRF_K + h.bm25_rank)
    for h in sem_hits:
        if h.turn_id in by_id:
            existing = by_id[h.turn_id]
            existing.sem_rank = h.sem_rank
            existing.rrf_score += sem_weight / (RRF_K + h.sem_rank)
        else:
            h.rrf_score = sem_weight / (RRF_K + h.sem_rank)
            by_id[h.turn_id] = h

    fused = sorted(by_id.values(), key=lambda h: h.rrf_score, reverse=True)
    return fused[:top_k]


def format_hit(h: Hit) -> str:
    ts = (h.meta.get("timestamp") or "")[:19]
    role = (h.meta.get("role") or "?")[0].upper()
    src = h.meta.get("source") or "?"
    title = (h.meta.get("title") or "(no title)")[:45]
    snippet = (h.text or "")[:200].replace("\n", " ")
    ranks = []
    if h.bm25_rank:
        ranks.append(f"B{h.bm25_rank}")
    if h.sem_rank:
        ranks.append(f"S{h.sem_rank}")
    return (
        f"[rrf={h.rrf_score:.4f} {'+'.join(ranks) or '-'}] "
        f"{ts} [{role}/{src}] {title}\n"
        f"        {snippet}\n"
        f"        sess={(h.meta.get('session') or '')[:12]} "
        f"turn#{h.meta.get('turn')}"
    )


if __name__ == "__main__":
    import sys
    from anamnesis.db import connect

    if len(sys.argv) < 2:
        print("Usage: python -m anamnesis.search.hybrid <query> [top_k] [role]")
        sys.exit(1)
    q = sys.argv[1]
    tk = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    rl = sys.argv[3] if len(sys.argv) > 3 else None
    conn = connect()
    for h in search(conn, q, top_k=tk, role=rl):
        print(format_hit(h))
        print()
