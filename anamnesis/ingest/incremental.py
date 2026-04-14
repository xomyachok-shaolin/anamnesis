"""Incremental ingest: detect new/changed jsonl files by mtime, parse, upsert.

Reuses parsers from backfill_v2.py. Uses anamnesis_ingest_state to skip unchanged
files on repeat runs. Safe to run on a timer.
"""
import os
import sys
import time
import uuid
from glob import glob

sys.path.insert(0, os.path.expanduser("~/.claude-mem"))
from backfill_v2 import parse_claude_jsonl, parse_codex_jsonl, ts_to_epoch  # noqa: E402

from anamnesis.db import connect  # noqa: E402

CC_ROOT = os.path.expanduser("~/.claude/projects")
CODEX_ROOT = os.path.expanduser("~/.codex/sessions")


def _discover():
    """Yield (source, path, mtime_ns) for all known jsonl locations."""
    for p in glob(os.path.join(CC_ROOT, "*", "*.jsonl")):
        yield "claude", p, os.stat(p).st_mtime_ns
    for p in glob(os.path.join(CC_ROOT, "*", "*", "subagents", "*.jsonl")):
        yield "claude-subagent", p, os.stat(p).st_mtime_ns
    for p in glob(os.path.join(CODEX_ROOT, "*", "*", "*", "*.jsonl")):
        yield "codex", p, os.stat(p).st_mtime_ns


def _needs_ingest(cur, source, path, mtime_ns):
    row = cur.execute(
        "SELECT mtime_ns FROM anamnesis_ingest_state WHERE source=? AND path=?",
        (source, path),
    ).fetchone()
    if row is None:
        return True
    return mtime_ns > row[0]


def _mark_ingested(cur, source, path, mtime_ns, turns):
    cur.execute(
        """
        INSERT INTO anamnesis_ingest_state(source, path, mtime_ns, ingested_at, turns)
        VALUES (?, ?, ?, datetime('now'), ?)
        ON CONFLICT(source, path) DO UPDATE
        SET mtime_ns=excluded.mtime_ns,
            ingested_at=excluded.ingested_at,
            turns=excluded.turns
        """,
        (source, path, mtime_ns, turns),
    )


def _upsert_session(cur, meta):
    sid = meta["csid"]
    started = meta["first_ts"] or ""
    completed = meta["last_ts"] or started
    started_epoch = ts_to_epoch(started)
    completed_epoch = ts_to_epoch(completed)
    memory_id = str(uuid.uuid4())
    user_turns = [t for t in meta["turns"] if t[0] == "user"]
    first_prompt = (user_turns[0][1] if user_turns else meta["turns"][0][1])[:2000]

    row = cur.execute(
        "SELECT memory_session_id FROM sdk_sessions WHERE content_session_id=?",
        (sid,),
    ).fetchone()
    if row:
        memory_id = row[0]
    else:
        cur.execute(
            """
            INSERT INTO sdk_sessions
              (content_session_id, memory_session_id, project, platform_source,
               user_prompt, started_at, started_at_epoch,
               completed_at, completed_at_epoch, status,
               prompt_counter, custom_title)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?)
            """,
            (
                sid, memory_id, meta["cwd"], meta["platform"],
                first_prompt, started, started_epoch,
                completed, completed_epoch,
                len(user_turns), meta["title"],
            ),
        )

    # user_prompts
    for i, (role, text, ts) in enumerate(
        [t for t in meta["turns"] if t[0] == "user"], 1
    ):
        cur.execute(
            """
            INSERT INTO user_prompts
              (content_session_id, prompt_number, prompt_text,
               created_at, created_at_epoch)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(content_session_id, prompt_number) DO NOTHING
            """,
            (sid, i, text[:20000], ts or started, ts_to_epoch(ts) or started_epoch),
        )

    # historical_turns — rely on UNIQUE (session, turn_number) to avoid dupes
    for i, (role, text, ts) in enumerate(meta["turns"], 1):
        cur.execute(
            """
            INSERT INTO historical_turns
              (content_session_id, turn_number, role, text, timestamp,
               platform_source)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(content_session_id, turn_number) DO NOTHING
            """,
            (sid, i, role, text[:20000], ts or started, meta["platform"]),
        )

    return memory_id, len(meta["turns"])


def run(verbose=False):
    conn = connect()
    cur = conn.cursor()

    stats = {"total": 0, "skipped": 0, "new_files": 0, "new_turns": 0, "errors": 0}
    for source, path, mtime_ns in _discover():
        stats["total"] += 1
        if not _needs_ingest(cur, source, path, mtime_ns):
            stats["skipped"] += 1
            continue
        try:
            if os.path.getsize(path) < 100:
                _mark_ingested(cur, source, path, mtime_ns, 0)
                continue
            if source == "codex":
                meta = parse_codex_jsonl(path)
            else:
                meta = parse_claude_jsonl(
                    path, is_subagent=(source == "claude-subagent")
                )
            if not meta:
                _mark_ingested(cur, source, path, mtime_ns, 0)
                continue
            _, turn_count = _upsert_session(cur, meta)
            _mark_ingested(cur, source, path, mtime_ns, turn_count)
            stats["new_files"] += 1
            stats["new_turns"] += turn_count
            if verbose and stats["new_files"] % 20 == 0:
                print(f"  processed {stats['new_files']} new files ({stats['new_turns']} turns)")
            if stats["new_files"] % 50 == 0:
                conn.commit()
        except Exception as e:
            stats["errors"] += 1
            if verbose:
                print(f"  ERROR {path}: {e}", file=sys.stderr)

    conn.commit()
    conn.close()
    return stats


if __name__ == "__main__":
    t0 = time.time()
    stats = run(verbose=True)
    dt = time.time() - t0
    print(f"Ingest: scanned={stats['total']} skipped={stats['skipped']} "
          f"new_files={stats['new_files']} new_turns={stats['new_turns']} "
          f"errors={stats['errors']} ({dt:.1f}s)")
