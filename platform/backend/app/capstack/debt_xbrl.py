"""Deterministic per-instrument debt schedule from filing-level XBRL dimensions.

The debt footnote's instrument table is tagged: `DebtInstrumentAxis` members carry the
carrying amount at the balance-sheet instant, stated coupons, floating spreads, the
effective variable rate, secured/unsecured type, and (sometimes) the obligor entity.
Numbers therefore come from XBRL — the LLM's only remaining job is annotating maturity
strings (not tagged per instrument) on top of this list.

Retired instruments carry $0 at the latest instant and no committed capacity, so they
drop without any text interpretation — but undrawn committed facilities (revolvers,
delayed-draw term loans) are KEPT as rows: their commitment/undrawn capacity is the
liquidity story for a cash-burner. Issuers that don't dimension their debt
(< _MIN_MEMBERS members) fall back to the legacy footnote extraction in debt_schedule.py.
"""
from __future__ import annotations

import re
from typing import Optional

from ..edgar.client import index_url_for
from ..edgar.facts import fmt_money_millions
from ..schemas import Citation, CitedValue, DebtInstrument

# Carrying-amount concepts. Issuers spread their instrument table across these (LCID tags
# converts as LongTermDebt/ConvertibleDebt, facilities as ShortTermBorrowings; AAL uses
# DebtInstrumentCarryingAmount throughout). Per member we take the LARGEST value at the
# as-of instant — concepts overlap (LongTermDebt includes current maturities), so max
# avoids double-counting without preferring any issuer's tagging style.
_CARRYING_CONCEPTS = {
    "us-gaap:DebtInstrumentCarryingAmount",
    "us-gaap:LongTermDebt",
    "us-gaap:ConvertibleLongTermNotesPayable",
    "us-gaap:ConvertibleDebt",
    "us-gaap:ConvertibleNotesPayable",
    "us-gaap:NotesPayable",
    "us-gaap:SecuredDebt",
    "us-gaap:UnsecuredDebt",
    "us-gaap:LoansPayable",
    "us-gaap:LongTermLineOfCredit",
    "us-gaap:LinesOfCreditCurrent",
    "us-gaap:ShortTermBorrowings",
}
# Facility capacity concepts — commitment size and undrawn headroom. Selected at their own
# latest instant (subsequent-event amendments are exactly what we want to surface).
_COMMITMENT_CONCEPTS = {
    "us-gaap:LineOfCreditFacilityMaximumBorrowingCapacity",
    "us-gaap:LineOfCreditFacilityCurrentBorrowingCapacity",
}
_UNDRAWN_CONCEPTS = {
    "us-gaap:LineOfCreditFacilityRemainingBorrowingCapacity",
    "us-gaap:DebtInstrumentUnusedBorrowingCapacityAmount",
}
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

# The us-gaap type-axis standard labels sometimes leak into dimension_member_label; the
# member name itself ("A2025GIBCreditFacilityMember") is more specific than "Secured Debt".
_GENERIC_LABELS = {
    "secured debt", "unsecured debt", "convertible debt", "senior notes",
    "revolving credit facility", "line of credit", "notes payable", "loans payable",
    "medium-term notes", "junior subordinated debt", "subordinated debt", "long-term debt",
}


def _num(fact: dict) -> Optional[float]:
    try:
        return float(fact.get("numeric_value"))
    except (TypeError, ValueError):
        return None


def prettify_member(member: str) -> str:
    """'lcid:A2025GIBCreditFacilityMember' -> '2025 GIB Credit Facility' (XBRL member
    names can't start with a digit, hence the leading-A idiom)."""
    local = member.split(":")[-1]
    local = re.sub(r"Member$", "", local)
    local = re.sub(r"^A(?=\d)", "", local)
    words = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", local)
    return " ".join(words) or local


def _display_label(member: str, label: Optional[str], used_labels: set[str]) -> str:
    """Prefer the tagged member label, but fall back to the prettified member name when the
    label is a generic type-axis string or collides with another instrument's label
    (LCID tags two different facilities 'Revolving Credit Facility')."""
    if label and label.strip().lower() not in _GENERIC_LABELS and label not in used_labels:
        return label
    return prettify_member(member)


def facility_type_of(name_blob: str) -> Optional[str]:
    """Deterministic facility classification from member/label tokens."""
    s = re.sub(r"[-_]", " ", name_blob.lower())
    if "delayed draw" in s or re.search(r"\bddtl\b", s):
        return "delayed-draw term loan"
    if "revolv" in s or re.search(r"\babl\b", s):
        return "revolver"
    if "term loan" in s or re.search(r"\bterm [ab]\b", s):
        return "term loan"
    if "commercial paper" in s:
        return "commercial paper"
    if "bridge" in s:
        return "bridge loan"
    if "notes" in s or "debenture" in s or "bond" in s:
        return "notes"
    if "credit facility" in s or "line of credit" in s or "loan" in s:
        return "credit facility"
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


def _capacity_cv(fact: Optional[dict], member: str, label: str) -> Optional[CitedValue]:
    if fact is None:
        return None
    v = _num(fact)
    if v is None or v <= 0:
        return None
    instant = str(fact.get("period_instant") or "")
    return CitedValue(
        value=v,
        display=fmt_money_millions(v),
        citation=Citation(
            form_type="XBRL",
            section=f"XBRL {fact.get('concept')} [{member}]",
            quote=f"{label}: {fmt_money_millions(v)} as of {instant} "
                  f"[{fact.get('concept')}, {member}]",
        ),
    )


