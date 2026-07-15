"""Deterministic debt schedule: dimensional-fact grouping, retirement filtering, rate
display, and the tranche-coupon preference order. Canned facts from the AAL probe shapes."""
from __future__ import annotations

from app.capstack.debt_schedule import drop_retired
from app.capstack.debt_xbrl import (_instrument_from_member, facility_capacity,
                                    group_debt_facts, prettify_member, rate_display)
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


# LCID-shaped facts: converts under LongTermDebt/ConvertibleDebt (dual-tagged), an undrawn
# revolver, a commitment-only amendment member, and two facilities sharing a generic label.
LCID_FACTS = [
    _fact("us-gaap:LongTermDebt", 1085e6, "lcid:A2030NotesMember",
          dimension_member_label="2030 Notes"),
    _fact("us-gaap:ConvertibleDebt", 1085e6, "lcid:A2030NotesMember",
          dimension_member_label="2030 Notes"),
    _fact("us-gaap:ShortTermBorrowings", 1890e6, "lcid:A2025GIBCreditFacilityMember",
          dimension_member_label="Revolving Credit Facility"),
    _fact("us-gaap:ShortTermBorrowings", 503.5e6, "lcid:GIBCreditFacilityMember",
          dimension_member_label="Revolving Credit Facility"),
    _fact("us-gaap:LongTermDebt", 0, "lcid:ABLCreditFacilityMember",
          dimension_member_label="Revolving Credit Facility"),
    _fact("us-gaap:LineOfCreditFacilityRemainingBorrowingCapacity", 610e6,
          "lcid:ABLCreditFacilityMember"),
    # commitment tagged at a LATER (subsequent-event) instant, on a member with no carrying
    _fact("us-gaap:LineOfCreditFacilityMaximumBorrowingCapacity", 2500e6,
          "lcid:DDTLAmendmentMember", instant="2026-04-30",
          dimension_member_label="Secured Debt"),
    _fact("us-gaap:LongTermDebt", 0, "lcid:SIDFMember",
          dimension_member_label="SIDF"),   # zero balance, no capacity -> dropped
]


def test_broadened_concepts_and_asof_ignores_capacity_instants():
    by_member, debt, asof = group_debt_facts(LCID_FACTS)
    assert asof == "2026-03-31"          # the 2026-04-30 commitment must not skew as-of
    assert "lcid:A2030NotesMember" in by_member          # LongTermDebt now counts
    assert float(by_member["lcid:A2030NotesMember"]["numeric_value"]) == 1085e6
    cap = facility_capacity(debt)
    assert float(cap["lcid:ABLCreditFacilityMember"]["undrawn"]["numeric_value"]) == 610e6
    assert float(cap["lcid:DDTLAmendmentMember"]["commitment"]["numeric_value"]) == 2500e6


def test_prettify_and_label_collision():
    assert prettify_member("lcid:A2025GIBCreditFacilityMember") == "2025 GIB Credit Facility"
    assert prettify_member("lcid:DDTLCreditFacilityMember") == "DDTL Credit Facility"
    by_member, debt, _ = group_debt_facts(LCID_FACTS)
    used: set[str] = set()
    names = []
    for member in ("lcid:A2025GIBCreditFacilityMember", "lcid:GIBCreditFacilityMember"):
        rel = [f for f in debt if f.get("dim_us-gaap_DebtInstrumentAxis") == member]
        inst = _instrument_from_member(member, by_member[member], rel, None, used_labels=used)
        used.add(inst.instrument)
        names.append(inst.instrument)
    # the generic shared label must not survive as two identical rows
    assert names == ["2025 GIB Credit Facility", "GIB Credit Facility"]


def test_undrawn_facility_kept_and_convertible_seniority():
    by_member, debt, _ = group_debt_facts(LCID_FACTS)
    cap = facility_capacity(debt)
    abl = _instrument_from_member(
        "lcid:ABLCreditFacilityMember", by_member["lcid:ABLCreditFacilityMember"],
        [f for f in debt if f.get("dim_us-gaap_DebtInstrumentAxis") == "lcid:ABLCreditFacilityMember"],
        None, used_labels=set(), capacity=cap.get("lcid:ABLCreditFacilityMember"))
    assert abl.facility_type == "revolver"
    assert abl.undrawn is not None and abl.undrawn.value == 610e6
    notes = _instrument_from_member(
        "lcid:A2030NotesMember", by_member["lcid:A2030NotesMember"],
        [f for f in debt if f.get("dim_us-gaap_DebtInstrumentAxis") == "lcid:A2030NotesMember"],
        None, used_labels=set())
    assert notes.seniority == "convertible"      # from the ConvertibleDebt concept
    assert notes.facility_type == "notes"


def test_fill_maturity_from_name():
    from app.capstack.debt_schedule import fill_maturity_from_name

    insts = [
        DebtInstrument(instrument="2030 Notes", facility_type="notes"),
        DebtInstrument(instrument="2025 GIB Credit Facility", facility_type="revolver"),
        DebtInstrument(instrument="Senior Notes due 2028", facility_type="notes"),
        DebtInstrument(instrument="Term Loan", facility_type="term loan"),
        DebtInstrument(instrument="2026 Notes", facility_type="notes", maturity="December 2026"),
    ]
    n = fill_maturity_from_name(insts, "2026-03-31")
    assert n == 2
    assert insts[0].maturity == "2030"
    assert insts[1].maturity is None        # facility vintage year is not a maturity
    assert insts[2].maturity == "2028"
    assert insts[3].maturity is None
    assert insts[4].maturity == "December 2026"   # existing annotation untouched


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
