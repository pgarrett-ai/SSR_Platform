"""PR-D: Moyer F2 docket events, Layer A (manual ingest).
Manual analyst-entered Chapter-11 milestone events (petition/DIP/363/plan/confirmation…)
written straight into the Phase-6 event store via a direct store call — no registry
detector, no schema change (source='manual' already anticipated in models_events.py)."""
import pytest
from fastapi.testclient import TestClient

from app.core.db import session_scope
from app.main import app, _docket_event, DocketBody

# Context-managed so the lifespan handler runs (init_db creates the events table).
client = TestClient(app).__enter__()

CIK = "0009990002"   # test-only padded CIK, distinct from other suites (see test_events_api.py)
TICKER = "DKTX"


@pytest.fixture(scope="module", autouse=True)
def seed():
    from app import models_events as me
    with session_scope() as s:
        s.merge(me.UniverseCompany(cik=CIK, ticker=TICKER, name="Docket Test Corp",
                                   is_active=True))


def test_post_petition_inserts_and_appears_in_events_feed():
    r = client.post(f"/api/company/{TICKER}/recovery/docket", json={
        "subtype": "petition", "occurred_at": "2026-03-01", "title": "Voluntary petition filed"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inserted"] == 1
    assert body["dedupe_key"]

    evs = client.get(f"/api/events?ticker={TICKER}&event_type=docket").json()["events"]
    assert len(evs) == 1
    ev = evs[0]
    assert ev["severity"] == 5
    assert ev["source"] == "manual"
    assert ev["subtype"] == "petition"


def test_repost_same_body_is_idempotent():
    body = {"subtype": "dip", "occurred_at": "2026-03-05", "title": "DIP financing approved"}
    r1 = client.post(f"/api/company/{TICKER}/recovery/docket", json=body)
    assert r1.status_code == 200 and r1.json()["inserted"] == 1
    r2 = client.post(f"/api/company/{TICKER}/recovery/docket", json=body)
    assert r2.status_code == 200
    assert r2.json()["inserted"] == 0
    assert r2.json()["note"] == "already recorded (idempotent)"


def test_bad_subtype_400():
    r = client.post(f"/api/company/{TICKER}/recovery/docket", json={
        "subtype": "foo", "occurred_at": "2026-03-01", "title": "x"})
    assert r.status_code == 400


def test_javascript_source_url_400():
    r = client.post(f"/api/company/{TICKER}/recovery/docket", json={
        "subtype": "plan", "occurred_at": "2026-03-01", "title": "x",
        "source_url": "javascript:alert(1)"})
    assert r.status_code == 400


def test_unknown_ticker_404():
    r = client.post("/api/company/ZZZZNOPE/recovery/docket", json={
        "subtype": "plan", "occurred_at": "2026-03-01", "title": "x"})
    assert r.status_code == 404


def test_empty_title_422():
    r = client.post(f"/api/company/{TICKER}/recovery/docket", json={
        "subtype": "plan", "occurred_at": "2026-03-01", "title": ""})
    assert r.status_code == 422


def test_dedupe_key_distinct_across_ciks():
    """Pure unit assert on _docket_event: proves the cik-in-accession fix — make_dedupe_key
    itself omits cik, so without the synthetic-accession embedding, two issuers filing the
    same milestone on the same date would collide (models_events.py make_dedupe_key)."""
    b = DocketBody(subtype="petition", occurred_at="2026-05-01", title="Voluntary petition filed")
    ev1 = _docket_event("0000000001", b)
    ev2 = _docket_event("0000000002", b)
    assert ev1.dedupe_key != ev2.dedupe_key


def test_appears_in_timeline():
    r = client.post(f"/api/company/{TICKER}/recovery/docket", json={
        "subtype": "confirmation", "occurred_at": "2026-04-01", "title": "Plan confirmed"})
    assert r.status_code == 200

    items = client.get(f"/api/company/{TICKER}/timeline").json()["items"]
    matches = [it for it in items if it.get("kind") == "event" and it.get("subtype") == "confirmation"]
    assert len(matches) == 1


def test_delete_manual_row_ok_non_manual_row_refuses():
    from app import models_events as me

    r = client.post(f"/api/company/{TICKER}/recovery/docket", json={
        "subtype": "effective", "occurred_at": "2026-04-10", "title": "Plan effective"})
    ev = client.get(f"/api/events?ticker={TICKER}&event_type=docket&min_severity=4").json()["events"]
    manual_id = next(e["id"] for e in ev if e["subtype"] == "effective")

    with session_scope() as s:
        s.merge(me.Event(id=999999001, cik=CIK, event_type="bankruptcy", subtype="1.03",
                         severity=5, confidence=1.0, occurred_at=__import__("datetime").datetime(2026, 4, 10),
                         source="edgar", source_form="8-K", accession_no="edgar:not-manual",
                         title="not manual", payload={}, dedupe_key="not-manual-dk"))

    assert client.delete(f"/api/events/{manual_id}").json()["deleted"] == 1
    assert client.delete("/api/events/999999001").json()["deleted"] == 0  # refuses non-manual

    remaining_ids = [e["id"] for e in client.get(f"/api/events?ticker={TICKER}").json()["events"]]
    assert manual_id not in remaining_ids           # deleted
    assert 999999001 in remaining_ids                # non-manual row untouched
