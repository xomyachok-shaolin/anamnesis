"""Hybrid search: BM25 (SQLite FTS5) + semantic (Chroma) → Reciprocal Rank Fusion.

RRF formula:  score(d) = Σ_r  1 / (K + rank_r(d))
where K is a constant (60 per Cormack et al. 2009), r iterates over rankers.
"""
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Iterable

from anamnestic.config import (
    CHROMA_COLLECTION,
    CHROMA_DIR,
    EMBED_MODEL,
    FASTEMBED_CACHE,
    RRF_K,
    local_embed_model_ready,
)

COLL = CHROMA_COLLECTION

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
    return TextEmbedding(model_name=EMBED_MODEL, cache_dir=FASTEMBED_CACHE)


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
    rerank_score: float | None = None
    temporal_rank: int | None = None
    hit_type: str = "turn"  # "turn" or "summary"
    graph_rank: int | None = None


@dataclass
class SearchDiagnostics:
    """Per-channel statistics from a search run."""
    bm25_hits: int = 0
    semantic_hits: int = 0
    temporal_hits: int = 0
    graph_hits: int = 0
    summary_hits: int = 0
    fused_total: int = 0
    reranked: bool = False

    def to_dict(self) -> dict:
        return {
            "channels": {
                "bm25": self.bm25_hits,
                "semantic": self.semantic_hits,
                "temporal": self.temporal_hits,
                "graph": self.graph_hits,
                "summaries": self.summary_hits,
            },
            "fused_total": self.fused_total,
            "reranked": self.reranked,
        }


class SearchResult(list):
    """List of Hits with optional diagnostics. Behaves as a plain list."""
    diagnostics: SearchDiagnostics | None = None


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


