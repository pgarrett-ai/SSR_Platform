"""Company-options feasibility (Moyer ch. 11): buyback deployable/retirable math,
the deployable floor at 0 (LCID persona), the 60-day exchange gate, unquoted and
empty degradation."""
from app.capstack.options import build_options


def _cv(v):
    return {"value": v, "derived": True, "formula": "test"}


WEBCO_SCHED = [{"instrument": "10% Senior Notes due 2030", "outstanding": _cv(200e6),
                "coupon_pct": 10.0, "maturity": "June 2030", "secured": False,
                "facility_type": "notes"}]
WEBCO_BONDS = [{"coupon": 10.0, "maturity": "2030-06-15", "last_price": 25.0,
                "last_yield": 38.0}]


def _ov(schedule=None, cash=225e6, fcf=-150e6, ebitda=50e6, asof="2026-06-30",
        covenants=None, **extra):
    ov = {
        "header": {"issuer": "Webco", "ticker": "ZZOPT"},
        "debt_schedule_asof": asof,
        "economic_debt_bridge": {"ebitda": _cv(ebitda)} if ebitda is not None else {},
        "forensic_table": [{"fiscal_year": 2025, "cash": _cv(cash),
                            "free_cash_flow": _cv(fcf), "ebitda": _cv(ebitda)}],
        "debt_schedule": WEBCO_SCHED if schedule is None else schedule,
        "covenants": covenants or [],
    }
    ov.update(extra)
    return ov


def test_webco_deployable_and_retirable_capped():
    # cash 225 − one year of burn 150 -> deployable 75; issue 200 @ 25:
    # MV 50, retirable min(200, 75/0.25 = 300) = 200 — capped at face, feasible
    out = build_options(_ov(), WEBCO_BONDS)
    assert out["available"] is True
    assert out["buyback"]["deployable"]["value"] == 75.0
    row = out["buyback"]["rows"][0]
    assert row["price"] == 25.0
    assert row["market_mm"] == 50.0
    assert row["retirable"]["value"] == 200.0
    assert row["retirable_pct"] == 100.0
    assert row["feasible"] is True


def test_webco_deployable_40_retires_80pct():
    out = build_options(_ov(cash=190e6), WEBCO_BONDS)
    assert out["buyback"]["deployable"]["value"] == 40.0
    row = out["buyback"]["rows"][0]
    assert row["retirable"]["value"] == 160.0     # 40 ÷ 0.25
    assert row["retirable_pct"] == 80.0


def test_lcid_shaped_deployable_floors_at_zero():
    # cash 700 − burn 4,649 goes deeply negative -> floored at 0; every buyback row
    # renders feasible False / retirable 0 — the honest Moyer answer
    out = build_options(_ov(cash=700e6, fcf=-4649e6), WEBCO_BONDS)
    assert out["buyback"]["deployable"]["value"] == 0.0
    row = out["buyback"]["rows"][0]
    assert row["retirable"]["value"] == 0.0
    assert row["feasible"] is False


def test_gate_60d_at_45_vs_90_days():
    # events are month-granular: March 2026 event = 2026-03-01;
    # asof 2026-01-15 -> 45 days (fail), asof 2025-12-01 -> 90 days (pass)
    sched = [{"instrument": "9% Notes due 2026", "outstanding": _cv(100e6),
              "coupon_pct": 9.0, "maturity": "March 2026", "secured": False,
              "facility_type": "notes"}]
    g = build_options(_ov(schedule=sched, asof="2026-01-15"), [])["exchange_gate"]
    assert g["gate_60d"]["days_to_next_event"] == 45
    assert g["gate_60d"]["pass"] is False
    assert g["verdict"] == "no_window"
    g = build_options(_ov(schedule=sched, asof="2025-12-01"), [])["exchange_gate"]
    assert g["gate_60d"]["days_to_next_event"] == 90
    assert g["gate_60d"]["pass"] is True


def test_unquoted_issue_feasible_null():
    out = build_options(_ov(), [])                # no drop-file quotes
    row = out["buyback"]["rows"][0]
    assert row["feasible"] is None and row["retirable"] is None
    assert out["exchange_gate"]["discount_capture_per_100"] is None
    assert out["clock"]["who_controls"] == "unclear"    # cash-vs-MV leg not decisive


def test_discount_capture_reads_ladder_payload():
    # 100 − min unsecured quote (build_creation_ladder's min_unsecured_quote)
    out = build_options(_ov(), WEBCO_BONDS)
    g = out["exchange_gate"]
    assert g["min_unsecured_quote"] == 25.0
    assert g["discount_capture_per_100"] == 75.0
    assert g["n_unsecured_classes"] == 1
    assert g["verdict"] == "viable"


def test_empty_schedule_unavailable():
    out = build_options(_ov(schedule=[]), [])
    assert out["available"] is False
