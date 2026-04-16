"""Unified CLI: anamnesis <subcommand>"""
import argparse
import json
import sys

from anamnesis.audit import audited, write_health, recent
from anamnesis.db import connect


def _wal_checkpoint():
    conn = connect()
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def _compute_status() -> dict:
    conn = connect()
    cur = conn.cursor()
    totals = {
        "sessions": cur.execute("SELECT COUNT(*) FROM sdk_sessions").fetchone()[0],
        "turns": cur.execute("SELECT COUNT(*) FROM historical_turns").fetchone()[0],
        "user_prompts": cur.execute("SELECT COUNT(*) FROM user_prompts").fetchone()[0],
    }
    embedded = cur.execute(
        "SELECT COUNT(*) FROM anamnesis_embed_state WHERE collection='history_turns'"
    ).fetchone()[0]
    unembedded = cur.execute(
        """
        SELECT COUNT(*) FROM historical_turns ht
        LEFT JOIN anamnesis_embed_state es ON es.turn_id = ht.id AND es.collection='history_turns'
        WHERE es.turn_id IS NULL
        """
    ).fetchone()[0]
    last_ingest = cur.execute("SELECT MAX(ingested_at) FROM anamnesis_ingest_state").fetchone()[0]
    files_tracked = cur.execute("SELECT COUNT(*) FROM anamnesis_ingest_state").fetchone()[0]
    recent_audit = recent(5)
    conn.close()

    drift = totals["turns"] - embedded
    return {
        "totals": totals,
        "embedded": embedded,
        "unembedded": unembedded,
        "drift_turns_vs_chroma": drift,
        "files_tracked": files_tracked,
        "last_ingest": last_ingest,
        "healthy": drift == unembedded and drift >= 0,
        "recent_audit": recent_audit,
    }


def cmd_sync(args):
    from anamnesis.db import run_migrations
    from anamnesis.ingest.incremental import run as ingest
    from anamnesis.indexers.incremental_chroma import run as embed

    applied = run_migrations()
    if applied:
        print(f"migrations: {', '.join(applied)}")

    from anamnesis.entities import backfill as entity_backfill
    from anamnesis.threading import compute as thread_compute

    with audited("sync") as details:
        ing = ingest(verbose=args.verbose)
        emb = embed(verbose=args.verbose, batch_size=args.batch)
        ent = entity_backfill()
        thr = thread_compute()
        _wal_checkpoint()
        details.update({
            "ingest": ing,
            "embed": emb,
            "entities": ent,
            "threads": thr,
            "_status": "ok" if ing["errors"] == 0 and "error" not in emb else "warn",
        })

    snapshot = _compute_status()
    snapshot["last_sync"] = {"ingest": ing, "embed": emb, "entities": ent, "threads": thr}
    write_health(snapshot)
    print(json.dumps({"ingest": ing, "embed": emb, "entities": ent, "threads": thr}, ensure_ascii=False))


def cmd_status(args):
    snapshot = _compute_status()
    print(json.dumps(snapshot, indent=2, ensure_ascii=False, default=str))


def cmd_search(args):
    from anamnesis.search.hybrid import search, format_hit
    conn = connect()
    rl = args.role if args.role in ("user", "assistant") else None
    hits = search(conn, args.query, top_k=args.top_k, pool=args.pool, role=rl)
    for h in hits:
        print(format_hit(h))
        print()


def cmd_backup(args):
    from anamnesis.backup import run
    with audited("backup") as details:
        info = run()
        details.update(info)
    print(json.dumps(info, indent=2))


def cmd_restore(args):
    from anamnesis.restore import run
    with audited("restore") as details:
        info = run(args.tarball, force=args.force)
        details.update(info)
    print(json.dumps(info, indent=2, ensure_ascii=False))


def cmd_verify(args):
    from anamnesis.verify import run
    with audited("verify") as details:
        report = run()
        details.update({
            "healthy": report["healthy"],
            "issues_count": len(report["issues"]),
            "_status": "ok" if report["healthy"] else "warn",
        })
    # Make the latest verification visible without invoking Python:
    # health.json is the canonical snapshot for external monitors (conky, bar, cron alerts).
    snapshot = _compute_status()
    snapshot["last_verify"] = report
    write_health(snapshot)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["healthy"] else 1


def cmd_audit(args):
    rows = recent(args.limit)
    for r in rows:
        d = r["details"] or {}
        compact = {k: v for k, v in d.items() if k not in ("_status",)}
        print(f"{r['at']} [{r['status']}] {r['action']} "
              f"({r['duration_sec']}s) {json.dumps(compact, ensure_ascii=False)[:150]}")


