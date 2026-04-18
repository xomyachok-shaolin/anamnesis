"""Entity extraction from historical turns.

Deterministic regex-based extraction of structured entities (file paths, URLs)
from turn text. No LLM involved — fast, reproducible, auditable.

Extracted entities go into `anamnestic_entities` and enable scoped queries
("what did we do with config.py?") via `mem_entity`.
"""
from __future__ import annotations

import re
from typing import Iterator

from anamnestic.db import connect

_PATH_RE = re.compile(
    r"(?<!\w)"
    r"(?:"
    r"(?:/(?:home|usr|var|etc|opt|tmp|mnt|srv|run|proc|sys|dev|boot|root)"
    r"(?:/[\w.+\-@]+)+)"
    r"|(?:~/[\w.+\-@]+(?:/[\w.+\-@]+)*)"
    r"|(?:\.{1,2}/[\w.+\-@]+(?:/[\w.+\-@]+)*)"
    r")"
    r"(?<![.,;:!?])"
)

_URL_RE = re.compile(
    r"https?://[^\s<>\"')\]},;]+(?<![.,;:!?)])"
)

_EXTRACTORS: list[tuple[str, re.Pattern]] = [
    ("path", _PATH_RE),
    ("url", _URL_RE),
]

BATCH_SIZE = 2000


def extract(text: str) -> Iterator[tuple[str, str]]:
    """Yield (entity_type, value) pairs from `text`."""
    for etype, pattern in _EXTRACTORS:
        seen: set[str] = set()
        for m in pattern.finditer(text):
            val = m.group()
            if val not in seen:
                seen.add(val)
                yield etype, val


def backfill(limit: int | None = None) -> dict:
    """Extract entities from all unprocessed turns.

    Returns {processed: int, entities_added: int}.
    """
    conn = connect()
    processed = 0
    added = 0
    try:
        while True:
            rows = conn.execute(
                """
                SELECT ht.id, ht.text
                FROM historical_turns ht
                LEFT JOIN anamnestic_entity_state es ON es.turn_id = ht.id
                WHERE es.turn_id IS NULL
                ORDER BY ht.id
                LIMIT ?
                """,
                (BATCH_SIZE if limit is None else min(BATCH_SIZE, limit - processed),),
            ).fetchall()
            if not rows:
                break
            for row in rows:
                tid = row["id"]
                entities = list(extract(row["text"]))
                if entities:
                    conn.executemany(
                        "INSERT OR IGNORE INTO anamnestic_entities"
                        "(turn_id, entity_type, value) VALUES (?, ?, ?)",
                        [(tid, etype, val) for etype, val in entities],
                    )
                    added += len(entities)
                conn.execute(
                    "INSERT OR IGNORE INTO anamnestic_entity_state(turn_id) VALUES (?)",
                    (tid,),
                )
                processed += 1
            conn.commit()
            if limit is not None and processed >= limit:
                break
    finally:
        conn.close()
    return {"processed": processed, "entities_added": added}


def stats() -> dict:
    conn = connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM anamnestic_entities").fetchone()[0]
        by_type = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT entity_type, COUNT(*) FROM anamnestic_entities GROUP BY entity_type"
            ).fetchall()
        }
        processed = conn.execute("SELECT COUNT(*) FROM anamnestic_entity_state").fetchone()[0]
        total_turns = conn.execute("SELECT COUNT(*) FROM historical_turns").fetchone()[0]
        return {
            "total_entities": total,
            "by_type": by_type,
            "turns_processed": processed,
            "turns_total": total_turns,
            "turns_pending": total_turns - processed,
        }
    finally:
        conn.close()
