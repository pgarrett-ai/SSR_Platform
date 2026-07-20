"""PR-3: full 8-K item table, structural form detectors, ratings detector, the golden
Trinseo submissions fixture, and the route_audit exit-test instrument. Pure unit tests —
no network, no DB. CIKs are canonical 10-digit zero-padded (Interface Contract)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.events.edgar_feed as feed
from app.events.detectors_8k import ITEM_SPECS, detect_8k_items
from app.events.types import FilingMeta

CIK = "0001519061"
ACCN = "0000000000-26-000099"


def _meta(items, form="8-K", accession=ACCN):
    return FilingMeta(cik=CIK, form=form, filing_date="2026-05-26",
                      accession_no=accession,
                      source_url="https://www.sec.gov/x-index.htm",
                      items=items,
                      items_unknown=(form.startswith("8-K") and not items))


# --- Task 18: the full 16-item 8-K table -------------------------------------

def test_all_sixteen_plan_items_present():
    assert set(ITEM_SPECS) == {"1.01", "1.02", "1.03", "2.01", "2.03", "2.04", "2.05",
                               "2.06", "3.01", "3.02", "4.01", "4.02", "5.02", "5.07",
                               "7.01", "8.01"}


@pytest.mark.parametrize("item,spec", sorted(ITEM_SPECS.items()))
def test_each_item_fires_with_its_spec(item, spec):
    event_type, severity, _label = spec
    evs = detect_8k_items(_meta([item]), {"items": item}, None)
    assert len(evs) == 1
    e = evs[0]
    assert (e.event_type, e.subtype, e.severity, e.confidence) == (event_type, item, severity, 1.0)
    assert item in e.title


def test_204_acceleration_showcase():
    evs = detect_8k_items(_meta(["2.04"]), {"items": "2.04"}, None)
    assert len(evs) == 1
    e = evs[0]
    assert (e.event_type, e.subtype, e.severity, e.confidence) == ("acceleration", "2.04", 5, 1.0)
    # a 2.04 and a 1.03 on the SAME accession dedupe to DISTINCT rows (subtype is in the key)
    bk = detect_8k_items(_meta(["1.03"]), {"items": "1.03"}, None)[0]
    assert e.dedupe_key != bk.dedupe_key


def test_multi_item_one_event_per_tracked_item():
    evs = detect_8k_items(_meta(["2.04", "2.05", "9.01"]), {"items": "2.04,2.05,9.01"}, None)
    assert [e.subtype for e in evs] == ["2.04", "2.05"]      # 9.01 (exhibits): deliberate skip
    assert [e.event_type for e in evs] == ["acceleration", "exit_costs"]


# --- Task 19: structural (non-8-K) form detectors ----------------------------
from app.events import detectors_forms  # noqa: E402,F401 — import registers the detectors
from app.events.registry import detectors_for


def _fmeta(form):
    return FilingMeta(cik=CIK, form=form, filing_date="2026-05-26",
                      accession_no=ACCN, source_url="https://www.sec.gov/x-index.htm")


@pytest.mark.parametrize("form,event_type,severity", [
    ("NT 10-K", "late_filing", 4), ("NT 10-K/A", "late_filing", 4),
    ("NT 10-Q", "late_filing", 3),
    ("25", "delisting", 4), ("25-NSE", "delisting", 4),
    ("15-12B", "deregistration", 4),
    ("4", "insider_filing", 1), ("4/A", "insider_filing", 1),
    ("SC 13D", "stake_13d", 3), ("SC 13D/A", "stake_13d", 3),
    ("SC 13G", "stake_13g", 2), ("SC 13G/A", "stake_13g", 2),
])
def test_structural_form_detector(form, event_type, severity):
    meta = _fmeta(form)
    evs = [e for det in detectors_for(form) for e in det(meta, {}, None)]
    assert [(e.event_type, e.severity, e.subtype) for e in evs] == [(event_type, severity, form)]
    assert all(e.confidence == 1.0 for e in evs)


@pytest.mark.parametrize("form", ["424B5", "10-K", "DEF 14A", "S-1"])
def test_untracked_forms_route_nowhere(form):
    meta = _fmeta(form)
    assert [e for det in detectors_for(form) for e in det(meta, {}, None)] == []


# --- Task 20: 17g-7 ratings default detector ---------------------------------
from app.events.detectors_ratings import events_from_sd_rows


def test_ratings_events_pure_and_idempotent():
    rows = [{"cik": "1234", "name": "Alpha Airways, Inc.", "filed": "2020-05-01",
             "source": "fitch_rd"}]
    evs = events_from_sd_rows(rows)
    assert len(evs) == 1
    e = evs[0]
    assert (e.event_type, e.severity, e.source, e.source_form) == \
        ("rating_default", 5, "ratings", "17g-7")
    assert e.accession_no == "ratings:fitch_rd:0000001234:2020-05-01"
    assert e.cik == "0000001234"                        # padded via feed.pad_cik
    # pure + stable: same rows -> same dedupe key across calls (keys the idempotent insert)
    assert events_from_sd_rows(rows)[0].dedupe_key == e.dedupe_key


# --- Task 21: golden fixture (real captured Trinseo submissions doc) ----------
GOLD = Path(__file__).parent / "data" / "submissions_1519061_8k.json"


def test_golden_trinseo_8k_routing():
    arrays = json.loads(GOLD.read_text(encoding="utf-8"))["filings"]["recent"]
    pairs = feed.tracked_rows(arrays, "1519061", since=None, prefixes=("8-K",))
    assert pairs, "fixture must contain 8-K rows"
    evs = [e for meta, raw in pairs for e in detect_8k_items(meta, raw, None)]
    bk = [e for e in evs if e.event_type == "bankruptcy"]
    assert bk, "Trinseo Ch.11 fixture must yield an Item 1.03 bankruptcy event"
    assert all(e.severity == 5 and e.cik == "0001519061" for e in bk)
    assert bk[0].source_url and bk[0].accession_no.replace("-", "") in bk[0].source_url


# --- Task 22: route_audit exit-test instrument (pure path only) ---------------

def test_route_audit_rows_pure():
    from app.events.route_audit import audit_rows
    meta_pairs = feed.tracked_rows(
        json.loads(GOLD.read_text(encoding="utf-8"))["filings"]["recent"],
        "1519061", since=None, prefixes=("8-K",))
    rows = audit_rows(meta_pairs)
    assert rows and set(rows[0]) == {"accession_no", "cik", "form", "filing_date",
                                     "items", "routed_event_types", "accepted_at"}
    bk = [r for r in rows if "bankruptcy" in r["routed_event_types"]]
    assert bk and "1.03" in bk[0]["items"]
