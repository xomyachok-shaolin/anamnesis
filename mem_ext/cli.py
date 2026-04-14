"""Unified CLI: mem-ext <subcommand>"""
import argparse
import json
import sys


def cmd_sync(args):
    from mem_ext.db import run_migrations
    from mem_ext.ingest.incremental import run as ingest
    from mem_ext.indexers.incremental_chroma import run as embed

    applied = run_migrations()
    if applied:
        print(f"migrations: {', '.join(applied)}")
    ing = ingest(verbose=args.verbose)
    emb = embed(verbose=args.verbose, batch_size=args.batch)
    print(json.dumps({"ingest": ing, "embed": emb}, ensure_ascii=False))


def cmd_status(args):
    from mem_ext.db import connect
    conn = connect()
    cur = conn.cursor()

    totals = {
        "sessions": cur.execute("SELECT COUNT(*) FROM sdk_sessions").fetchone()[0],
        "turns": cur.execute("SELECT COUNT(*) FROM historical_turns").fetchone()[0],
        "user_prompts": cur.execute("SELECT COUNT(*) FROM user_prompts").fetchone()[0],
    }
    embedded = cur.execute(
        "SELECT COUNT(*) FROM ext_embed_state WHERE collection='history_turns'"
    ).fetchone()[0]
    unembedded = cur.execute(
        """
        SELECT COUNT(*) FROM historical_turns ht
        LEFT JOIN ext_embed_state es ON es.turn_id = ht.id AND es.collection='history_turns'
        WHERE es.turn_id IS NULL
        """
    ).fetchone()[0]
    last_ingest = cur.execute(
        "SELECT MAX(ingested_at) FROM ext_ingest_state"
    ).fetchone()[0]
    files_tracked = cur.execute("SELECT COUNT(*) FROM ext_ingest_state").fetchone()[0]
    conn.close()

    drift = totals["turns"] - embedded
    report = {
        "totals": totals,
        "embedded": embedded,
        "unembedded": unembedded,
        "drift_turns_vs_chroma": drift,
        "files_tracked": files_tracked,
        "last_ingest": last_ingest,
        "healthy": drift == unembedded and drift >= 0,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


def cmd_search(args):
    from mem_ext.db import connect
    from mem_ext.search.hybrid import search, format_hit
    conn = connect()
    rl = args.role if args.role in ("user", "assistant") else None
    hits = search(conn, args.query, top_k=args.top_k, pool=args.pool, role=rl)
    for h in hits:
        print(format_hit(h))
        print()


def cmd_backup(args):
    from mem_ext.backup import run
    info = run()
    print(json.dumps(info, indent=2))


def cmd_eval(args):
    from mem_ext.eval.run import evaluate, load_golden
    from pathlib import Path
    golden = args.golden or str(Path(__file__).parent / "eval" / "golden.yaml")
    queries = load_golden(golden)
    r = evaluate(queries, top_k=args.top_k, mode=args.mode)
    print(f"{args.mode}: {r['total_passed']}/{r['total_queries']} "
          f"pass ({r['pass_rate']:.0%}), p@{args.top_k}={r['avg_precision_at_k']:.3f}")
    return 0 if r["total_passed"] == r["total_queries"] else 1


def build_parser():
    ap = argparse.ArgumentParser(prog="mem-ext")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="incremental ingest + embed")
    s.add_argument("--verbose", action="store_true")
    s.add_argument("--batch", type=int, default=64)
    s.set_defaults(func=cmd_sync)

    st = sub.add_parser("status", help="health + drift report")
    st.set_defaults(func=cmd_status)

    sr = sub.add_parser("search", help="hybrid search")
    sr.add_argument("query")
    sr.add_argument("--top-k", type=int, default=10)
    sr.add_argument("--pool", type=int, default=50)
    sr.add_argument("--role", default="any")
    sr.set_defaults(func=cmd_search)

    b = sub.add_parser("backup", help="safe tarball of DB + Chroma")
    b.set_defaults(func=cmd_backup)

    e = sub.add_parser("eval", help="run golden eval")
    e.add_argument("--mode", choices=["semantic", "hybrid"], default="hybrid")
    e.add_argument("--top-k", type=int, default=10)
    e.add_argument("--golden", default=None)
    e.set_defaults(func=cmd_eval)

    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()
    rc = args.func(args)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
