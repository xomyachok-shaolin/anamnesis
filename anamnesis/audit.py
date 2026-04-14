"""Audit log + health snapshot helpers."""
import json
import time
from contextlib import contextmanager

from anamnesis.config import HEALTH_FILE
from anamnesis.db import connect


def write_audit(action: str, status: str, duration_sec: float | None, details: dict):
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO anamnesis_audit(action, status, duration_sec, details) "
            "VALUES (?, ?, ?, ?)",
            (action, status, duration_sec, json.dumps(details, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def write_health(snapshot: dict):
    snapshot = dict(snapshot)
    snapshot.setdefault("written_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    with open(HEALTH_FILE, "w") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


@contextmanager
def audited(action: str):
    """Context manager: log start, duration, status=ok|error; re-raise."""
    t0 = time.time()
    details = {}
    try:
        yield details
    except Exception as e:
        dt = round(time.time() - t0, 2)
        details["error"] = f"{type(e).__name__}: {e}"
        write_audit(action, "error", dt, details)
        raise
    else:
        dt = round(time.time() - t0, 2)
        status = details.pop("_status", "ok")
        write_audit(action, status, dt, details)


def recent(limit: int = 20) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT at, action, status, duration_sec, details "
            "FROM anamnesis_audit ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "at": r["at"],
                "action": r["action"],
                "status": r["status"],
                "duration_sec": r["duration_sec"],
                "details": json.loads(r["details"]) if r["details"] else {},
            }
            for r in rows
        ]
    finally:
        conn.close()
