"""Deterministic per-instrument debt schedule from filing-level XBRL dimensions.

The debt footnote's instrument table is tagged: `DebtInstrumentAxis` members carry the
carrying amount at the balance-sheet instant, stated coupons, floating spreads, the
effective variable rate, secured/unsecured type, and (sometimes) the obligor entity.
Numbers therefore come from XBRL — the LLM's only remaining job is annotating maturity
strings (not tagged per instrument) on top of this list.

Retired instruments carry $0 at the latest instant, so a `carrying > 0` filter drops
paid-off debt without any text interpretation. Issuers that don't dimension their debt
(< _MIN_MEMBERS members) fall back to the legacy footnote extraction in debt_schedule.py.
"""
from __future__ import annotations

from typing import Optional

from ..edgar.client import index_url_for
from ..edgar.facts import fmt_money_millions
from ..schemas import Citation, CitedValue, DebtInstrument

_CARRYING_CONCEPTS = {"us-gaap:DebtInstrumentCarryingAmount", "us-gaap:ShortTermBorrowings"}
_STATED_PCT = "DebtInstrumentInterestRateStatedPercentage"
_EFFECTIVE_PCT = "LongTermDebtPercentageBearingVariableInterestRate"
_SPREAD_TOKEN = "BasisSpreadOnVariableRate"
_MIN_MEMBERS = 3   # fewer dimensioned members than this -> issuer doesn't tag debt; fall back

_SENIORITY_BY_TYPE = {
    "SecuredDebtMember": (True, "senior secured"),
    "SeniorNotesMember": (False, "senior notes"),
    "UnsecuredDebtMember": (False, "unsecured"),
    "ConvertibleDebtMember": (False, "convertible"),
    "SubordinatedDebtMember": (False, "subordinated"),
}


def _num(fact: dict) -> Optional[float]:
    try:
        return float(fact.get("numeric_value"))
    except (TypeError, ValueError):
        return None


def _pct(v: Optional[float]) -> Optional[float]:
    """XBRL percent facts are pure decimals (0.0575). Normalize to percent units."""
    if v is None:
        return None
    return round(v * 100.0, 4) if abs(v) < 1.5 else round(v, 4)


def rate_display(coupon_pct, coupon_pct_max, spread_pct, effective_rate_pct,
                 rate_base: Optional[str], rates: Optional[dict]) -> Optional[str]:
    """The coupon cell: fixed -> '5.75%'; range -> '2.88%–7.15%'; floating ->
    'SOFR + 2.75% → 6.05%' (tagged effective rate first, else base rate + spread)."""
    if spread_pct is not None or effective_rate_pct is not None:
        base = rate_base or "SOFR"
        allin = effective_rate_pct
        if allin is None and spread_pct is not None and rates and rates.get(base) is not None:
            allin = rates[base] + spread_pct
        left = f"{base} + {spread_pct:.2f}%" if spread_pct is not None else "variable"
        return f"{left} → {allin:.2f}%" if allin is not None else left
    if coupon_pct is not None:
        if coupon_pct_max is not None and coupon_pct_max != coupon_pct:
            return f"{coupon_pct:.2f}%–{coupon_pct_max:.2f}%"
        return f"{coupon_pct:.2f}%"
    return None


