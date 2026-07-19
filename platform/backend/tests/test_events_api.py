"""PR-5: the /api/events feed and /api/company/{ticker}/timeline merge.
DB-only — no network, no pipeline; events are seeded straight into the store."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import models_events as me
from app.core.db import session_scope
from app.main import app

# Context-managed so the lifespan handler runs (init_db creates the events table).
client = TestClient(app).__enter__()

CIK = "0009990001"   # test-only padded CIK, distinct from anything other suites seed


def _ev(**kw):
    base = dict(cik=CIK, event_type="bankruptcy", subtype="2.04", severity=4,
                confidence=1.0, occurred_at=dt.datetime(2026, 7, 1),
                detected_at=dt.datetime(2026, 7, 1, 12), source="edgar",
                source_form="8-K", accession_no=None,
                source_url="https://www.sec.gov/Archives/edgar/data/9990001/x-index.htm",
                title="8-K Item 2.04 — acceleration", payload={},
                dedupe_key=kw.pop("dedupe_key"))
    base.update(kw)
    return me.Event(**base)


@pytest.fixture(scope="module", autouse=True)
def seed():
    with session_scope() as s:
        s.merge(me.UniverseCompany(cik=CIK, ticker="EVTX", name="Eventful Corp",
                                   is_active=True))
        s.add_all([
            _ev(dedupe_key="pr5-e1"),
            _ev(dedupe_key="pr5-e2", event_type="late_filing", subtype=None, severity=3,
                source_form="NT 10-K", occurred_at=dt.datetime(2026, 6, 15),
                detected_at=dt.datetime(2026, 6, 15, 9), title="NT 10-K — late filing"),
            # backfill row: detected_at NULL, never faked (plan §5)
            _ev(dedupe_key="pr5-e3", severity=5, subtype="1.03",
                occurred_at=dt.datetime(2024, 3, 1), detected_at=None,
                accession_no="0000999-24-000001",
                title="8-K Item 1.03 — bankruptcy (backfill)"),
        ])


def test_filter_by_ticker_orders_detected_desc_nulls_last():
    r = client.get("/api/events?ticker=EVTX")
    assert r.status_code == 200, r.text
    evs = r.json()["events"]
    assert [e["cik"] for e in evs] == [CIK] * 3
    assert [e["title"][:12] for e in evs] == ["8-K Item 2.0", "NT 10-K — la", "8-K Item 1.0"]
    assert evs[-1]["detected_at"] is None          # backfill sorts last
    assert all(e["source_url"] for e in evs)       # deep links present
    assert evs[0]["ticker"] == "EVTX"              # universe join fills display ticker


def test_filter_by_raw_cik_pads():
    assert len(client.get("/api/events?cik=9990001").json()["events"]) == 3


def test_unknown_ticker_404():
    r = client.get("/api/events?ticker=ZZZZNOPE")
    assert r.status_code == 404 and r.json()["error"] == "ticker_not_found"


def test_event_type_repeatable_and_min_severity():
    evs = client.get("/api/events?ticker=EVTX&event_type=bankruptcy&event_type=late_filing"
                     ).json()["events"]
    assert len(evs) == 3
    assert len(client.get("/api/events?ticker=EVTX&event_type=late_filing"
                          ).json()["events"]) == 1
    evs = client.get("/api/events?ticker=EVTX&min_severity=4").json()["events"]
    assert sorted(e["severity"] for e in evs) == [4, 5]


def test_since_until_filter_occurred_at():
    evs = client.get("/api/events?ticker=EVTX&since=2026-01-01").json()["events"]
    assert len(evs) == 2                            # 2024 backfill excluded
    evs = client.get("/api/events?ticker=EVTX&until=2026-06-30").json()["events"]
    assert len(evs) == 2                            # July row excluded (until inclusive)


def test_limit_offset_paging():
    page1 = client.get("/api/events?ticker=EVTX&limit=2").json()["events"]
    page2 = client.get("/api/events?ticker=EVTX&limit=2&offset=2").json()["events"]
    assert len(page1) == 2 and len(page2) == 1
    assert page1[0]["id"] != page2[0]["id"]


def test_input_bounds_and_charset():
    assert client.get("/api/events?limit=501").status_code == 422        # Query(le=500)
    assert client.get("/api/events?offset=-1").status_code == 422
    assert client.get("/api/events?cik=12ab").status_code == 422         # digits only
    assert client.get("/api/events?ticker=..%2fetc").status_code == 422  # charset pattern
    assert client.get("/api/events?since=2026-13-40").status_code == 400 # real-date check
    assert client.get("/api/events?event_type=..%2fx").status_code == 400


# ---- /api/company/{ticker}/timeline: events + cached filings + what-changed --------

def _fake_overview():
    """A cached Overview (2 filings, one what-changed row) — the first source shares its
    accession with the backfilled 1.03 event so the merge must drop the duplicate filing."""
    from app.schemas import ChangeItem, FilingRef, IssuerHeader, Overview
    return Overview(
        header=IssuerHeader(ticker="EVTX", years=3, cik=CIK),
        what_changed=[ChangeItem(metric="Net leverage", delta_pct=42.0, direction="worse",
                                 latest_fy=2025, prior_fy=2024)],
        sources=[
            FilingRef(accession_no="0000999-24-000001", form_type="8-K",   # dup -> dropped
                      filing_date="2024-03-01",
                      filing_index_url="https://www.sec.gov/dup-index.htm"),
            FilingRef(accession_no="0000888-25-000009", form_type="10-K",   # kept
                      filing_date="2025-11-15",
                      filing_index_url="https://www.sec.gov/10k-index.htm"),
        ],
    )


def test_timeline_merges_events_filings_changes(monkeypatch):
    from app import main
    monkeypatch.setattr(main, "load_overview", lambda ticker, years: _fake_overview())
    r = client.get("/api/company/EVTX/timeline")
    assert r.status_code == 200, r.text
    body = r.json()
    items = body["items"]
    assert {it["kind"] for it in items} == {"event", "filing", "changes"}

    # accession-dedupe drops the filing that already exists as the backfilled 1.03 event
    filings = [it for it in items if it["kind"] == "filing"]
    assert [f["accession_no"] for f in filings] == ["0000888-25-000009"]

    # the what-changed card is dated to the FY period-end (derived display, not a filing date)
    changes = [it for it in items if it["kind"] == "changes"]
    assert changes[0]["date"] == "2025-12-31"

    # one merged vertical, newest-first (None-dated rows sort last)
    dates = [it["date"] for it in items if it["date"]]
    assert dates == sorted(dates, reverse=True)
    assert items[0]["kind"] == "event" and items[0]["date"] == "2026-07-01"
    assert body["cik"] == CIK
    assert body["note"] is None            # a cached overview is present


def test_timeline_is_cache_only(monkeypatch):
    """A page mount must never launch a live pipeline run (PR-B). With no cache the
    endpoint still returns 200, a build-me note, and the DB event rows."""
    from app import main

    def _boom(*a, **k):
        raise AssertionError("timeline must not trigger a live pipeline run")

    monkeypatch.setattr(main, "run_overview", _boom)
    monkeypatch.setattr(main, "load_overview", lambda *a, **k: None)
    monkeypatch.setattr(main, "load_latest_overview", lambda *a, **k: None)
    r = client.get("/api/company/EVTX/timeline")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["note"]                    # "open the Overview tab once" hint
    events = [it for it in body["items"] if it["kind"] == "event"]
    assert len(events) == 3                 # DB rows still served, cache-free
    assert all(it["kind"] == "event" for it in body["items"])  # nothing but events w/o a cache