def _bm25_summaries(conn, q: str, k: int) -> list[Hit]:
    """BM25 search over session_summaries_fts."""
    fts_expr = _fts_query(q)
    if not fts_expr:
        return []
    try:
        rows = conn.execute(
            """
            SELECT ss.id, ss.summary_text, ss.content_session_id,
                   ss.project, ss.memory_session_id,
                   ss.created_at,
                   bm25(session_summaries_fts) AS score
            FROM session_summaries_fts
            JOIN session_summaries ss ON ss.id = session_summaries_fts.rowid
            WHERE session_summaries_fts MATCH ?
            ORDER BY score ASC
            LIMIT ?
            """,
            (fts_expr, k),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    hits = []
    for rank, row in enumerate(rows, 1):
        # Use negative ID to distinguish from turn IDs
        hits.append(
            Hit(
                turn_id=-row["id"],
                text=row["summary_text"] or "",
                meta={
                    "session": row["content_session_id"] or "",
                    "turn": 0,
                    "role": "summary",
                    "timestamp": row["created_at"] or "",
                    "source": "summary",
                    "title": "",
                    "project": row["project"] or "",
                },
                bm25_rank=rank,
                hit_type="summary",
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
) -> tuple[list[Hit], SearchDiagnostics] | list[Hit]:
    """Hybrid search. Returns fused top-k Hits and diagnostics.

    Returns (hits, diagnostics) tuple. For backwards compatibility,
    callers that unpack as a list still work — use search_with_diagnostics()
    to get the tuple explicitly.
    """
    from anamnestic.config import TEMPORAL_WEIGHT

    diag = SearchDiagnostics()

    bm25_hits = _bm25(conn, query, pool)
    summary_hits_list = _bm25_summaries(conn, query, pool // 5)
    diag.bm25_hits = len(bm25_hits)
    diag.summary_hits = len(summary_hits_list)
    bm25_hits.extend(summary_hits_list)
    if local_embed_model_ready():
        try:
            emb = _embedder()
            col = _chroma_col()
            sem_hits = _semantic(emb, col, query, pool, role=role)
        except Exception:
            sem_hits = []
    else:
        sem_hits = []
    diag.semantic_hits = len(sem_hits)

    # Temporal channel — only fires when query has time expressions
    from anamnestic.search.temporal import detect_time_range, temporal_search

    time_range = detect_time_range(query)
    temp_hits = temporal_search(conn, time_range, pool) if time_range else []
    diag.temporal_hits = len(temp_hits)

    # Graph channel — entity co-occurrence traversal
    from anamnestic.config import GRAPH_WEIGHT, GRAPH_MAX_HOPS
    from anamnestic.entities import extract as extract_entities

    graph_hits: list[Hit] = []
    if GRAPH_WEIGHT > 0:
        query_entities = [val for _, val in extract_entities(query)]
        if query_entities:
            from anamnestic.graph import graph_search
            try:
                graph_hits = graph_search(conn, query_entities, max_hops=GRAPH_MAX_HOPS, k=pool)
            except Exception:
                graph_hits = []
    diag.graph_hits = len(graph_hits)

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
    for h in temp_hits:
        if h.turn_id in by_id:
            existing = by_id[h.turn_id]
            existing.temporal_rank = h.temporal_rank
            existing.rrf_score += TEMPORAL_WEIGHT / (RRF_K + h.temporal_rank)
        else:
            h.rrf_score = TEMPORAL_WEIGHT / (RRF_K + h.temporal_rank)
            by_id[h.turn_id] = h
    for h in graph_hits:
        if h.turn_id in by_id:
            existing = by_id[h.turn_id]
            existing.graph_rank = h.graph_rank
            existing.rrf_score += GRAPH_WEIGHT / (RRF_K + h.graph_rank)
        else:
            h.rrf_score = GRAPH_WEIGHT / (RRF_K + h.graph_rank)
            by_id[h.turn_id] = h

    # Apply importance weighting
    from anamnestic.config import IMPORTANCE_WEIGHT

    if IMPORTANCE_WEIGHT > 0 and by_id:
        turn_ids = [tid for tid in by_id if tid > 0]  # skip summary hits (negative IDs)
        placeholders = ",".join("?" * len(turn_ids)) if turn_ids else "0"
        imp_rows = conn.execute(
            f"SELECT id, COALESCE(importance, 0.5) FROM historical_turns WHERE id IN ({placeholders})",
            turn_ids,
        ).fetchall()
        imp_map = {r[0]: r[1] for r in imp_rows}
        for h in by_id.values():
            imp = imp_map.get(h.turn_id, 0.5)
            h.rrf_score *= 1.0 + IMPORTANCE_WEIGHT * (imp - 0.5)

    # Apply temporal decay — recent results rank higher
    from anamnestic.config import DECAY_ENABLED, DECAY_HALF_LIFE_DAYS

    if DECAY_ENABLED and by_id:
        from anamnestic.decay import decay_factor
        for h in by_id.values():
            ts = h.meta.get("timestamp", "")
            df = decay_factor(ts, DECAY_HALF_LIFE_DAYS)
            h.rrf_score *= 0.5 + 0.5 * df  # floor at 50% of original score

    fused = sorted(by_id.values(), key=lambda h: h.rrf_score, reverse=True)
    diag.fused_total = len(fused)

    # Cross-encoder reranking (final precision step)
    from anamnestic.config import RERANK_ENABLED, RERANK_TOP_N

    if RERANK_ENABLED and len(fused) > top_k:
        from anamnestic.search.rerank import rerank
        fused = rerank(query, fused[:RERANK_TOP_N], top_k)
        diag.reranked = True
    else:
        fused = fused[:top_k]

    result = SearchResult(fused)
    result.diagnostics = diag
    return result


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
    if h.temporal_rank:
        ranks.append(f"T{h.temporal_rank}")
    if h.graph_rank:
        ranks.append(f"G{h.graph_rank}")
    if h.rerank_score is not None:
        ranks.append(f"R{h.rerank_score:.2f}")
    return (
        f"[rrf={h.rrf_score:.4f} {'+'.join(ranks) or '-'}] "
        f"{ts} [{role}/{src}] {title}\n"
        f"        {snippet}\n"
        f"        sess={(h.meta.get('session') or '')[:12]} "
        f"turn#{h.meta.get('turn')}"
    )


if __name__ == "__main__":
    import sys
    from anamnestic.db import connect

    if len(sys.argv) < 2:
        print("Usage: python -m anamnestic.search.hybrid <query> [top_k] [role]")
        sys.exit(1)
    q = sys.argv[1]
    tk = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    rl = sys.argv[3] if len(sys.argv) > 3 else None
    conn = connect()
    for h in search(conn, q, top_k=tk, role=rl):
        print(format_hit(h))
        print()
