"""Cross-host bidirectional jsonl sync.

Pulls newer files from peers, then pushes local newer files to them.
Records per-peer attempt/success/error in `anamnestic_peer_state`.

Peer config — first match wins:

  1. CLI flag `--peer host[,host2]`
  2. Env `ANAMNESTIC_PEERS` — colon- or comma-separated `user@host` or `host`
  3. File `~/.claude-mem/peers.txt` — one entry per line (`user@host` or `host`)

Auth: passwordless SSH only (BatchMode=yes). If keys aren't set up,
the call is skipped without crashing.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from anamnestic.config import CC_ROOT, CODEX_ROOT, DATA_DIR
from anamnestic.db import connect

# Sources to mirror, mapped to the path on a remote that has the same layout.
# The rsync `-az --update` flags do timestamp-based merge, no destructive deletion.
SOURCES = (
    (CC_ROOT, ".claude/projects/"),
    (CODEX_ROOT, ".codex/sessions/"),
    (str(Path.home() / ".claude" / "sessions"), ".claude/sessions/"),
)

PEERS_FILE = DATA_DIR / "peers.txt"


def _normalize_peer(p: str) -> str:
    p = p.strip()
    if not p or p.startswith("#"):
        return ""
    if "@" not in p:
        # default to current user
        p = f"{os.environ.get('USER', 'minaevas')}@{p}"
    return p


def discover_peers(cli_peers: list[str] | None = None) -> list[str]:
    if cli_peers:
        candidates = cli_peers
    else:
        env = os.environ.get("ANAMNESTIC_PEERS", "").replace(":", ",").split(",")
        candidates = [p for p in env if p.strip()]
        if not candidates and PEERS_FILE.is_file():
            candidates = [
                _normalize_peer(line) for line in PEERS_FILE.read_text().splitlines()
            ]
    return [p for p in (_normalize_peer(c) for c in candidates) if p]


def _ssh_alive(peer: str, timeout: int = 5) -> bool:
    cmd = [
        "ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}",
        "-o", "StrictHostKeyChecking=accept-new", peer, "true",
    ]
    return subprocess.run(cmd, capture_output=True, timeout=timeout + 5).returncode == 0


def _rsync(direction: str, peer: str, local: str, remote: str) -> tuple[int, str]:
    """direction: 'pull' (peer→local) or 'push' (local→peer)."""
    if not Path(local).exists() and direction == "push":
        return 0, ""
    if direction == "pull":
        src = f"{peer}:{remote}"
        dst = local.rstrip("/") + "/"
    else:
        src = local.rstrip("/") + "/"
        dst = f"{peer}:{remote}"
    cmd = [
        "rsync", "-az", "--update",
        "-e", "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new",
        "--itemize-changes",
        src, dst,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    files = sum(1 for ln in proc.stdout.splitlines() if ln and not ln.startswith("."))
    return files, (proc.stderr or "").strip()


def _record_attempt(peer: str, success: bool, dur: float, err: str,
                    pulled: int, pushed: int) -> None:
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO anamnestic_peer_state
              (peer, last_attempt_at, last_success_at, last_error,
               duration_sec, files_pulled, files_pushed)
            VALUES (?, datetime('now'), ?, ?, ?, ?, ?)
            ON CONFLICT(peer) DO UPDATE SET
              last_attempt_at = excluded.last_attempt_at,
              last_success_at = COALESCE(excluded.last_success_at, last_success_at),
              last_error      = excluded.last_error,
              duration_sec    = excluded.duration_sec,
              files_pulled    = files_pulled + excluded.files_pulled,
              files_pushed    = files_pushed + excluded.files_pushed
            """,
            (
                peer,
                "datetime('now')" if success else None,
                err if not success else None,
                round(dur, 2),
                pulled,
                pushed,
            ),
        )
        if success:
            conn.execute(
                "UPDATE anamnestic_peer_state SET last_success_at = datetime('now') "
                "WHERE peer = ?",
                (peer,),
            )
        conn.commit()
    finally:
        conn.close()


def sync_with_peer(peer: str, verbose: bool = False) -> dict:
    t0 = time.time()
    if not shutil.which("rsync") or not shutil.which("ssh"):
        return {"peer": peer, "ok": False, "error": "rsync/ssh missing", "skipped": True}
    if not _ssh_alive(peer):
        info = {"peer": peer, "ok": False, "error": "unreachable", "skipped": True}
        _record_attempt(peer, False, time.time() - t0, "unreachable", 0, 0)
        return info

    pulled = pushed = 0
    err_msg = ""
    try:
        for local, remote in SOURCES:
            os.makedirs(local, exist_ok=True)
            n, err = _rsync("pull", peer, local, remote)
            pulled += n
            if err and verbose:
                print(f"[pull {peer}] stderr: {err}")
            n, err = _rsync("push", peer, local, remote)
            pushed += n
            if err and verbose:
                print(f"[push {peer}] stderr: {err}")
    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"

    dur = time.time() - t0
    ok = not err_msg
    _record_attempt(peer, ok, dur, err_msg, pulled, pushed)
    return {
        "peer": peer, "ok": ok, "error": err_msg or None,
        "files_pulled": pulled, "files_pushed": pushed,
        "duration_sec": round(dur, 2),
    }


def run(peers: list[str] | None = None, verbose: bool = False) -> dict:
    peer_list = discover_peers(peers)
    if not peer_list:
        return {"peers": [], "results": [], "note": "no peers configured"}
    results = [sync_with_peer(p, verbose=verbose) for p in peer_list]
    return {"peers": peer_list, "results": results}


if __name__ == "__main__":
    import sys
    out = run(verbose=True)
    print(json.dumps(out, indent=2, ensure_ascii=False))
