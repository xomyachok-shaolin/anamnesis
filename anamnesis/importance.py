"""Deterministic importance scoring for historical turns.

Assigns a 0.0–1.0 score based on text heuristics: code blocks, errors,
decisions, length. Used as an RRF score multiplier during search.
"""
from __future__ import annotations

import re

_CODE_BLOCK_RE = re.compile(r"```")
_ERROR_RE = re.compile(
    r"\b(?:error|exception|traceback|failed|failure|ENOENT|panic|segfault"
    r"|stack\s*trace|abort|fatal)\b",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(
    r"\b(?:decided|decision|conclusion|resolved|solution|approach|will\s+use"
    r"|выбрали|решили|будем\s+использовать|вывод|итог|решение)\b",
    re.IGNORECASE,
)

BATCH_SIZE = 2000


def score(text: str, role: str = "assistant") -> float:
    """Return importance 0.0–1.0 for a single turn text."""
    if not text:
        return 0.05

    length = len(text)

    # Very short trivial turns — low importance
    if length < 50:
        return 0.1

    s = 0.0

    # Length contribution (longer = more substantive), max 0.15
    s += min(length / 2000, 1.0) * 0.15

    # Code blocks — each adds 0.1, capped at 0.3
    code_blocks = len(_CODE_BLOCK_RE.findall(text)) // 2  # pairs of ```
    s += min(code_blocks * 0.1, 0.3)

    # Error / exception indicators
    if _ERROR_RE.search(text):
        s += 0.15

    # Decision / conclusion language
    if _DECISION_RE.search(text):
        s += 0.15

    # User turns with questions are moderately important
    if role == "user" and "?" in text:
        s += 0.1

    # Clamp to [0.05, 1.0]
    return max(0.05, min(s + 0.15, 1.0))  # +0.15 base


def backfill(limit: int | None = None) -> dict:
    """Re-score turns that still have the default 0.5 importance."""
    from anamnesis.db import connect

    conn = connect()
    cur = conn.cursor()

    query = """
        SELECT id, text, role FROM historical_turns
        WHERE importance = 0.5
        ORDER BY id
    """
    if limit:
        query += f" LIMIT {int(limit)}"

    rows = cur.execute(query).fetchall()
    updated = 0

    batch: list[tuple[float, int]] = []
    for row in rows:
        imp = score(row["text"], row["role"])
        batch.append((imp, row["id"]))
        if len(batch) >= BATCH_SIZE:
            cur.executemany(
                "UPDATE historical_turns SET importance = ? WHERE id = ?",
                batch,
            )
            conn.commit()
            updated += len(batch)
            batch.clear()

    if batch:
        cur.executemany(
            "UPDATE historical_turns SET importance = ? WHERE id = ?",
            batch,
        )
        conn.commit()
        updated += len(batch)

    conn.close()
    return {"scored": updated}
