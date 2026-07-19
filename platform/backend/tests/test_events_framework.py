"""Phase 6 event framework: dataclasses/dedupe, registry routing, idempotent store,
poller end-to-end with every network seam monkeypatched. No network, temp DB only.
CIKs everywhere are the canonical 10-digit zero-padded form (Interface Contract)."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from app.events.types import Event, FilingMeta

CIK = "0000000777"


def _ev(**kw):
    base = dict(cik=CIK, event_type="bankruptcy", subtype="1.03", severity=5,
                confidence=1.0, occurred_at="2026-01-02", source="edgar",
                source_form="8-K", accession_no="0000000000-26-000001",
                source_url=None, title="t", payload={})
    base.update(kw)
    return Event(**base)


def test_dedupe_key_stable_and_delegates_to_models_events():
    from app.models_events import make_dedupe_key
    assert _ev().dedupe_key == make_dedupe_key("0000000000-26-000001", "bankruptcy", "1.03")
    assert _ev(subtype="2.04").dedupe_key != _ev().dedupe_key
    assert _ev(accession_no="x-2").dedupe_key != _ev().dedupe_key
    assert len(_ev().dedupe_key) == 64


def test_event_bounds_enforced():
    with pytest.raises(ValueError):
        _ev(severity=6)
    with pytest.raises(ValueError):
        _ev(confidence=1.5)


# --- Task 14: registry + feed seams ------------------------------------------
from app.events import registry
import app.events.edgar_feed as feed


def test_form_matches_prefix_rules():
    assert registry.form_matches("8-K/A", "8-K") is True
    assert registry.form_matches("8-K", "8-K") is True
    assert registry.form_matches("15-12B", "15") is True      # '15' covers 15-12B/15-15D
    assert registry.form_matches("424B5", "4") is False       # a prefix is not a text prefix
    assert registry.form_matches("NT 10-K", "10-K") is False  # "NT 10-K" is its own form


def test_register_and_route_round_trip():
    @registry.register("ZZZ-TEST")
    def _det(meta, raw, row):
        return []
    assert _det in registry.detectors_for("ZZZ-TEST")
    assert _det in registry.detectors_for("ZZZ-TEST/A")
    assert _det not in registry.detectors_for("8-K")


def _arrays():
    return {"form":            ["8-K",        "10-K",       "NT 10-K",    "8-K"],
            "filingDate":      ["2026-07-17", "2026-03-01", "2026-05-01", "2026-07-16"],
            "accessionNumber": ["a-26-4",     "a-26-3",     "a-26-2",     "a-26-1"],
            "items":           ["1.03",       "",           "",           ""],
            "acceptanceDateTime": ["2026-07-17T16:31:02.000Z", "", "", ""]}


def test_tracked_rows_generalizes_eightk_parser():
    pairs = feed.tracked_rows(_arrays(), "777", since=None, prefixes=("8-K", "NT 10-K"))
    metas = [m for m, raw in pairs]
    assert [m.form for m in metas] == ["8-K", "NT 10-K", "8-K"]      # 10-K excluded
    assert metas[0].items == ["1.03"] and metas[0].items_unknown is False
    assert metas[2].items is None and metas[2].items_unknown is True  # items-less 8-K
    assert metas[1].items is None and metas[1].items_unknown is False # non-8-K: no items
    assert metas[0].cik == "0000000777"                               # canonical padded
    assert metas[0].accepted_at == "2026-07-17T16:31:02.000Z"
    assert metas[1].accepted_at is None
    assert pairs[0][1]["items"] == "1.03"                             # raw header intact


def test_tracked_rows_since_filter():
    pairs = feed.tracked_rows(_arrays(), "777", since="2026-07-17", prefixes=("8-K",))
    assert [m.filing_date for m, _ in pairs] == ["2026-07-17"]


def test_efts_hits_pages_like_labels(monkeypatch):
    pages = {0: {"hits": {"total": {"value": 12},
                          "hits": [{"_source": {"ciks": ["1"]}}] * 10}},
             10: {"hits": {"total": {"value": 12},
                           "hits": [{"_source": {"ciks": ["2"]}}] * 2}}}
    urls = []

    def fake_get_json(url, timeout=30.0):
        urls.append(url)
        frm = int(url.rsplit("from=", 1)[1])
        return pages[frm]

    monkeypatch.setattr(feed, "get_json", fake_get_json)
    hits = feed.efts_hits({"q": '"Item 1.03"', "forms": "8-K",
                           "startdt": "2026-07-17", "enddt": "2026-07-18"})
    assert len(hits) == 12 and len(urls) == 2
    assert urls[0].startswith("https://efts.sec.gov/LATEST/search-index?")


# --- Task 15: store adapter + seeded 8-K detector ----------------------------
from app import models_events as me
from app.core.db import init_db, session_scope
from app.events import store


def test_store_adapter_idempotent_and_detected_at_discipline():
    init_db()
    with session_scope() as s:
        assert store.insert_events(s, [_ev()], detected_at=dt.datetime(2026, 1, 2, 12)) == 1
        assert store.insert_events(s, [_ev(), _ev()], detected_at=dt.datetime(2026, 1, 2, 13)) == 0
        assert store.has_event(s, CIK, "2026-01-02", ("bankruptcy",))
        assert not store.has_event(s, CIK, "2026-01-02", ("acceleration",))
        # backfill rows: detected_at stays NULL, never faked (plan §10)
        assert store.insert_events(s, [_ev(accession_no="x-2")], detected_at=None) == 1
    with session_scope() as s:
        row = s.query(me.Event).filter_by(accession_no="x-2").one()
        assert row.detected_at is None
        assert row.dedupe_key == _ev(accession_no="x-2").dedupe_key
        assert row.occurred_at == dt.datetime(2026, 1, 2)   # ISO date -> naive midnight


from app.events.detectors_8k import ITEM_SPECS, detect_8k_items


def _meta(items, form="8-K"):
    return FilingMeta(cik=CIK, form=form, filing_date="2026-05-01",
                      accession_no="0000000000-26-000007",
                      source_url="https://www.sec.gov/x-index.htm",
                      items=items, items_unknown=(form.startswith("8-K") and items is None))


def test_103_bankruptcy_detector():
    evs = detect_8k_items(_meta(["1.03", "9.01"]), {"items": "1.03,9.01"}, None)
    assert len(evs) == 1                                   # 9.01 (exhibits) deliberate skip
    e = evs[0]
    assert (e.event_type, e.subtype, e.severity, e.confidence) == ("bankruptcy", "1.03", 5, 1.0)
    assert e.occurred_at == "2026-05-01" and e.source_form == "8-K"
    assert e.accession_no == "0000000000-26-000007" and "1.03" in e.title


def test_unknown_items_never_silent():
    evs = detect_8k_items(_meta(None), {"items": ""}, None)
    assert [e.event_type for e in evs] == ["8k_items_unknown"]
    assert evs[0].severity == 1 and evs[0].confidence == 0.5
