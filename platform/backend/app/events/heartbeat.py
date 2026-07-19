"""Worker liveness gauges for /api/health. File-based (DATA_DIR) so the API process
shares nothing with the worker beyond the events table. The silent-death failure mode
of every poller (plan §10) is covered by two independent RAW gauges — heartbeat age
(is the process looping?) and hours since the last detected event (is it ingesting?).
The filing-hours-aware alarm itself is computed in main.py (PR-6) from these gauges —
exactly one alarm implementation."""
from __future__ import annotations

import datetime as dt
import json
import os
import time

from sqlalchemy import func, select

from ..core.config import DATA_DIR

HEARTBEAT_PATH = DATA_DIR / "worker_heartbeat.json"
STALE_S = 900              # two missed 5-min polls + margin


def _read() -> dict:
    try:
        return json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def beat(events_ingested: int, jobs: dict, stopping: bool = False) -> None:
    """Called once per worker tick. The daily counter accumulates in the file itself
    (single writer: the worker) and resets when the date rolls."""
    prev, today = _read(), dt.date.today().isoformat()
    count = int(events_ingested) + (int(prev.get("events_ingested_today") or 0)
                                    if prev.get("day") == today else 0)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_PATH.write_text(json.dumps({
        "ts": time.time(), "day": today, "pid": os.getpid(),
        "events_ingested_today": count, "jobs": jobs, "stopping": stopping}),
        encoding="utf-8")


def worker_status() -> dict:
    """Read-only raw gauges: heartbeat file + authoritative events-table gauge."""
    hb = _read()
    age = (time.time() - float(hb["ts"])) if hb.get("ts") else None
    alive = age is not None and age < STALE_S and not hb.get("stopping")
    last_hours = None
    try:
        from .. import models_events
        from ..core.db import session_scope
        with session_scope() as s:
            last = s.execute(select(func.max(models_events.Event.detected_at))).scalar()
        if last is not None:
            last_hours = round((dt.datetime.utcnow() - last).total_seconds() / 3600, 1)
    except Exception:
        pass       # events table absent (fresh DB) -> gauge stays None
    return {
        "alive": alive,
        "heartbeat_age_s": None if age is None else round(age),
        "events_ingested_today": int(hb.get("events_ingested_today") or 0),
        "last_event_hours": last_hours,
    }
