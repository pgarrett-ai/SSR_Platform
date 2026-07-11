"""Deterministic debt schedule: dimensional-fact grouping, retirement filtering, rate
display, and the tranche-coupon preference order. Canned facts from the AAL probe shapes."""
from __future__ import annotations

from app.capstack.debt_schedule import drop_retired
from app.capstack.debt_xbrl import _instrument_from_member, group_debt_facts, rate_display
from app.fulcrum.adapter import _parse_coupon, _tranche_coupon
from app.schemas import CitedValue, DebtInstrument


def _fact(concept, value, member=None, instant="2026-03-31", **extra):
    f = {"concept": concept, "numeric_value": str(value), "period_instant": instant,
         "label": extra.pop("label", member or concept)}
    if member:
        f["dim_us-gaap_DebtInstrumentAxis"] = member
    f.update(extra)
    return f


FACTS = [
    # 2013 TL: carrying at two instants (comparative column) + floater tagging
    _fact("us-gaap:DebtInstrumentCarryingAmount", 970e6, "aal:TL2013Member",
          dimension_member_label="2013 Term Loan Facility",
          **{"dim_us-gaap_LongtermDebtTypeAxis": "us-gaap:SecuredDebtMember"}),
    _fact("us-gaap:DebtInstrumentCarryingAmount", 980e6, "aal:TL2013Member",
          instant="2025-12-31"),
    _fact("aal:DebtInstrumentBasisSpreadOnVariableRateInterestRateMargin", 0.0275,
          "aal:TL2013Member"),
    _fact("aal:DebtInstrumentBasisSpreadOnVariableRateFloorInterestRate", 0.0,
          "aal:TL2013Member"),                      # floor must not be read as the spread
    _fact("us-gaap:LongTermDebtPercentageBearingVariableInterestRate", 0.06,
          "aal:TL2013Member"),
    # fixed notes with a stated range (EETC-style)
    _fact("us-gaap:DebtInstrumentCarryingAmount", 6.9e9, "aal:EETCMember",
          dimension_member_label="Enhanced equipment trust certificates"),
    _fact("us-gaap:DebtInstrumentInterestRateStatedPercentage", 0.0288, "aal:EETCMember"),
    _fact("us-gaap:DebtInstrumentInterestRateStatedPercentage", 0.0715, "aal:EETCMember"),
    # retired note: $0 carrying at the latest instant
    _fact("us-gaap:DebtInstrumentCarryingAmount", 0, "aal:RetiredNotesMember"),
    # short-term member via the ShortTermBorrowings concept
    _fact("us-gaap:ShortTermBorrowings", 629e6, "aal:STLoanMember",
          dimension_member_label="Senior short-term term loan facility"),
    # a consolidated + per-entity duplicate: consolidated must win
    _fact("us-gaap:DebtInstrumentCarryingAmount", 3.0e9, "aal:Notes575Member",
          dimension_member_label="5.75% senior secured notes"),
    _fact("us-gaap:DebtInstrumentCarryingAmount", 3.0e9, "aal:Notes575Member",
          **{"dim_dei_LegalEntityAxis": "aal:AmericanAirlinesIncMember"}),
    _fact("us-gaap:DebtInstrumentInterestRateStatedPercentage", 0.0575, "aal:Notes575Member"),
]


def test_group_latest_instant_and_entity_preference():
    by_member, debt, asof = group_debt_facts(FACTS)
    assert asof == "2026-03-31"
    assert set(by_member) == {"aal:TL2013Member", "aal:EETCMember", "aal:RetiredNotesMember",
                              "aal:STLoanMember", "aal:Notes575Member"}
    assert float(by_member["aal:TL2013Member"]["numeric_value"]) == 970e6   # not the comparative
    assert not by_member["aal:Notes575Member"].get("dim_dei_LegalEntityAxis")  # consolidated wins


def test_instrument_fields_floater_and_range():
    by_member, debt, _ = group_debt_facts(FACTS)
    rel = [f for f in debt if f.get("dim_us-gaap_DebtInstrumentAxis") == "aal:TL2013Member"]
    tl = _instrument_from_member("aal:TL2013Member", by_member["aal:TL2013Member"], rel, None)
    assert tl.rate_type == "floating"
    assert tl.spread_pct == 2.75 and tl.effective_rate_pct == 6.0
    assert tl.coupon == "SOFR + 2.75% → 6.00%"
    assert tl.secured is True and tl.seniority == "senior secured"

    rel = [f for f in debt if f.get("dim_us-gaap_DebtInstrumentAxis") == "aal:EETCMember"]
    eetc = _instrument_from_member("aal:EETCMember", by_member["aal:EETCMember"], rel, None)
    assert eetc.rate_type == "fixed"
    assert eetc.coupon == "2.88%–7.15%"
    assert eetc.coupon_pct == 2.88 and eetc.coupon_pct_max == 7.15


def test_rate_display_resolves_from_rates_table():
    assert rate_display(None, None, 2.75, None, "SOFR", {"SOFR": 4.30}) == "SOFR + 2.75% → 7.05%"
    assert rate_display(None, None, 2.75, None, "SOFR", None) == "SOFR + 2.75%"
    assert rate_display(5.75, None, None, None, None, None) == "5.75%"


def test_drop_retired():
    def inst(value, maturity):
        return DebtInstrument(instrument="x", outstanding=CitedValue(value=value),
                              maturity=maturity)
    kept = drop_retired([inst(1e9, "2025"), inst(1e9, "February 2028"),
                         inst(0, "2030"), inst(1e9, "2026 to 2038")], "2026-03-31")
    assert [i.maturity for i in kept] == ["February 2028", "2026 to 2038"]


def test_tranche_coupon_preference_order():
    assert _tranche_coupon({"effective_rate_pct": 6.0, "coupon": "SOFR + 2.75%"}) == 0.06
    assert _tranche_coupon({"coupon_pct": 2.88, "coupon_pct_max": 7.15}) == (2.88 + 7.15) / 200
    # string fallback takes the LAST percent — never the spread
    assert _parse_coupon("SOFR + 2.75% → 6.05%") == 0.0605
    assert _parse_coupon("fixed rates ranging from 2.88% to 7.15%, averaging 3.95%") == 0.0395
    assert _parse_coupon(None) == 0.0
