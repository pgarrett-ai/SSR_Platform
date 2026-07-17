"""Bank triage + filing telegraph (Moyer ch. 8): bank identification, the six states,
coupon-focal arithmetic, war-chest three-way, payables reuse, FTS recency, score."""
import datetime as dt

from fastapi.testclient import TestClient

from app import models
from app.capstack.triage import (_is_bank, bank_triage, events_from_ov,
                                 filing_telegraph)
from app.core import db
from app.core.db import session_scope
from app.main import app
from app.store import rebuild_fts

client = TestClient(app).__enter__()   # lifespan runs init_db (tables + FTS)


def _cv(v):
    return {"value": v, "derived": True, "formula": "test"}


def _ov(schedule=None, ebitda=100e6, forensic=None, flags=None, snapshot=None,
        asof="2026-06-30"):
    return {
        "header": {"issuer": "Test Co", "ticker": "ZZTRI"},
        "debt_schedule_asof": asof,
        "economic_debt_bridge": {"ebitda": _cv(ebitda)} if ebitda is not None else {},
        "forensic_table": forensic if forensic is not None else
                          [{"fiscal_year": 2024, "ebitda": _cv(ebitda), "cash": _cv(50e6)},
                           {"fiscal_year": 2025, "ebitda": _cv(ebitda), "cash": _cv(50e6)}],
        "forensic_flags": flags or [],
        "debt_schedule": schedule if schedule is not None else [],
        "asset_snapshot": snapshot,
        "covenants": [],
    }


# ---- bank identification ---------------------------------------------------------


def test_bank_id_name_fallback_and_type():
    # AAL tags no facility_type — the name regex is load-bearing
    assert _is_bank({"instrument": "2014 Revolving Facility"}) is True
    assert _is_bank({"instrument": "2021 AAdvantage Term Loan Facility"}) is True
    assert _is_bank({"instrument": "ABL Credit Facility"}) is True
    assert _is_bank({"instrument": "DDTL Credit Facility"}) is True
    assert _is_bank({"instrument": "5.75% Senior Notes"}) is False
    assert _is_bank({"instrument": "Special Facility Revenue Bonds"}) is False
    # tagged facility_type wins over the name
    assert _is_bank({"instrument": "X", "facility_type": "revolver"}) is True
    assert _is_bank({"instrument": "X", "facility_type": "commercial paper"}) is False
    assert _is_bank({"instrument": "X", "facility_type": "notes"}) is False


# ---- the six states --------------------------------------------------------------


def test_empty_schedule_unavailable():
    out = bank_triage(_ov(schedule=[]))
    assert out["available"] is False and "no debt schedule" in out["note"]


def test_no_bank_debt():
    sched = [{"instrument": "8% Senior Notes", "outstanding": _cv(300e6),
              "secured": False, "facility_type": "notes"}]
    out = bank_triage(_ov(schedule=sched))
    assert out["state"] == "no_bank_debt"


def test_security_grab_on_unsecured_facility():
    # name must not hit the classifier's secured regex ("revolv" would auto-secure it)
    sched = [{"instrument": "Working Capital Loan", "facility_type": "credit facility",
              "outstanding": _cv(200e6), "secured": False, "seniority": "unsecured"}]
    out = bank_triage(_ov(schedule=sched))
    assert out["state"] == "security_grab"
    assert out["rows"][0]["secured"] is False
    assert out["rows"][0]["secured_source"] == "tagged"


def test_undersecured_watch_and_waiver_path():
    # bank 500 vs EV 4x100 = 400 -> 80% at trough (short), covered at 6x -> watch
    sched = [{"instrument": "Term Loan B", "outstanding": _cv(500e6), "secured": True,
              "seniority": "senior secured", "facility_type": "term loan"}]
    out = bank_triage(_ov(schedule=sched, ebitda=100e6))
    assert out["state"] == "undersecured_watch"
    pts = out["coverage"]["points"]
    assert pts[0]["coverage_pct"] == 80.0 and pts[1]["coverage_pct"] == 100.0
    # bank 200 covered at trough 400 -> waiver path
    sched2 = [{"instrument": "Term Loan B", "outstanding": _cv(200e6), "secured": True,
               "seniority": "senior secured", "facility_type": "term loan"}]
    assert bank_triage(_ov(schedule=sched2, ebitda=100e6))["state"] == "waiver_path"


