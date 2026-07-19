"""edgar.http: the one paced EDGAR transport — User-Agent, global min-interval pacing,
429/403 backoff. The transport (_urlopen) and the clock/sleep seams are monkeypatched —
no network, no real sleeping."""
from __future__ import annotations

import urllib.error

import pytest

import app.edgar.http as eh
from app.core.config import get_settings


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _frozen(monkeypatch):
    """Fresh pacing state, frozen clock, recorded (instant) sleeps."""
    sleeps: list[float] = []
    monkeypatch.setattr(eh, "_next_slot", 0.0)
    monkeypatch.setattr(eh, "_now", lambda: 1000.0)
    monkeypatch.setattr(eh, "_sleep", sleeps.append)
    return sleeps


def _no_pace(monkeypatch):
    """Pacing off, sleeps recorded — backoff tests see only backoff sleeps."""
    sleeps: list[float] = []
    monkeypatch.setattr(eh, "_pace", lambda: None)
    monkeypatch.setattr(eh, "_sleep", sleeps.append)
    return sleeps


def test_sends_sec_user_agent(monkeypatch):
    _frozen(monkeypatch)
    seen = {}

    def fake(req, timeout):
        seen["ua"], seen["timeout"] = req.get_header("User-agent"), timeout
        return _Resp(b'{"ok": 1}')

    monkeypatch.setattr(eh, "_urlopen", fake)
    assert eh.paced_get("https://data.sec.gov/x", timeout=12.0) == b'{"ok": 1}'
    assert seen["ua"] == get_settings().sec_user_agent
    assert seen["timeout"] == 12.0


def test_global_min_interval_between_requests(monkeypatch):
    sleeps = _frozen(monkeypatch)
    monkeypatch.setattr(eh, "_urlopen", lambda req, timeout: _Resp(b"x"))
    eh.paced_get("https://data.sec.gov/a")      # first slot free -> no sleep
    eh.paced_get("https://data.sec.gov/b")      # same frozen instant -> one interval wait
    assert sleeps == [pytest.approx(1.0 / get_settings().edgar_max_requests_per_sec)]


def test_429_backs_off_then_succeeds(monkeypatch):
    sleeps = _no_pace(monkeypatch)
    calls = {"n": 0}

    def flaky(req, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(req.full_url, 429, "slow down", None, None)
        return _Resp(b"ok")

    monkeypatch.setattr(eh, "_urlopen", flaky)
    assert eh.paced_get("https://data.sec.gov/x") == b"ok"
    assert sleeps == [2.0, 4.0]                 # exponential: 2s then 4s


def test_retry_after_header_wins_over_exponential(monkeypatch):
    import email.message

    sleeps = _no_pace(monkeypatch)
    hdrs = email.message.Message()
    hdrs["Retry-After"] = "7"
    calls = {"n": 0}

    def flaky(req, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "slow down", hdrs, None)
        return _Resp(b"ok")

    monkeypatch.setattr(eh, "_urlopen", flaky)
    assert eh.paced_get("https://data.sec.gov/x") == b"ok"
    assert sleeps == [7.0]


def test_403_exhausts_backoff_then_raises(monkeypatch):
    sleeps = _no_pace(monkeypatch)

    def banned(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 403, "forbidden", None, None)

    monkeypatch.setattr(eh, "_urlopen", banned)
    with pytest.raises(urllib.error.HTTPError):
        eh.paced_get("https://www.sec.gov/x")
    assert sleeps == [2.0, 4.0, 8.0]            # 4 tries, 3 backoffs, then propagates


def test_other_errors_propagate_untouched(monkeypatch):
    # 5xx/transport retries belong to the CALLERS (eightk._cached, labels' FTS loop)
    sleeps = _no_pace(monkeypatch)

    def down(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "boom", None, None)

    monkeypatch.setattr(eh, "_urlopen", down)
    with pytest.raises(urllib.error.HTTPError):
        eh.paced_get("https://data.sec.gov/x")
    assert sleeps == []


def test_eightk_fetches_via_paced_get(monkeypatch):
    import app.capstack.eightk as eightk

    monkeypatch.setattr(eightk, "paced_get", lambda url, **kw: b'{"a": 1}')
    assert eightk._http_get_json(
        "https://data.sec.gov/submissions/CIK0000320193.json") == {"a": 1}
