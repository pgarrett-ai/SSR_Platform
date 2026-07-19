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
