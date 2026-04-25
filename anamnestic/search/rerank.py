"""Cross-encoder reranking. Lazy-loads ONNX model on first call.

Reranks the top-N RRF candidates for higher precision.
Falls back gracefully if fastembed cross-encoder is unavailable.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anamnestic.search.hybrid import Hit

log = logging.getLogger(__name__)

_reranker = None
_reranker_failed = False


def _get_reranker():
    global _reranker, _reranker_failed
    if _reranker is not None:
        return _reranker
    if _reranker_failed:
        return None
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        from anamnestic.config import RERANK_MODEL, FASTEMBED_CACHE

        _reranker = TextCrossEncoder(model_name=RERANK_MODEL, cache_dir=FASTEMBED_CACHE)
        return _reranker
    except Exception as exc:
        log.warning("cross-encoder unavailable, reranking disabled: %s", exc)
        _reranker_failed = True
        return None


def rerank(query: str, hits: list[Hit], top_k: int) -> list[Hit]:
    """Rerank hits using cross-encoder. Returns top_k best."""
    if not hits:
        return hits

    reranker = _get_reranker()
    if reranker is None:
        return hits[:top_k]

    documents = [h.text[:512] for h in hits]
    try:
        results = list(reranker.rerank(query, documents, top_k=min(top_k, len(hits))))
    except Exception as exc:
        log.warning("rerank failed, returning RRF order: %s", exc)
        return hits[:top_k]

    reranked = []
    for i, entry in enumerate(results):
        try:
            if hasattr(entry, "index"):
                idx, sc = entry.index, entry.score
            elif isinstance(entry, dict):
                idx, sc = entry["index"], entry["score"]
            elif isinstance(entry, (int, float)):
                # Some fastembed versions return raw scores; use enumeration order
                idx, sc = i, float(entry)
            else:
                idx, sc = i, float(entry)
        except (KeyError, TypeError, ValueError):
            continue
        if idx >= len(hits):
            continue
        h = hits[idx]
        h.rerank_score = sc
        reranked.append(h)

    return reranked[:top_k]
