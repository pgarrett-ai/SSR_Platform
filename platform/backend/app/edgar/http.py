"""The one paced EDGAR transport (Phase 6 PR-1).

Every raw outbound EDGAR request (data.sec.gov, www.sec.gov, efts.sec.gov) leaves through
`paced_get`, which owns the three SEC fair-access concerns in exactly one place:

  * **User-Agent** — read from `Settings.sec_user_agent`, the same single config value
    `edgar/client._ensure_identity()` feeds to edgartools. edgartools paces and identifies
    its own internal requests; this module covers everything we fetch ourselves. Stdlib
    only — no edgartools import.
  * **Global min-interval pacing** — default 8 req/s (`edgar_max_requests_per_sec`), under
    SEC's 10 req/s fair-access limit. Thread-safe: the Phase-6 poller worker and API
    request threads share one budget.
  * **429/403 exponential backoff** — Retry-After honored when parseable; after three
    backoffs the error propagates.

Transient 5xx / socket errors are deliberately NOT retried here — callers keep their own
retry semantics (`capstack.eightk._cached`, the FTS loop in `hazard.labels`).
"""
from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request

from ..core.config import get_settings

# clock/sleep seams so tests never really sleep (house pattern: module-level names)
_now = time.monotonic
_sleep = time.sleep

_lock = threading.Lock()
_next_slot = 0.0        # monotonic time at which the next request may fire

_MAX_TRIES = 4          # one try + three 429/403 backoffs: 2s, 4s, 8s
_BACKOFF_S = 2.0


def _urlopen(req: urllib.request.Request, timeout: float):
    """The real socket — monkeypatched in tests."""
    return urllib.request.urlopen(req, timeout=timeout)   # pragma: no cover


def _pace() -> None:
    """Reserve the next global request slot; sleep until it opens. Slots are handed out
    under the lock and slept on outside it, so concurrent threads space out instead of
    stampeding."""
    # ponytail: one global budget; split per-host only if a second rate-limited host appears
    global _next_slot
    with _lock:
        now = _now()
        wait = _next_slot - now
        # max(0.1, ...) clamps a misconfigured rate: 0 would ZeroDivisionError, <0 silently disable pacing
        _next_slot = max(now, _next_slot) + 1.0 / max(0.1, get_settings().edgar_max_requests_per_sec)
    if wait > 0:
        _sleep(wait)


def paced_get(url: str, *, timeout: float = 30.0) -> bytes:
    """GET `url` with the SEC User-Agent, globally paced, retrying 429/403 with
    exponential backoff. Every other failure propagates untouched."""
    req = urllib.request.Request(url, headers={"User-Agent": get_settings().sec_user_agent})
    for attempt in range(_MAX_TRIES):
        _pace()
        try:
            with _urlopen(req, timeout) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 403) or attempt == _MAX_TRIES - 1:
                raise
            try:
                delay = float(exc.headers.get("Retry-After")) if exc.headers else None
            except (TypeError, ValueError):
                delay = None
            # clamp guards a hostile/buggy Retry-After: max(0,...) — sleep raises on <0; min(60,...)
            # bounds absurdly large values (e.g. Retry-After: 86400) from pinning a shared thread
            _sleep(min(60.0, max(0.0, delay)) if delay is not None else _BACKOFF_S * 2 ** attempt)
    raise AssertionError("unreachable")   # pragma: no cover
