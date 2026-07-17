"""Creation-multiple ladder (Moyer): quote matching, cumulative math, degradation."""
from app.capstack.creation import build_creation_ladder
from app.capstack.quotes import match_quotes, spread_bps


def _cv(value):
    return {"value": value, "derived": True, "formula": "test"}


def _ov(ebitda=100e6, cash=50e6, schedule=None):
    return {
        "header": {"issuer": "Test Co"},
        "economic_debt_bridge": {"ebitda": _cv(ebitda)},
        "forensic_table": [{"ebitda": _cv(ebitda), "cash": _cv(cash)}],
        "debt_schedule": schedule if schedule is not None else [
            {"instrument": "Term Loan B", "outstanding": _cv(300e6), "coupon_pct": 7.0,
             "maturity": "April 2028", "secured": True, "seniority": "senior secured"},
            {"instrument": "8.00% Senior Notes due 2030", "outstanding": _cv(200e6),
             "coupon_pct": 8.0, "maturity": "2030", "secured": False,
             "seniority": "senior unsecured"},
            {"instrument": "6.00% Subordinated Notes due 2031", "outstanding": _cv(100e6),
             "coupon_pct": 6.0, "maturity": "2031", "secured": False,
             "seniority": "subordinated"},
        ],
    }


BONDS = [
    {"coupon": 8.0, "maturity": "2030-06-15", "last_price": 60.0, "last_yield": 22.0},
    {"coupon": 6.0, "maturity": "2031-01-01", "last_price": 40.0, "last_yield": 30.0},
]


def test_match_quotes_by_coupon_and_year():
    ov = _ov()
    matches, notes = match_quotes(ov["debt_schedule"], BONDS)
    assert set(matches) == {"8.00% Senior Notes due 2030", "6.00% Subordinated Notes due 2031"}
    assert not notes


def test_match_quotes_ambiguous_left_unquoted():
    sched = [
        {"instrument": "Notes A", "coupon_pct": 8.0, "maturity": "2030"},
        {"instrument": "Notes B", "coupon_pct": 8.0, "maturity": "2030"},
    ]
    matches, notes = match_quotes(sched, BONDS[:1])
    assert matches == {} and any("ambiguous" in n for n in notes)


def test_ladder_cumulative_math_and_market_discount():
    ladder = build_creation_ladder(_ov(), BONDS)
    classes = ladder["classes"]
    # secured 300 face unquoted; unsecured pool = 200 + 100 (both quoted)
    assert classes[0]["cum_face"] == 300.0 and classes[0]["unquoted"]
    assert classes[0]["cum_market"] == 300.0            # face fallback
    last = classes[-1]
    assert last["cum_face"] == 600.0
    # market: 300 + 200*0.6 + 100*0.4 = 460
    assert last["cum_market"] == 460.0
    assert last["multiple_face"] == 6.0                  # 600 / 100
    assert last["multiple_market"] == 4.6
    # net-at-market leverage: (460 - 50) / 100 = 4.1
    assert ladder["net_market_leverage"]["value"] == 4.1
    assert ladder["net_market_leverage"]["derived"] is True


def test_ladder_fulcrum_marker():
    # EBITDA 80 -> reference EV 480 < 600 face: the unsecured pool is impaired
    ladder = build_creation_ladder(_ov(ebitda=80e6), BONDS)
    assert ladder["fulcrum_class"] == "Unsecured"
    assert ladder["creation_multiple_fulcrum"] is not None
    # fully covered at the reference EV -> no impaired class, marker stays None
    assert build_creation_ladder(_ov(), BONDS)["fulcrum_class"] is None


def test_ladder_negative_ebitda_degrades():
    ladder = build_creation_ladder(_ov(ebitda=-500e6), BONDS)
    assert all(c["multiple_face"] is None for c in ladder["classes"])
    assert ladder["net_market_leverage"] is None
    assert ladder["creation_multiple_fulcrum"] is None


def test_ladder_empty_schedule():
    ladder = build_creation_ladder(_ov(schedule=[]), BONDS)
    assert ladder["classes"] == [] and ladder["n_instruments"] == 0


def test_oid_market_leg_and_pct_of_accreted():
    # Moyer Table 5-5 shape: face 100, carrying (accreted) 71.2, quote 65
    sched = [
        {"instrument": "Term Loan B", "outstanding": _cv(300e6), "coupon_pct": 7.0,
         "maturity": "April 2028", "secured": True, "seniority": "senior secured"},
        {"instrument": "OID Notes due 2030", "outstanding": _cv(71.2e6),
         "face_amount": _cv(100e6), "coupon_pct": 8.0, "maturity": "2030",
         "secured": False, "seniority": "senior unsecured"},
    ]
    bonds = [{"coupon": 8.0, "maturity": "2030-06-15", "last_price": 65.0}]
    ladder = build_creation_ladder(_ov(schedule=sched), bonds)
    assert ladder["has_oid"] is True and ladder["oid_note"]
    q = ladder["quote_by_instrument"]["OID Notes due 2030"]
    assert q["pct_of_accreted"] == 91.3 and q["oid"] is True
    unsec = ladder["classes"][-1]
    # market leg prices the PRINCIPAL: 65% × 100 face = 65, never 65% × 71.2 carrying
    assert unsec["market"] == 65.0
    assert unsec["face"] == 100.0                  # face column shows principal
    assert unsec["accreted"] == 71.2               # accreted (carrying) tracked per class
    assert unsec["cum_accreted"] == 371.2
    # distress leg re-based: 91.3 (NOT a 40%+ discount), while the raw quote reads 65
    assert ladder["min_unsecured_quote"] == 91.3


def test_no_face_amount_keeps_phase1_behavior():
    ladder = build_creation_ladder(_ov(), BONDS)
    assert ladder["has_oid"] is False and ladder["oid_note"] is None
    # face = accreted = carrying; market = quote% × carrying (unchanged)
    last = ladder["classes"][-1]
    assert last["cum_face"] == last["cum_accreted"] == 600.0
    assert last["cum_market"] == 460.0
    assert ladder["quote_by_instrument"]["8.00% Senior Notes due 2030"]["pct_of_accreted"] == 60.0


def test_waterfall_claim_convention_is_carrying():
    # the adapter feeds carrying (accreted), never face, as Tranche.face — the ch. 5
    # claim convention holds with zero engine change
    from app.fulcrum.adapter import overview_to_structure
    sched = [{"instrument": "OID Notes due 2030", "outstanding": _cv(71.2e6),
              "face_amount": _cv(100e6), "coupon_pct": 8.0, "maturity": "2030",
              "secured": False}]
    structure, _, _ = overview_to_structure(_ov(schedule=sched))
    assert structure.tranches[0].face == 71.2


def test_spread_bps_interpolation():
    tsy = {"DTB3": 4.0, "DGS10": 4.5, "DGS30": 5.0}
    import datetime as dt
    b = {"last_yield": 15.0, "maturity": f"{dt.date.today().year + 10}-01-01"}
    # ttm 10y -> treasury 4.5 -> spread 1050 bps
    assert spread_bps(b, tsy) == 1050.0
    assert spread_bps({"last_yield": None, "maturity": "2030"}, tsy) is None
    assert spread_bps(b, {}) is None
