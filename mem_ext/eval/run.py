#!/usr/bin/env python3
"""
Evaluate semantic index against golden.yaml.

Usage:
  python -m mem_ext.eval.run [--top-k 10] [--role any|user|assistant]

Prints per-query precision and overall score.
Exits non-zero if any query fails its min_hits threshold.
"""
import argparse
import os
import sys
from pathlib import Path

DATA = os.path.expanduser("~/.claude-mem")
CHROMA_DIR = f"{DATA}/semantic-chroma"
COLL = "history_turns"
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def load_golden(path):
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)["queries"]


def get_embedder():
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=MODEL, cache_dir=f"{DATA}/fastembed-models")


def get_collection():
    import chromadb
    return chromadb.PersistentClient(path=CHROMA_DIR).get_collection(COLL)


def evaluate(queries, top_k=10, role="any"):
    emb = get_embedder()
    col = get_collection()

    total_passed = 0
    total_queries = len(queries)
    hits_at_k = []
    results_detail = []

    for q in queries:
        query = q["query"]
        kws = [k.lower() for k in q["any_keywords"]]
        min_hits = q.get("min_hits", 1)
        k = q.get("top_k", top_k)
        r = q.get("role", role)

        vec = list(emb.embed([query]))[0].tolist()
        where = {"role": r} if r in ("user", "assistant") else None
        res = col.query(query_embeddings=[vec], n_results=k, where=where)
        docs = res["documents"][0] if res["documents"] else []

        matches = 0
        matched_ranks = []
        for rank, d in enumerate(docs, 1):
            dl = d.lower()
            if any(kw in dl for kw in kws):
                matches += 1
                matched_ranks.append(rank)

        passed = matches >= min_hits
        if passed:
            total_passed += 1
        hits_at_k.append(matches / k)
        results_detail.append(
            {
                "query": query,
                "matches": matches,
                "min_hits": min_hits,
                "top_k": k,
                "passed": passed,
                "matched_ranks": matched_ranks,
            }
        )

    return {
        "total_queries": total_queries,
        "total_passed": total_passed,
        "pass_rate": total_passed / total_queries if total_queries else 0,
        "avg_precision_at_k": sum(hits_at_k) / len(hits_at_k) if hits_at_k else 0,
        "details": results_detail,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--role", default="any")
    ap.add_argument("--golden", default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    golden_path = args.golden or str(Path(__file__).parent / "golden.yaml")
    queries = load_golden(golden_path)
    result = evaluate(queries, top_k=args.top_k, role=args.role)

    print(f"=== Eval: {result['total_passed']}/{result['total_queries']} passed "
          f"({result['pass_rate']:.0%}), avg precision@{args.top_k} = {result['avg_precision_at_k']:.3f}")
    print()
    for d in result["details"]:
        mark = "✓" if d["passed"] else "✗"
        ranks = ",".join(map(str, d["matched_ranks"])) if d["matched_ranks"] else "-"
        print(f"  {mark} {d['matches']}/{d['top_k']} (min={d['min_hits']}) ranks=[{ranks}] :: {d['query'][:70]}")

    sys.exit(0 if result["total_passed"] == result["total_queries"] else 1)


if __name__ == "__main__":
    main()