def cmd_cross_sync(args):
    from anamnesis.sync.cross import run as run_cross
    out = run_cross(peers=args.peer or None, verbose=args.verbose)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    bad = [r for r in out.get("results", []) if not r.get("ok") and not r.get("skipped")]
    return 0 if not bad else 2


def cmd_errors(args):
    conn = connect()
    rows = conn.execute(
        """
        SELECT id, at, source, path, error_class, error_message, resolved_at
        FROM anamnesis_ingest_errors
        WHERE (? = 1) OR resolved_at IS NULL
        ORDER BY at DESC LIMIT ?
        """,
        (1 if args.all else 0, args.limit),
    ).fetchall()
    conn.close()
    if not rows:
        print("no ingest errors recorded")
        return 0
    for r in rows:
        marker = "✓ resolved" if r["resolved_at"] else "✗ open"
        print(f"[{r['at']}] {marker} {r['source']} {r['error_class']}")
        print(f"  {r['path']}")
        print(f"  {r['error_message'][:200]}")
        print()
    return 0


def cmd_entities(args):
    from anamnesis.entities import backfill, stats
    with audited("entities") as details:
        r = backfill()
        details.update(r)
    s = stats()
    print(json.dumps({"backfill": r, "stats": s}, indent=2, ensure_ascii=False))


def cmd_threads(args):
    from anamnesis.threading import compute, stats
    with audited("threads") as details:
        r = compute()
        details.update(r)
    s = stats()
    print(json.dumps({"compute": r, "stats": s}, indent=2, ensure_ascii=False))


def cmd_eval(args):
    from anamnesis.eval.run import evaluate, load_golden
    from pathlib import Path
    golden = args.golden or str(Path(__file__).parent / "eval" / "golden.yaml")
    queries = load_golden(golden)
    r = evaluate(queries, top_k=args.top_k, mode=args.mode)
    print(f"{args.mode}: {r['total_passed']}/{r['total_queries']} "
          f"pass ({r['pass_rate']:.0%}), p@{args.top_k}={r['avg_precision_at_k']:.3f}")
    return 0 if r["total_passed"] == r["total_queries"] else 1


def build_parser():
    ap = argparse.ArgumentParser(prog="anamnesis")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="incremental ingest + embed + WAL checkpoint")
    s.add_argument("--verbose", action="store_true")
    s.add_argument("--batch", type=int, default=64)
    s.set_defaults(func=cmd_sync)

    st = sub.add_parser("status", help="health + drift report + recent audit")
    st.set_defaults(func=cmd_status)

    sr = sub.add_parser("search", help="hybrid search")
    sr.add_argument("query")
    sr.add_argument("--top-k", type=int, default=10)
    sr.add_argument("--pool", type=int, default=50)
    sr.add_argument("--role", default="any")
    sr.set_defaults(func=cmd_search)

    b = sub.add_parser("backup", help="safe tarball of DB + Chroma")
    b.set_defaults(func=cmd_backup)

    r = sub.add_parser("restore", help="restore from a backup tarball")
    r.add_argument("tarball")
    r.add_argument("--force", action="store_true")
    r.set_defaults(func=cmd_restore)

    v = sub.add_parser("verify", help="integrity + consistency check")
    v.set_defaults(func=cmd_verify)

    au = sub.add_parser("audit", help="recent operational events")
    au.add_argument("--limit", type=int, default=20)
    au.set_defaults(func=cmd_audit)

    cs = sub.add_parser("cross-sync", help="bidirectional jsonl rsync with peers")
    cs.add_argument("--peer", action="append",
                    help="peer 'user@host'. Repeatable. If omitted, uses ANAMNESIS_PEERS / peers.txt")
    cs.add_argument("--verbose", action="store_true")
    cs.set_defaults(func=cmd_cross_sync)

    er = sub.add_parser("errors", help="show ingest errors")
    er.add_argument("--limit", type=int, default=50)
    er.add_argument("--all", action="store_true",
                    help="include resolved errors (default: open only)")
    er.set_defaults(func=cmd_errors)

    ent = sub.add_parser("entities", help="extract entities (paths, URLs) from turns")
    ent.set_defaults(func=cmd_entities)

    th = sub.add_parser("threads", help="recompute session continuation threads")
    th.set_defaults(func=cmd_threads)

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