def test_filing_pretext_liquidation_basis():
    # bank 500, EBITDA −50, cash 100 -> orderly proceeds 100 × 93% = 93 -> 18.6%
    sched = [{"instrument": "Revolving Credit Facility", "outstanding": _cv(500e6),
              "secured": True, "facility_type": "revolver"}]
    snapshot = {"cash": _cv(100e6)}
    out = bank_triage(_ov(schedule=sched, ebitda=-50e6, snapshot=snapshot))
    assert out["state"] == "filing_pretext"
    assert out["coverage"]["basis"] == "liquidation"
    assert out["coverage"]["coverage_pct"] == 18.6
    assert out["coverage"]["net_proceeds_mm"] == 93.0


def test_coverage_unknown_without_snapshot():
    sched = [{"instrument": "Revolving Credit Facility", "outstanding": _cv(500e6),
              "secured": True, "facility_type": "revolver"}]
    out = bank_triage(_ov(schedule=sched, ebitda=-50e6, snapshot=None))
    assert out["state"] == "coverage_unknown"


def test_name_heuristic_source_surfaced():
    # LCID GIB shape: secured is None on the row -> classified 1L by name, tagged as such
    sched = [{"instrument": "2025 GIB Credit Facility", "outstanding": _cv(1890e6),
              "secured": None, "facility_type": "revolver"}]
    out = bank_triage(_ov(schedule=sched, ebitda=100e6))
    row = out["rows"][0]
    assert row["secured"] is True and row["lien_rank"] == 1
    assert row["secured_source"] == "name-heuristic"


# ---- telegraph signals -----------------------------------------------------------

STEELBOX = [{"instrument": "12% Senior Notes due 2031", "outstanding": _cv(150e6),
             "coupon_pct": 12.0, "maturity": "2031", "secured": False,
             "facility_type": "notes"}]


def _signal(tel, key):
    return next(s for s in tel["signals"] if s["key"] == key)


def test_steelbox_coupon_focal():
    # 150 × 12% ÷ 2 = 9.0M semiannual coupon vs 6.0M cash -> ON, grace stated
    ov = _ov(schedule=STEELBOX,
             forensic=[{"fiscal_year": 2024, "cash": _cv(10e6)},
                       {"fiscal_year": 2025, "cash": _cv(6e6)}])
    with session_scope() as s:
        tel = filing_telegraph(ov, s)
    sig = _signal(tel, "coupon_focal")
    assert sig["state"] == "on"
    assert sig["amount"]["value"] == 9e6
    assert "30 days" in sig["detail"] and "30-day grace" in sig["assumption"]
    # events recomputed, not read from the (absent) cached calendar
    cal = events_from_ov(ov)
    assert any(e.kind == "coupon" for e in cal["events"])
    assert cal["cash"] == 6e6


def test_coupon_focal_off_when_cash_covers():
    ov = _ov(schedule=STEELBOX,
             forensic=[{"fiscal_year": 2024, "cash": _cv(500e6)},
                       {"fiscal_year": 2025, "cash": _cv(500e6)}])
    with session_scope() as s:
        tel = filing_telegraph(ov, s)
    assert _signal(tel, "coupon_focal")["state"] == "off"


BANK_DRAWN = {"instrument": "Revolving Credit Facility", "outstanding": _cv(400e6),
              "secured": True, "facility_type": "revolver"}


def test_war_chest_three_way():
    # ON: drawn with zero tagged headroom
    ov = _ov(schedule=[BANK_DRAWN])
    with session_scope() as s:
        assert _signal(filing_telegraph(ov, s), "war_chest")["state"] == "on"
    # ON: drawn, headroom remains, but cash spiked > 1.25x prior
    spiked = _ov(schedule=[{**BANK_DRAWN, "undrawn": _cv(100e6)}],
                 forensic=[{"fiscal_year": 2024, "cash": _cv(100e6)},
                           {"fiscal_year": 2025, "cash": _cv(200e6)}])
    with session_scope() as s:
        sig = _signal(filing_telegraph(spiked, s), "war_chest")
    assert sig["state"] == "on" and "equity raise" in sig["detail"]
    # OFF: drawn, headroom remains, cash flat
    flat = _ov(schedule=[{**BANK_DRAWN, "undrawn": _cv(100e6)}])
    with session_scope() as s:
        assert _signal(filing_telegraph(flat, s), "war_chest")["state"] == "off"
    # UNKNOWN: fewer than two cash observations
    thin = _ov(schedule=[{**BANK_DRAWN, "undrawn": _cv(100e6)}],
               forensic=[{"fiscal_year": 2025, "cash": _cv(100e6)}])
    with session_scope() as s:
        assert _signal(filing_telegraph(thin, s), "war_chest")["state"] == "unknown"


