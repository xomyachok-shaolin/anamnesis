"""Parser for VS Code GitHub Copilot Chat sessions.

Location: ~/.config/Code/User/workspaceStorage/<workspace_hash>/chatSessions/<uuid>.jsonl

File is JSON-per-line with records like:
    {"kind": "0", "v": {"version": "3", "creationDate": "<ms>",
                         "sessionId": "<uuid>",
                         "customTitle": "...",
                         "requests": "[{'requestId': ..., ...}, ...]",
                         ...}}

`v.requests` is a Python-repr stringified list of request/response rounds.
Each round has user message + agent response. We parse via ast.literal_eval.

Platform tag: 'vscode-copilot'.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Optional

from anamnesis.ingest.parsers import ts_to_epoch


def _ms_to_iso(ms: int | str | None) -> str:
    try:
        from datetime import datetime, timezone
        ms = int(ms or 0)
        if not ms:
            return ""
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def _extract_round_texts(r: dict) -> tuple[str, str]:
    """Return (user_text, assistant_text) from a Copilot request dict."""
    user = ""
    assistant = ""

    # User prompt — usually in 'message' or 'request' or directly 'text'
    msg = r.get("message") or {}
    if isinstance(msg, dict):
        user = str(msg.get("text") or msg.get("value") or "").strip()
    if not user:
        user = str(r.get("requestText") or r.get("text") or "").strip()

    # Assistant response — 'response' is commonly a list of chunks
    resp = r.get("response")
    if isinstance(resp, list):
        parts = []
        for chunk in resp:
            if isinstance(chunk, dict):
                parts.append(str(chunk.get("value") or chunk.get("text") or ""))
            elif isinstance(chunk, str):
                parts.append(chunk)
        assistant = "\n".join(p for p in parts if p.strip()).strip()
    elif isinstance(resp, dict):
        assistant = str(resp.get("text") or resp.get("value") or "").strip()
    elif isinstance(resp, str):
        assistant = resp.strip()

    return user, assistant


def parse_vscode_copilot_jsonl(path: str) -> Optional[dict]:
    """Return meta dict compatible with anamnesis ingest, or None."""
    p = Path(path)
    if not p.is_file():
        return None

    session_id = None
    title = None
    creation_iso = None
    requests_raw = None
    workspace_hash = p.parent.parent.name  # .../<hash>/chatSessions/<file>

    with p.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            v = d.get("v")
            if not isinstance(v, dict):
                continue
            session_id = session_id or v.get("sessionId") or p.stem
            title = title or v.get("customTitle")
            creation_iso = creation_iso or _ms_to_iso(v.get("creationDate"))
            # Pick requests from the latest-looking record
            r = v.get("requests")
            if r:
                requests_raw = r

    if not requests_raw:
        return None

    try:
        requests = ast.literal_eval(requests_raw) if isinstance(requests_raw, str) else requests_raw
    except Exception:
        return None
    if not isinstance(requests, list):
        return None

    turns: list[tuple[str, str, str]] = []
    first_ts = creation_iso or ""
    last_ts = first_ts
    for r in requests:
        if not isinstance(r, dict):
            continue
        user, assistant = _extract_round_texts(r)
        ts_ms = r.get("timestamp")
        ts_iso = _ms_to_iso(ts_ms)
        if ts_iso:
            if not first_ts:
                first_ts = ts_iso
            last_ts = ts_iso
        if user:
            turns.append(("user", user, ts_iso or first_ts))
        if assistant:
            turns.append(("assistant", assistant, ts_iso or first_ts))

    if not turns:
        return None

    return {
        "csid": session_id or f"vscode-{p.stem}",
        "cwd": f"vscode-workspace:{workspace_hash}",
        "title": title,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "platform": "vscode-copilot",
        "turns": turns,
    }