def _instrument_from_member(member: str, carrying: Optional[dict], related: list[dict],
                            rates: Optional[dict],
                            used_labels: Optional[set[str]] = None,
                            capacity: Optional[dict] = None) -> DebtInstrument:
    amount = _num(carrying) if carrying else None
    tagged_label = None
    if carrying:
        tagged_label = carrying.get("dimension_member_label") or carrying.get("label")
    elif related:
        tagged_label = related[0].get("dimension_member_label")
    label = _display_label(member, tagged_label, used_labels or set())

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
    ent = str(carrying.get("dim_dei_LegalEntityAxis") or "") if carrying else ""
    if ent:
        obligor = ent.split(":")[-1].replace("Member", "")

    rate_type = "floating" if (spread is not None or effective is not None) else (
        "fixed" if stated else None)
    coupon_pct = stated[0] if stated else None
    coupon_pct_max = stated[-1] if len(stated) > 1 else None
    rate_base = "SOFR" if (spread is not None or effective is not None) else None

    outstanding = None
    if carrying is not None:
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

    if seniority is None:
        concepts = " ".join(str(f.get("concept") or "") for f in related)
        blob = f"{member} {tagged_label or ''} {concepts}"
        if "convertible" in blob.lower():
            seniority = "convertible"

    slot = capacity or {}
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
        facility_type=facility_type_of(f"{member} {tagged_label or ''}"),
        commitment=_capacity_cv(slot.get("commitment"), member, label),
        undrawn=_capacity_cv(slot.get("undrawn"), member, label),
    )


def _pick_key(f: dict) -> tuple[int, float]:
    """Consolidated (no LegalEntityAxis) beats per-entity duplicates; then the larger
    amount wins — carrying concepts overlap (LongTermDebt spans ConvertibleDebt etc.),
    so max-of-concepts avoids double-counting a member without preferring a tag style."""
    return (0 if f.get("dim_dei_LegalEntityAxis") else 1, _num(f) or 0.0)


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
        if cur is None or _pick_key(f) > _pick_key(cur):
            by_member[m] = f
    return by_member, debt, asof


def facility_capacity(debt: list[dict]) -> dict[str, dict[str, dict]]:
    """{member: {"commitment": fact, "undrawn": fact}} — each at its own latest instant.
    Capacity facts legitimately post-date the carrying as-of (subsequent-event facility
    amendments); the citation quote carries the instant."""
    def cap_key(f: dict) -> tuple[str, int, float]:
        return (str(f.get("period_instant") or ""),
                0 if f.get("dim_dei_LegalEntityAxis") else 1, _num(f) or 0.0)

    out: dict[str, dict[str, dict]] = {}
    for kind, concepts in (("commitment", _COMMITMENT_CONCEPTS), ("undrawn", _UNDRAWN_CONCEPTS)):
        for f in debt:
            if str(f.get("concept")) not in concepts or _num(f) is None:
                continue
            m = str(f["dim_us-gaap_DebtInstrumentAxis"])
            slot = out.setdefault(m, {})
            cur = slot.get(kind)
            if cur is None or cap_key(f) > cap_key(cur):
                slot[kind] = f
    return out


def build_xbrl_debt_schedule(company, rates: Optional[dict] = None
                             ) -> tuple[list[DebtInstrument], Optional[str], Optional[object]]:
    """Instrument-level debt schedule from the latest 10-K/10-Q's dimensional XBRL.
    Returns ([], None, filing) when the issuer doesn't dimension debt — caller falls back
    to the legacy footnote extraction. The source filing rides along so maturity
    annotation reads the same document the numbers came from."""
    filing = company.get_filings(form=["10-K", "10-Q"]).latest(1)
    if filing is None:
        return [], None, None
    facts = filing.xbrl().query().execute()
    by_member, debt, asof = group_debt_facts(facts)
    if len(by_member) < _MIN_MEMBERS:
        return [], None, filing

    cik = str(company.cik)
    accession = getattr(filing, "accession_no", None)

    def stamp(cv: Optional[CitedValue]) -> None:
        if cv is not None and cv.citation is not None and accession:
            cv.citation.accession_no = accession
            cv.citation.form_type = getattr(filing, "form", None) or "XBRL"
            cv.citation.filing_date = str(getattr(filing, "filing_date", "") or "")
            cv.citation.source_url = index_url_for(cik, accession)

    capacity = facility_capacity(debt)
    instruments = []
    used_labels: set[str] = set()
    members = sorted(by_member.items(), key=lambda kv: -(_num(kv[1]) or 0))
    # commitment-only members (facility amendments tagged without a carrying fact)
    members += [(m, None) for m in capacity if m not in by_member]
    for member, cf in members:
        amount = _num(cf) if cf else None
        slot = capacity.get(member, {})
        has_capacity = any((_num(f) or 0) > 0 for f in slot.values())
        if (amount or 0) <= 0 and not has_capacity:
            continue   # retired paper: no balance and no committed capacity
        related = [f for f in debt if str(f.get("dim_us-gaap_DebtInstrumentAxis")) == member]
        inst = _instrument_from_member(member, cf, related, rates,
                                       used_labels=used_labels, capacity=slot)
        used_labels.add(inst.instrument)
        for cv in (inst.outstanding, inst.commitment, inst.undrawn):
            stamp(cv)
        instruments.append(inst)
    return instruments, asof, filing