def test_payables_stretch_reuses_forensic_flag():
    on = _ov(flags=[{"flag_type": "ap_outrunning_revenue", "severity": "watch",
                     "narrative": "DPO up 14 days"}])
    off = _ov()
    thin = _ov(forensic=[{"fiscal_year": 2025, "cash": _cv(1e6)}])
    with session_scope() as s:
        assert _signal(filing_telegraph(on, s), "payables_stretch")["state"] == "on"
        assert _signal(filing_telegraph(off, s), "payables_stretch")["state"] == "off"
        assert _signal(filing_telegraph(thin, s), "payables_stretch")["state"] == "unknown"


# ---- FTS scan: seed, recency, degradation ----------------------------------------


def _seed_mdna(text, period_end):
    with session_scope() as s:
        s.add(models.MdnaSection(ticker="ZZTRI", section_name="MD&A",
                                 period_end=period_end, text=text))
        rebuild_fts(s, "ZZTRI")


def _clean_fts():
    with session_scope() as s:
        for r in s.query(models.MdnaSection).filter_by(ticker="ZZTRI").all():
            s.delete(r)
        if db.FTS_AVAILABLE:
            from sqlalchemy import text
            s.execute(text("DELETE FROM search WHERE ticker = 'ZZTRI'"))


def test_fts_signal_on_with_snippet_evidence():
    if not db.FTS_AVAILABLE:
        return
    _seed_mdna("there is substantial doubt about our ability to continue as a going "
               "concern", dt.date(2026, 3, 31))
    try:
        with session_scope() as s:
            sig = _signal(filing_telegraph(_ov(), s), "advisor_going_concern")
        assert sig["state"] == "on"
        assert any("<mark>" in h["snippet"] for h in sig["evidence"])
    finally:
        _clean_fts()


def test_fts_stale_period_excluded():
    if not db.FTS_AVAILABLE:
        return
    _seed_mdna("substantial doubt about our ability to continue as a going concern",
               dt.date(2024, 3, 31))    # > 12 months before the 2026-06-30 as-of
    try:
        with session_scope() as s:
            assert _signal(filing_telegraph(_ov(), s),
                           "advisor_going_concern")["state"] == "off"
    finally:
        _clean_fts()


def test_fts_unavailable_reads_unknown_and_leaves_denominator(monkeypatch):
    monkeypatch.setattr(db, "FTS_AVAILABLE", False)
    with session_scope() as s:
        tel = filing_telegraph(_ov(), s)
    assert _signal(tel, "default_disclosure")["state"] == "unknown"
    assert _signal(tel, "advisor_going_concern")["state"] == "unknown"
    # both FTS signals out of the denominator
    assert tel["score"]["evaluable"] == sum(
        1 for s_ in tel["signals"] if s_["state"] != "unknown")


def test_score_on_2_of_4():
    # coupon ON (9 vs 6), payables ON (flag), war_chest UNKNOWN (one cash row),
    # FTS signals OFF (no hits) -> {on: 2, evaluable: 4}
    ov = _ov(schedule=STEELBOX + [{**BANK_DRAWN, "undrawn": _cv(100e6)}],
             forensic=[{"fiscal_year": 2024, "ebitda": _cv(100e6)},
                       {"fiscal_year": 2025, "cash": _cv(6e6)}],
             flags=[{"flag_type": "ap_outrunning_revenue", "narrative": "n"}])
    with session_scope() as s:
        tel = filing_telegraph(ov, s)
    assert tel["score"] == {"on": 2, "evaluable": 4}


# ---- API smoke -------------------------------------------------------------------


def test_telegraph_api_atus_semantics(monkeypatch):
    # empty debt schedule (ATUS/TSE): top-level available, bank.available false,
    # the five signals still evaluated
    import app.main as main
    from app.schemas import IssuerHeader, Overview

    ov = Overview(header=IssuerHeader(issuer="ATUS-shaped", ticker="ZZTRI", years=3))
    monkeypatch.setattr(main, "run_overview", lambda *a, **k: ov)
    r = client.get("/api/company/ZZTRI/telegraph")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["available"] is True
    assert d["bank"]["available"] is False
    assert len(d["telegraph"]["signals"]) == 5
    assert {s["key"] for s in d["telegraph"]["signals"]} == {
        "coupon_focal", "default_disclosure", "advisor_going_concern",
        "war_chest", "payables_stretch"}
