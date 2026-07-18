"""capstack.eightk.crisis_screen (Moyer ch. 8): the crisis-of-confidence four-factor
liquidity screen. Calibrated to the book's two anchor cases — WorldCom (a restatement
trigger + a refi wall it can't cover -> crisis) vs Global Crossing ($2B cash, no near
need -> not a crisis even amid severe fraud). Pure function, no network/DB."""
from app.capstack.eightk import crisis_screen


def _trigger():
    return [{"filing_date": "2024-02-01", "accession": "x-24-1", "source_url": "u",
             "items": ["4.02"], "items_unknown": False,
             "triggers": {"4.02": "non-reliance / restatement"}}]


def _cv(v):
    return {"value": v}


def test_worldcom_style_trigger_plus_unfundable_maturity_is_crisis():
    liq = {"cash": _cv(200e6), "undrawn_committed": _cv(1e9)}   # thin cash, big revolver reliance
    events = [{"date": "2024-05", "kind": "maturity", "instrument": "Notes",
               "amount": _cv(2e9), "flags": ["maturity_unfundable"]}]
    out = crisis_screen(_trigger(), liq, events, accel={"clauses_found": 2, "available": True})
    assert out["triggered"] is True and out["crisis"] is True
    assert out["factors"]["immediate_need"]["covered_by_cash"] is False
    assert out["trigger_items"] == ["4.02"]
    assert out["factors"]["revolver"]["reliance_pct"] == 83.3       # 1000/(200+1000)


def test_global_crossing_style_ample_cash_no_near_need_is_not_crisis():
    liq = {"cash": _cv(2e9)}
    events = [{"date": "2024-05", "kind": "coupon", "instrument": "Notes",
               "amount": _cv(30e6), "flags": []}]     # not flagged -> no immediate need
    out = crisis_screen(_trigger(), liq, events, accel={"clauses_found": 1})
    assert out["triggered"] is True and out["crisis"] is False


def test_at_risk_but_cash_covers_is_not_crisis():
    liq = {"cash": _cv(5e9)}
    events = [{"amount": _cv(2e9), "flags": ["maturity_unfundable"]}]
    out = crisis_screen(_trigger(), liq, events)
    assert out["factors"]["immediate_need"]["covered_by_cash"] is True
    assert out["crisis"] is False


def test_no_trigger_never_crisis():
    events = [{"amount": _cv(1e9), "flags": ["maturity_unfundable"]}]
    out = crisis_screen([], {"cash": _cv(0)}, events)     # dire liquidity, but no trigger
    assert out["triggered"] is False and out["crisis"] is False


def test_unknown_items_not_counted_as_trigger():
    triggers = [{"filing_date": "2024-01-01", "items": None, "items_unknown": True,
                 "triggers": None}]
    out = crisis_screen(triggers, {}, [])
    assert out["triggered"] is False and out["crisis"] is False
