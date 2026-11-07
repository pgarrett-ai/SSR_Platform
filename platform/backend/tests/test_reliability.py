"""Demo reliability: concurrent identical requests run the pipeline once; a failed hazard
run falls back to the last cached payload; an EDGAR outage reports its real cause."""
from __future__ import annotations

import json
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


def test_failed_hazard_run_serves_newest_cache_marked_stale(monkeypatch, tmp_path):
    import app.main as main

    monkeypatch.setattr(main, "CACHE_DIR", tmp_path)
    (tmp_path / "hazard").mkdir()
    (tmp_path / "hazard" / "ZZHZ_10y.json").write_text(
        json.dumps({"as_of": "2026-01-02", "data": {"scores": {"Merton DD": {"pd": 0.2}}}}),
        encoding="utf-8")

    def edgar_down(*a, **k):
        raise ConnectionError("SEC unreachable")

    monkeypatch.setattr(main, "hazard_analyze", edgar_down)
    assert main._hazard_section("ZZHZ", 10, live=True) == {
        "scores": {"Merton DD": {"pd": 0.2}}, "stale_as_of": "2026-01-02"}
    with pytest.raises(ConnectionError):   # nothing cached: the real cause surfaces
        main._hazard_section("ZZNONE", 10, live=False)


def test_edgar_outage_is_a_pipeline_error_not_an_unknown_ticker(monkeypatch):
    from fastapi.testclient import TestClient

    import app.edgar.client as edgar_client
    from app.main import app

    def edgar_down(identifier):
        raise ConnectionError("SEC unreachable")

    monkeypatch.setattr(edgar_client, "Company", edgar_down)
    r = TestClient(app).get("/api/overview", params={"ticker": "ZZNET", "live": True})
    assert r.status_code == 500
    assert r.json() == {"error": "pipeline_error", "detail": "SEC unreachable"}