def _instrument_from_member(member: str, carrying: dict, related: list[dict],
                            rates: Optional[dict]) -> DebtInstrument:
    amount = _num(carrying)
    accession = str(carrying.get("fact_id") or "")
    label = (carrying.get("dimension_member_label") or carrying.get("label")
             or member.split(":")[-1].replace("Member", ""))

    stated = sorted(p for f in related
                    if _STATED_PCT in str(f.get("concept")) and (p := _pct(_num(f))) is not None)
    spread = next((_pct(_num(f)) for f in related
                   if _SPREAD_TOKEN in str(f.get("concept"))
                   and "Floor" not in str(f.get("concept")) and _num(f) is not None), None)
    effective = next((_pct(_num(f)) for f in related
                      if _EFFECTIVE_PCT in str(f.get("concept")) and _num(f) is not None), None)

    secured, seniority = None, None
    for f in related:
        t = str(f.get("dim_us-gaap_LongtermDebtTypeAxis") or
                f.get("dim_us-gaap_ShortTermDebtTypeAxis") or "")
        if t:
            local = t.split(":")[-1]
            if local in _SENIORITY_BY_TYPE:
                secured, seniority = _SENIORITY_BY_TYPE[local]
                break
            if "Secured" in local:
                secured, seniority = True, "senior secured"
                break
            if "Unsecured" in local:
                secured, seniority = False, "unsecured"
                break

    obligor = None
    ent = str(carrying.get("dim_dei_LegalEntityAxis") or "")
    if ent:
        obligor = ent.split(":")[-1].replace("Member", "")

    rate_type = "floating" if (spread is not None or effective is not None) else (
        "fixed" if stated else None)
    coupon_pct = stated[0] if stated else None
    coupon_pct_max = stated[-1] if len(stated) > 1 else None
    rate_base = "SOFR" if (spread is not None or effective is not None) else None

    instant = str(carrying.get("period_instant") or "")
    outstanding = CitedValue(
        value=amount,
        display=fmt_money_millions(amount),
        citation=Citation(     # accession/form/date/url stamped by the caller from the filing
            form_type="XBRL",
            section=f"XBRL {carrying.get('concept')} [{member}]",
            quote=f"{label}: {fmt_money_millions(amount)} as of {instant} "
                  f"[{carrying.get('concept')}, {member}]",
        ),
    )
    return DebtInstrument(
        instrument=label,
        outstanding=outstanding,
        coupon=rate_display(coupon_pct, coupon_pct_max, spread, effective, rate_base, rates),
        maturity=None,                      # annotated from the footnote text (LLM) if enabled
        secured=secured,
        seniority=seniority,
        coupon_pct=coupon_pct,
        coupon_pct_max=coupon_pct_max,
        spread_pct=spread,
        effective_rate_pct=effective,
        rate_type=rate_type,
        rate_base=rate_base,
        xbrl_member=member,
        obligor=obligor,
    )


def group_debt_facts(facts: list[dict]) -> tuple[dict[str, dict], list[dict], Optional[str]]:
    """(carrying fact per member at the latest instant, all debt-dimensioned facts, as-of).
    Comparative-period columns and duplicate per-entity tagging are filtered here:
    only the latest instant counts, and consolidated (no LegalEntityAxis) facts win."""
    debt = [f for f in facts if f.get("dim_us-gaap_DebtInstrumentAxis")]
    carrying = [f for f in debt
                if str(f.get("concept")) in _CARRYING_CONCEPTS and _num(f) is not None]
    if not carrying:
        return {}, debt, None
    asof = max(str(f.get("period_instant") or "") for f in carrying)
    by_member: dict[str, dict] = {}
    for f in carrying:
        if str(f.get("period_instant") or "") != asof:
            continue
        m = str(f["dim_us-gaap_DebtInstrumentAxis"])
        cur = by_member.get(m)
        if cur is None or (cur.get("dim_dei_LegalEntityAxis")
                           and not f.get("dim_dei_LegalEntityAxis")):
            by_member[m] = f
    return by_member, debt, asof


def build_xbrl_debt_schedule(company, rates: Optional[dict] = None
                             ) -> tuple[list[DebtInstrument], Optional[str]]:
    """Instrument-level debt schedule from the latest 10-K/10-Q's dimensional XBRL.
    Returns ([], None) when the issuer doesn't dimension debt — caller falls back to the
    legacy footnote extraction."""
    filing = company.get_filings(form=["10-K", "10-Q"]).latest(1)
    if filing is None:
        return [], None
    facts = filing.xbrl().query().execute()
    by_member, debt, asof = group_debt_facts(facts)
    if len(by_member) < _MIN_MEMBERS:
        return [], None

    cik = str(company.cik)
    accession = getattr(filing, "accession_no", None)
    instruments = []
    for member, cf in sorted(by_member.items(), key=lambda kv: -(_num(kv[1]) or 0)):
        amount = _num(cf)
        if not amount or amount <= 0:
            continue   # retired instruments carry $0 at the instant
        related = [f for f in debt if str(f.get("dim_us-gaap_DebtInstrumentAxis")) == member]
        inst = _instrument_from_member(member, cf, related, rates)
        if inst.outstanding and inst.outstanding.citation and accession:
            inst.outstanding.citation.accession_no = accession
            inst.outstanding.citation.form_type = getattr(filing, "form", None) or "XBRL"
            inst.outstanding.citation.filing_date = str(getattr(filing, "filing_date", "") or "")
            inst.outstanding.citation.source_url = index_url_for(cik, accession)
        instruments.append(inst)
    return instruments, asof
