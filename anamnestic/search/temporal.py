"""Temporal retrieval channel for time-scoped queries.

Parses temporal expressions (EN/RU) from the query, retrieves turns
within the detected time range, and produces ranked Hits for RRF fusion.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now()


def _days_ago(n: int) -> datetime:
    return _now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=n)


def _start_of_day(d: datetime) -> datetime:
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def _end_of_day(d: datetime) -> datetime:
    return d.replace(hour=23, minute=59, second=59, microsecond=999999)


def _start_of_week() -> datetime:
    now = _now()
    return _start_of_day(now - timedelta(days=now.weekday()))


def _month_range(month_name: str) -> tuple[datetime, datetime]:
    """Return (start, end) for the most recent occurrence of the named month."""
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    m = months.get(month_name.lower())
    if not m:
        return None  # type: ignore[return-value]
    now = _now()
    year = now.year if m <= now.month else now.year - 1
    start = datetime(year, m, 1)
    # End of month
    if m == 12:
        end = datetime(year + 1, 1, 1) - timedelta(seconds=1)
    else:
        end = datetime(year, m + 1, 1) - timedelta(seconds=1)
    return start, end


_RU_MONTHS = {
    "январе": 1, "феврале": 2, "марте": 3, "апреле": 4,
    "мае": 5, "июне": 6, "июле": 7, "августе": 8,
    "сентябре": 9, "октябре": 10, "ноябре": 11, "декабре": 12,
}


def _month_range_ru(month_name: str) -> tuple[datetime, datetime] | None:
    m = _RU_MONTHS.get(month_name.lower())
    if not m:
        return None
    now = _now()
    year = now.year if m <= now.month else now.year - 1
    start = datetime(year, m, 1)
    if m == 12:
        end = datetime(year + 1, 1, 1) - timedelta(seconds=1)
    else:
        end = datetime(year, m + 1, 1) - timedelta(seconds=1)
    return start, end


# ---------------------------------------------------------------------------
# Pattern table — each entry: (regex, resolver)
# resolver receives the Match object and returns (start, end) datetimes
# ---------------------------------------------------------------------------

def _resolve_yesterday(_m=None):
    d = _now() - timedelta(days=1)
    return _start_of_day(d), _end_of_day(d)

def _resolve_today(_m=None):
    d = _now()
    return _start_of_day(d), _end_of_day(d)

def _resolve_this_week(_m=None):
    return _start_of_week(), _end_of_day(_now())

def _resolve_last_week(_m=None):
    end = _start_of_week() - timedelta(seconds=1)
    start = _start_of_day(end - timedelta(days=6))
    return start, end

def _resolve_last_month(_m=None):
    now = _now()
    first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = first_this - timedelta(seconds=1)
    start = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, end

def _resolve_n_days_ago(m):
    n = int(m.group(1))
    d = _now() - timedelta(days=n)
    return _start_of_day(d), _end_of_day(_now() - timedelta(days=n))

def _resolve_en_month(m):
    return _month_range(m.group(1))

def _resolve_ru_month(m):
    return _month_range_ru(m.group(1))


_PATTERNS: list[tuple[re.Pattern, callable]] = [
    # English
    (re.compile(r"\byesterday\b", re.I), _resolve_yesterday),
    (re.compile(r"\btoday\b", re.I), _resolve_today),
    (re.compile(r"\bthis\s+week\b", re.I), _resolve_this_week),
    (re.compile(r"\blast\s+week\b", re.I), _resolve_last_week),
    (re.compile(r"\blast\s+month\b", re.I), _resolve_last_month),
    (re.compile(r"\b(\d+)\s+days?\s+ago\b", re.I), _resolve_n_days_ago),
    (re.compile(
        r"\bin\s+(january|february|march|april|may|june|july|august"
        r"|september|october|november|december)\b", re.I,
    ), _resolve_en_month),
    # Russian
    (re.compile(r"\bвчера\b", re.I), _resolve_yesterday),
    (re.compile(r"\bсегодня\b", re.I), _resolve_today),
    (re.compile(r"\bна\s+этой\s+неделе\b", re.I), _resolve_this_week),
    (re.compile(r"\bна\s+прошлой\s+неделе\b", re.I), _resolve_last_week),
    (re.compile(r"\bв\s+прошлом\s+месяце\b", re.I), _resolve_last_month),
    (re.compile(
        r"\bв\s+(январе|феврале|марте|апреле|мае|июне|июле|августе"
        r"|сентябре|октябре|ноябре|декабре)\b", re.I,
    ), _resolve_ru_month),
    (re.compile(r"\b(\d+)\s+(?:дней|дня|день)\s+назад\b", re.I), _resolve_n_days_ago),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_time_range(query: str) -> tuple[str, str] | None:
    """Parse query for temporal expressions.

    Returns (ISO-start, ISO-end) or None if no temporal signal detected.
    """
    for pattern, resolver in _PATTERNS:
        m = pattern.search(query)
        if m:
            result = resolver(m)
            if result:
                start, end = result
                return start.isoformat(), end.isoformat()
    return None


def temporal_search(
    conn: sqlite3.Connection,
    time_range: tuple[str, str],
    k: int,
) -> list:
    """Retrieve turns within time_range, scored by recency (most recent = rank 1)."""
    from anamnestic.search.hybrid import Hit

    start, end = time_range
    rows = conn.execute(
        """
        SELECT ht.id, ht.text, ht.content_session_id, ht.turn_number,
               ht.role, ht.timestamp, ht.platform_source,
               s.custom_title, s.project
        FROM historical_turns ht
        LEFT JOIN sdk_sessions s ON s.content_session_id = ht.content_session_id
        WHERE ht.timestamp BETWEEN ? AND ?
        ORDER BY ht.timestamp DESC
        LIMIT ?
        """,
        (start, end, k),
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
                temporal_rank=rank,
            )
        )
    return hits
