"""python -m app.worker — the Phase-6 ingestion daemon.

One plain synchronous loop, zero scheduler dependencies. Justification: every job is a
sequential network-bound call under the single global EDGAR pacing budget, so
concurrency buys nothing; APScheduler is a new dependency for four fixed cadences;
asyncio adds Windows-Proactor Ctrl+C pain for zero parallel I/O. KeyboardInterrupt out
of time.sleep is the one Ctrl+C path that Just Works on Windows. Mid-job kills are
safe: ingest is idempotent (dedupe_key) and backfill checkpoints per CIK. Re-entry
trigger for asyncio: a job that genuinely needs parallelism (Phase 7 LLM queue)."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time

from .core.config import DATA_DIR

LOCK_PATH = DATA_DIR / "worker.lock"
LOCK_STALE_S = 900
TICK_S = 30


class Job:
    def __init__(self, name: str, every_s: int, fn):
        self.name, self.every_s, self.fn = name, every_s, fn
        self.next_run = 0.0            # due immediately unless restored
        self.last_ok: str | None = None

    def restore(self, last_ok_iso: str | None) -> None:
        """Resume cadence across restarts (a restart must not re-download the monthly
        ratings CSVs)."""
        if not last_ok_iso:
            return
        try:
            t = dt.datetime.fromisoformat(last_ok_iso).timestamp()
        except ValueError:
            return
        self.last_ok, self.next_run = last_ok_iso, t + self.every_s


def build_jobs() -> list[Job]:
    from .events import poller, universe
    return [
        Job("poll_filings", 300, poller.poll_once),
        Job("catchup_form_idx", 86_400, poller.catchup_form_idx),
        Job("universe_refresh", 86_400, universe.refresh_universe),
        # PR-3 appends: Job("ratings_refresh", 30 * 86_400, refresh_ratings)
    ]


def acquire_lock() -> bool:
    """Single-instance guard. ponytail: freshness-based (ts refreshed each tick,
    stale after 15 min) instead of PID liveness probes — os.kill(pid, 0) is not a
    safe existence check on Windows."""
    if LOCK_PATH.exists():
        try:
            blob = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
            if time.time() - float(blob.get("ts") or 0) < LOCK_STALE_S:
                return False
        except Exception:
            pass                        # corrupt lock -> treat as stale
    refresh_lock()
    return True


def refresh_lock() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}),
                         encoding="utf-8")


def _tick_bookkeeping(jobs, ingested) -> None:
    """Heartbeat + lock refresh; an FS hiccup (AV lock, disk full) must never kill the
    eternal loop — WARN and carry on, the next tick retries."""
    from .events import heartbeat
    try:
        heartbeat.beat(ingested, {j.name: j.last_ok for j in jobs})
        refresh_lock()
    except OSError as exc:
        print(f"WARN heartbeat/lock write: {type(exc).__name__}: {exc}")


def run_due(jobs: list[Job], now: float) -> int:
    """Run every due job once; a failing job WARNs and waits its full period (no hot
    loop on a broken feed). Returns events ingested this pass."""
    ingested = 0
    for job in jobs:
        if now < job.next_run:
            continue
        try:
            n = job.fn()
            ingested += int(n or 0)
            job.last_ok = dt.datetime.now().isoformat(timespec="seconds")
        except Exception as exc:
            print(f"WARN {job.name}: {type(exc).__name__}: {exc}")
        job.next_run = now + job.every_s
    return ingested


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="backslashreplace")   # cp1252 log + unicode WARN
    except AttributeError:
        pass
    from .core.db import init_db
    from .events import heartbeat
    if not acquire_lock():
        print(f"another worker holds {LOCK_PATH} (fresh heartbeat) — exiting")
        return 1
    init_db()
    jobs = build_jobs()
    prev_jobs = heartbeat._read().get("jobs") or {}
    for j in jobs:
        j.restore(prev_jobs.get(j.name))
    print(f"worker up (pid {os.getpid()}); jobs: {[j.name for j in jobs]}; Ctrl+C stops")
    try:
        while True:
            n = run_due(jobs, time.time())
            _tick_bookkeeping(jobs, n)
            time.sleep(TICK_S)
    except KeyboardInterrupt:
        print("worker: shutting down")
    finally:
        try:
            heartbeat.beat(0, {j.name: j.last_ok for j in jobs}, stopping=True)
        except OSError:
            pass
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
