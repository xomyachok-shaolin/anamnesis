"""Incremental ingest: detect new/changed jsonl files by mtime, parse, upsert.

Uses anamnestic_ingest_state to skip unchanged files on repeat runs.
Safe to run on a timer.
"""
import os
import sys
import time
import uuid
from glob import glob

from anamnestic.config import (
    CC_ROOT,
    CODEX_ROOT,
    INGEST_VSCODE_COPILOT,
    VSCODE_WORKSPACE_ROOT,
    is_project_in_scope,
)
from anamnestic.db import connect, retry_on_busy
from anamnestic.ingest.parsers import parse_claude_jsonl, parse_codex_jsonl, ts_to_epoch
from anamnestic.ingest.vscode_copilot import parse_vscode_copilot_jsonl


def _discover():
    """Yield (source, path, mtime_ns) for all known jsonl locations."""
    for p in glob(os.path.join(CC_ROOT, "*", "*.jsonl")):
        yield "claude", p, os.stat(p).st_mtime_ns
    for p in glob(os.path.join(CC_ROOT, "*", "*", "subagents", "*.jsonl")):
        yield "claude-subagent", p, os.stat(p).st_mtime_ns
    for p in glob(os.path.join(CODEX_ROOT, "*", "*", "*", "*.jsonl")):
        yield "codex", p, os.stat(p).st_mtime_ns
    if INGEST_VSCODE_COPILOT:
        for p in glob(
            os.path.join(VSCODE_WORKSPACE_ROOT, "*", "chatSessions", "*.jsonl")
        ):
            # Skip tiny empty stubs
            try:
                if os.path.getsize(p) < 500:
                    continue
            except OSError:
                continue
            yield "vscode-copilot", p, os.stat(p).st_mtime_ns


def _needs_ingest(cur, source, path, mtime_ns):
    row = cur.execute(
        "SELECT mtime_ns FROM anamnestic_ingest_state WHERE source=? AND path=?",
        (source, path),
    ).fetchone()
    if row is None:
        return True
    return mtime_ns > row[0]


def _mark_ingested(cur, source, path, mtime_ns, turns):
    cur.execute(
        """
        INSERT INTO anamnestic_ingest_state(source, path, mtime_ns, ingested_at, turns)
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
    from anamnestic.importance import score as importance_score

    for i, (role, text, ts) in enumerate(meta["turns"], 1):
        imp = importance_score(text[:20000], role)
        cur.execute(
            """
            INSERT INTO historical_turns
              (content_session_id, turn_number, role, text, timestamp,
               platform_source, importance)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(content_session_id, turn_number) DO NOTHING
            """,
            (sid, i, role, text[:20000], ts or started, meta["platform"], imp),
        )

    return memory_id, len(meta["turns"])


def _record_error(cur, source: str, path: str, exc: Exception) -> None:
    cur.execute(
        """
        INSERT INTO anamnestic_ingest_errors
          (source, path, error_class, error_message)
        VALUES (?, ?, ?, ?)
        """,
        (source, path, type(exc).__name__, str(exc)[:2000]),
    )


def _resolve_errors(cur, path: str) -> int:
    cur.execute(
        """
        UPDATE anamnestic_ingest_errors
        SET resolved_at = datetime('now')
        WHERE path = ? AND resolved_at IS NULL
        """,
        (path,),
    )
    return cur.rowcount


@retry_on_busy
def _ingest_one(cur, source, path, mtime_ns, meta):
    """Apply UPSERT + state bookkeeping for a single file. Wrapped in BUSY retry."""
    if meta is None or (meta and not is_project_in_scope(meta["cwd"])):
        _mark_ingested(cur, source, path, mtime_ns, 0)
        resolved = _resolve_errors(cur, path)
        return 0, resolved
    _, turn_count = _upsert_session(cur, meta)
    _mark_ingested(cur, source, path, mtime_ns, turn_count)
    resolved = _resolve_errors(cur, path)
    return turn_count, resolved


def run(verbose=False):
    conn = connect()
    cur = conn.cursor()

    stats = {
        "total": 0, "skipped": 0, "new_files": 0, "new_turns": 0,
        "errors": 0, "resolved": 0,
    }
    for source, path, mtime_ns in _discover():
        stats["total"] += 1
        if not _needs_ingest(cur, source, path, mtime_ns):
            stats["skipped"] += 1
            continue
        try:
            if os.path.getsize(path) < 100:
                _mark_ingested(cur, source, path, mtime_ns, 0)
                stats["resolved"] += _resolve_errors(cur, path)
                continue
            if source == "codex":
                meta = parse_codex_jsonl(path)
            elif source == "vscode-copilot":
                meta = parse_vscode_copilot_jsonl(path)
            else:
                meta = parse_claude_jsonl(
                    path, is_subagent=(source == "claude-subagent")
                )
            if meta and not is_project_in_scope(meta["cwd"]):
                stats["skipped"] += 1
                continue
            if not meta:
                _mark_ingested(cur, source, path, mtime_ns, 0)
                stats["resolved"] += _resolve_errors(cur, path)
                continue
            turn_count, resolved = _ingest_one(cur, source, path, mtime_ns, meta)
            stats["resolved"] += resolved
            stats["new_files"] += 1
            stats["new_turns"] += turn_count
            if verbose and stats["new_files"] % 20 == 0:
                print(f"  processed {stats['new_files']} new files ({stats['new_turns']} turns)")
            if stats["new_files"] % 50 == 0:
                conn.commit()
        except Exception as e:
            stats["errors"] += 1
            try:
                _record_error(cur, source, path, e)
            except Exception:
                pass
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
