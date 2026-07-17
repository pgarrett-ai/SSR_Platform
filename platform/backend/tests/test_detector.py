"""Capacity-avoidance detector + mezzanine recast (Moyer ch. 6): busted-convert ratio
tones, PIK flags, temporary-equity recast to a preferred class, graceful empties."""
from app.capstack.creation import detect_capacity_avoidance, mezz_recast_row
from app.fulcrum.adapter import overview_to_structure


def _cv(value):
    return {"value": value, "derived": True, "formula": "test"}


def _ov(schedule=None, mezzanine=None):
    return {"header": {"issuer": "Test Co"}, "debt_schedule": schedule or [],
            "forensic_table": [], "mezzanine": mezzanine}


CONVERT = {"instrument": "2.00% Convertible Notes due 2030", "outstanding": _cv(1000e6),
           "coupon_pct": 2.0, "maturity": "2030", "secured": False,
           "seniority": "convertible",
           "conversion_price": {"value": 54.78, "unit": "USD/share"}}


def test_deeply_busted():
    out = detect_capacity_avoidance(_ov([CONVERT]), 2.10)
    (item,) = out["items"]
    assert item["kind"] == "busted_convert"
    assert item["ratio"] == 0.04                     # 2.10 / 54.78
    assert item["busted"] is True and item["tone"] == "high"
    assert "analyze purely as debt" in item["note"]
    assert out["meta_note"] is not None


def test_in_the_money_not_busted():
    out = detect_capacity_avoidance(_ov([CONVERT]), 60.0)
    (item,) = out["items"]
    assert item["ratio"] == 1.10 and item["busted"] is False
    assert item["tone"] == "neutral"                 # ratio displayed, tone not a gate


def test_no_equity_price_degrades():
    out = detect_capacity_avoidance(_ov([CONVERT]), None)
    (item,) = out["items"]
    assert item["ratio"] is None and item["busted"] is None
    assert "Default Risk" in item["note"]


def test_no_conversion_price_degrades():
    conv = {**CONVERT, "conversion_price": None}
    out = detect_capacity_avoidance(_ov([conv]), 2.10)
    (item,) = out["items"]
    assert item["ratio"] is None
    assert "not extracted" in item["note"]


def test_convertible_flag_from_quote():
    # XBRL missed the seniority; the drop-file quote knows it's a convert
    conv = {**CONVERT, "seniority": None, "conversion_price": None}
    bonds = [{"coupon": 2.0, "maturity": "2030-03-01", "last_price": 40.0,
              "convertible": True}]
    out = detect_capacity_avoidance(_ov([conv]), 2.10, bonds)
    assert out["items"] and out["items"][0]["kind"] == "busted_convert"


def test_pik_flagged():
    pik = {"instrument": "Senior PIK Toggle Notes", "outstanding": _cv(200e6),
           "secured": False, "pik": True}
    out = detect_capacity_avoidance(_ov([pik]), 10.0)
    (item,) = out["items"]
    assert item["kind"] == "pik" and "coverage overstated" in item["note"]


def test_mezzanine_item_and_recast_adds_preferred_class():
    ov = _ov([CONVERT], mezzanine=_cv(1300e6))
    out = detect_capacity_avoidance(ov, 60.0)
    kinds = {i["kind"] for i in out["items"]}
    assert "mezzanine" in kinds
    row = mezz_recast_row(ov)
    ov["debt_schedule"] = [*ov["debt_schedule"], row]
    structure, _, _ = overview_to_structure(ov)
    (mezz,) = [t for t in structure.tranches if t.preferred]
    assert mezz.name == "Mezzanine (recast as debt)"
    assert mezz.face == 1300.0                       # $mm, pays after debt before common
    # preferred ranks after every debt tranche in priority order
    order = structure.priority_order()
    assert order[-1] == mezz.name


def test_empty_schedule_no_items():
    # ATUS/TSE shape: nothing extracted, no mezzanine
    out = detect_capacity_avoidance(_ov([]), 5.0)
    assert out["items"] == [] and out["meta_note"] is None
    assert mezz_recast_row(_ov([])) is None
