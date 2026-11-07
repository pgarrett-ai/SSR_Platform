"""Demo reliability: concurrent identical requests run the pipeline once."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pytest

from app import pipeline
from app.core import cache
from app.core.progress import ProgressLog
from app.schemas import IssuerHeader, Overview


@pytest.mark.parametrize("live", [False, True])
def test_concurrent_runs_for_one_key_build_once(monkeypatch, tmp_path, live):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    builds: list[str] = []
    second_waiting = threading.Event()

    def slow_build(ticker, years, progress):
        builds.append(ticker)
        second_waiting.wait(2)   # hold the key until the second request is queued on it
        ov = Overview(header=IssuerHeader(
            ticker=ticker, years=years, last_updated=datetime.now(timezone.utc).isoformat()))
        cache.cache_path(ticker, years).write_text(ov.model_dump_json(), encoding="utf-8")
        return ov

    monkeypatch.setattr(pipeline, "_build_overview", slow_build)
    results: list[Overview] = []

    def request(progress=None):
        results.append(pipeline.run_overview("ZZSF", 3, progress=progress, live=live))

    def on_progress(evt):
        if evt.message.startswith("Waiting"):
            second_waiting.set()

    first = threading.Thread(target=request)
    first.start()
    while not builds and first.is_alive():
        time.sleep(0.01)
    second = threading.Thread(target=request, args=(ProgressLog(sink=on_progress),))
    second.start()
    first.join(5)
    second.join(5)

    assert builds == ["ZZSF"]
    assert len(results) == 2
    assert results[0].header.last_updated == results[1].header.last_updated
