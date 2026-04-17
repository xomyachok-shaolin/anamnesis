"""Entity co-occurrence graph and graph-based retrieval.

Builds edges between entities that appear in the same session.
Graph retrieval traverses these edges (BFS) to find turns related to
query entities, feeding results into RRF as an additional channel.
"""
from __future__ import annotations

import json
import logging
from itertools import combinations

log = logging.getLogger(__name__)

BATCH_SIZE = 500


def build_edges(limit: int | None = None) -> dict:
    """Compute co-occurrence edges from entity pairs in same session."""
    from anamnesis.db import connect

    conn = connect()

    query = """
        SELECT DISTINCT ht.content_session_id
        FROM anamnesis_entities ae
        JOIN historical_turns ht ON ht.id = ae.turn_id
        LEFT JOIN anamnesis_graph_state gs
            ON gs.content_session_id = ht.content_session_id
        WHERE gs.content_session_id IS NULL
        ORDER BY ht.content_session_id
    """
    if limit:
        query += f" LIMIT {int(limit)}"

    sessions = conn.execute(query).fetchall()
    processed = 0
    edges_added = 0

    for row in sessions:
        sid = row[0]
        entities = conn.execute(
            """SELECT DISTINCT ae.value
               FROM anamnesis_entities ae
               JOIN historical_turns ht ON ht.id = ae.turn_id
               WHERE ht.content_session_id = ?""",
            (sid,),
        ).fetchall()

        values = sorted(set(r[0] for r in entities))

        # Create edges for all pairs (limit to avoid combinatorial explosion)
        if len(values) > 50:
            values = values[:50]

        for a, b in combinations(values, 2):
            # Canonical ordering
            if a > b:
                a, b = b, a
            conn.execute(
                """INSERT INTO anamnesis_entity_edges (entity_a, entity_b, weight, sessions)
                   VALUES (?, ?, 1, ?)
                   ON CONFLICT(entity_a, entity_b) DO UPDATE SET
                     weight = weight + 1,
                     sessions = json_insert(
                       COALESCE(sessions, '[]'), '$[#]', ?
                     )""",
                (a, b, json.dumps([sid]), sid),
            )
            edges_added += 1

        conn.execute(
            "INSERT OR IGNORE INTO anamnesis_graph_state(content_session_id) VALUES (?)",
            (sid,),
        )
        processed += 1

        if processed % BATCH_SIZE == 0:
            conn.commit()

    conn.commit()
    conn.close()
    return {"sessions_processed": processed, "edges_added": edges_added}


def graph_search(
    conn,
    query_entities: list[str],
    max_hops: int = 2,
    k: int = 50,
) -> list:
    """BFS traversal: find turns mentioning entities related to query entities.

    Returns Hit objects with graph_rank set.
    """
    from anamnesis.search.hybrid import Hit

    if not query_entities:
        return []

    visited = set(query_entities)
    frontier = list(query_entities)
    related: list[tuple[str, int, int]] = []  # (entity, hop, weight)

    for hop in range(1, max_hops + 1):
        next_frontier = []
        for entity in frontier:
            neighbors = conn.execute(
                """SELECT entity_b AS neighbor, weight
                   FROM anamnesis_entity_edges WHERE entity_a = ?
                   UNION ALL
                   SELECT entity_a AS neighbor, weight
                   FROM anamnesis_entity_edges WHERE entity_b = ?""",
                (entity, entity),
            ).fetchall()
            for n in neighbors:
                nb = n["neighbor"]
                if nb not in visited:
                    visited.add(nb)
                    next_frontier.append(nb)
                    related.append((nb, hop, n["weight"]))
        frontier = next_frontier

    if not related:
        return []

    # Sort by weight desc, take top entities
    related.sort(key=lambda x: (-x[2], x[1]))
    top_entities = [r[0] for r in related[:30]]

    # Find turns mentioning these related entities
    placeholders = ",".join("?" * len(top_entities))
    rows = conn.execute(
        f"""
        SELECT DISTINCT ht.id, ht.text, ht.content_session_id, ht.turn_number,
               ht.role, ht.timestamp, ht.platform_source,
               s.custom_title, s.project
        FROM anamnesis_entities ae
        JOIN historical_turns ht ON ht.id = ae.turn_id
        LEFT JOIN sdk_sessions s ON s.content_session_id = ht.content_session_id
        WHERE ae.value IN ({placeholders})
        ORDER BY ht.timestamp DESC
        LIMIT ?
        """,
        (*top_entities, k),
    ).fetchall()

    hits = []
    for rank, row in enumerate(rows, 1):
        hits.append(
            Hit(
                turn_id=row["id"],
                text=row["text"],
                meta={
                    "session": row["content_session_id"],
                    "turn": row["turn_number"],
                    "role": row["role"],
                    "timestamp": row["timestamp"],
                    "source": row["platform_source"],
                    "title": row["custom_title"] or "",
                    "project": row["project"] or "",
                },
                graph_rank=rank,
            )
        )
    return hits
