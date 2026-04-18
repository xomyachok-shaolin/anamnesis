"""Recover missing `historical_turns` for Claude Code main sessions.

Bug: backfill v1 created sdk_sessions rows (platform='claude') without
populating historical_turns. v2 then skipped them via idempotency check.
This script re-parses the main jsonl files and fills historical_turns
for sessions that have 0 rows there.
"""
import os
import sys
from glob import glob

from anamnestic.config import CC_ROOT, is_project_in_scope
from anamnestic.db import connect
from anamnestic.ingest.parsers import parse_claude_jsonl


def main():
    conn = connect()
    cur = conn.cursor()

    # sessions needing recovery: platform='claude' AND zero historical_turns rows
    missing = cur.execute(
        """
        SELECT s.content_session_id
        FROM sdk_sessions s
        WHERE s.platform_source = 'claude'
          AND NOT EXISTS (
              SELECT 1 FROM historical_turns ht
              WHERE ht.content_session_id = s.content_session_id
          )
        """
    ).fetchall()
    missing_ids = {row[0] for row in missing}
    print(f"Sessions missing historical_turns: {len(missing_ids)}")
    if not missing_ids:
        print("Nothing to recover.")
        return

    jsonls = sorted(glob(os.path.join(CC_ROOT, "*", "*.jsonl")))
    recovered = 0
    total_turns = 0

    for path in jsonls:
        try:
            if os.path.getsize(path) < 100:
                continue
            meta = parse_claude_jsonl(path, is_subagent=False)
            if meta and not is_project_in_scope(meta["cwd"]):
                continue
            if not meta or meta["csid"] not in missing_ids:
                continue
            started = meta["first_ts"] or ""
            rows = []
            for i, (role, text, ts) in enumerate(meta["turns"], 1):
                rows.append((
                    meta["csid"], i, role, text[:20000],
                    ts or started, meta["platform"],
                ))
            cur.executemany(
                """
                INSERT INTO historical_turns
                  (content_session_id, turn_number, role, text, timestamp,
                   platform_source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            recovered += 1
            total_turns += len(rows)
            if recovered % 10 == 0:
                conn.commit()
                print(f"  recovered {recovered} sessions, {total_turns} turns")
        except Exception as e:
            print(f"  ERROR {path}: {e}", file=sys.stderr)

    conn.commit()
    print(f"Done. recovered={recovered} sessions, turns={total_turns}")
    # Stats
    total = cur.execute("SELECT COUNT(*) FROM historical_turns").fetchone()[0]
    by_src = cur.execute(
        "SELECT platform_source, COUNT(*) FROM historical_turns GROUP BY platform_source"
    ).fetchall()
    print(f"historical_turns total: {total}")
    for src, n in by_src:
        print(f"  {src}: {n}")
    conn.close()


if __name__ == "__main__":
    main()
