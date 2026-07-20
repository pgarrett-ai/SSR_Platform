"""PR-6 ops: the filing-hours-aware zero-ingest alarm, computed from PR-2b's raw
worker_status() gauges (plan §10: 'pollers die silently' — the alarm ships in
Phase 6, not Phase 11). ONE implementation — heartbeat.py stays raw-gauges-only."""
import datetime as dt

from fastapi.testclient import TestClient

import app.main as main
from app.main import _zero_ingest_alarm, app

client = TestClient(app).__enter__()

WED = dt.datetime(2026, 7, 15, 16, 0)    # Wednesday 16:00 UTC — inside filing hours
SUN = dt.datetime(2026, 7, 19, 16, 0)


def _w(alive=True, age=60, last_hours=1.0, count=5):
    return {"alive": alive, "heartbeat_age_s": age,
            "events_ingested_today": count, "last_event_hours": last_hours}


def test_zero_ingest_alarm_states():
    assert _zero_ingest_alarm({"alive": False, "heartbeat_age_s": None,
                               "events_ingested_today": 0,
                               "last_event_hours": None}, WED) is False  # never deployed ≠ dead
    assert _zero_ingest_alarm(_w(alive=False, age=7200), WED) is True    # worker died
    assert _zero_ingest_alarm(_w(), WED) is False                        # healthy
    assert _zero_ingest_alarm(_w(last_hours=12.0), WED) is True          # alive, ingesting nothing
    assert _zero_ingest_alarm(_w(last_hours=None), WED) is True          # alive, never ingested
    assert _zero_ingest_alarm(_w(last_hours=12.0), SUN) is False         # weekend quiet OK
    night = dt.datetime(2026, 7, 15, 3, 0)
    assert _zero_ingest_alarm(_w(last_hours=12.0), night) is False       # off-hours OK


def test_health_carries_alarm_field(monkeypatch):
    monkeypatch.setattr(main, "worker_status", lambda: _w())
    h = client.get("/api/health").json()
    assert h["worker"]["alive"] is True
    assert isinstance(h["zero_ingest_alarm"], bool)
