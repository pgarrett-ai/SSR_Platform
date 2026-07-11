"""Debt-footnote text interpretation for the debt schedule.

Numbers come from dimensional XBRL (debt_xbrl.py). This module's LLM does the two jobs the
tags can't: (a) `annotate_maturities` — per-instrument maturity strings and the floating-rate
base, quote-cited, keyed to the deterministic instrument list; (b) the legacy full extraction
`extract_debt_schedule`, kept ONLY as the fallback for issuers that don't dimension their
debt, with deterministic post-filters (`drop_retired`) on top.
"""
from __future__ import annotations

import re
from typing import Optional

from ..edgar.documents import FilingText
from ..edgar.facts import fmt_money_millions
from ..core.llm import extract_structured
from ..schemas import Citation, CitedValue, DebtInstrument

_SYSTEM = (
    "You are a distressed-credit analyst building an issuer's debt schedule (cap table) from its "
    "long-term-debt footnote. Extract one row per distinct debt instrument or facility. Be precise "
    "and conservative: use only amounts present in the text, and attach a verbatim quote to each "
    "instrument. Do not invent coupons, maturities, or lien priorities that aren't stated."
)

_TOOL_DESCRIPTION = (
    "Record each debt instrument / facility in the issuer's capital structure. Convert amounts to "
    "absolute US dollars (a table value of 1,250 under '(in millions)' -> 1250000000). `secured` "
    "is true for secured/collateralized debt. `seniority` should capture lien/priority where stated "
    "(e.g. 'first-lien senior secured', 'senior unsecured', 'subordinated'). Use the most recent "
    "balance-sheet date's outstanding amount."
)

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "instruments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "instrument": {"type": "string"},
                    "principal_usd": {"type": ["number", "null"]},
                    "outstanding_usd": {"type": ["number", "null"]},
                    "coupon": {"type": ["string", "null"]},
                    "maturity": {"type": ["string", "null"]},
                    "secured": {"type": ["boolean", "null"]},
                    "seniority": {"type": ["string", "null"]},
                    "quote": {"type": "string"},
                },
                "required": ["instrument", "quote"],
            },
        }
    },
    "required": ["instruments"],
}


def extract_debt_schedule(ft: FilingText) -> tuple[list[DebtInstrument], Optional[str]]:
    window = ft.debt_window()
    if not window:
        return [], None

    user = (
        f"Filing: {ft.form_type} filed {ft.filing_date} (period {ft.period_of_report}).\n"
        "Below is the long-term-debt footnote text. Extract the issuer's debt instruments with "
        "outstanding amount, coupon, maturity, secured/unsecured, and seniority/lien priority, each "
        "with a verbatim quote.\n\n"
        f"--- DEBT FOOTNOTE TEXT ---\n{window}"
    )
    result = extract_structured(
        system=_SYSTEM,
        user=user,
        tool_name="record_debt_instruments",
        tool_description=_TOOL_DESCRIPTION,
        input_schema=_INPUT_SCHEMA,
        max_tokens=6000,
    )
    if result is None:
        return [], "LLM unavailable"
    if "__error__" in result:
        return [], result["__error__"]

    out: list[DebtInstrument] = []
    for raw in result.get("instruments", []):
        citation = Citation(
            accession_no=ft.accession_no,
            form_type=ft.form_type,
            filing_date=ft.filing_date,
            section="Long-term debt footnote",
            source_url=ft.source_url,
            quote=raw.get("quote", ""),
        )
        principal = raw.get("principal_usd")
        outstanding = raw.get("outstanding_usd")
        out.append(
            DebtInstrument(
                instrument=raw.get("instrument", ""),
                principal=_cv(principal, citation),
                outstanding=_cv(outstanding, citation),
                coupon=raw.get("coupon"),
                maturity=raw.get("maturity"),
                secured=raw.get("secured"),
                seniority=raw.get("seniority"),
                citation=citation,
            )
        )
    return out, None


def _cv(amount: Optional[float], citation: Citation) -> Optional[CitedValue]:
    if amount is None:
        return None
    return CitedValue(value=amount, display=fmt_money_millions(amount), citation=citation)


def drop_retired(instruments: list[DebtInstrument], asof: Optional[str]) -> list[DebtInstrument]:
    """Deterministic post-filter for the legacy path: no balance outstanding, or a maturity
    year before the schedule's as-of year, means the instrument is gone."""
    asof_year = None
    if asof:
        m = re.match(r"(\d{4})", str(asof))
        asof_year = int(m.group(1)) if m else None
    out = []
    for inst in instruments:
        cv = inst.outstanding or inst.principal
        if cv is not None and (cv.value or 0) <= 0:
            continue
        if asof_year and inst.maturity:
            years = [int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", str(inst.maturity))]
            if years and max(years) < asof_year:
                continue
        out.append(inst)
    return out


_ANNOTATE_SYSTEM = (
    "You are annotating an issuer's debt schedule. The instrument list and all amounts are "
    "already known from XBRL — do NOT supply numbers. For each listed instrument, find its "
    "maturity (as stated: a year, 'February 2028', or a range like '2026 to 2038') and, for "
    "floating-rate instruments, the reference rate the agreement uses (SOFR, Term SOFR, "
    "prime, EFFR). Attach a short verbatim quote for each annotation. Skip instruments the "
    "text doesn't cover."
)

_ANNOTATE_SCHEMA = {
    "type": "object",
    "properties": {
        "annotations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "instrument": {"type": "string"},
                    "maturity": {"type": ["string", "null"]},
                    "rate_base": {"type": ["string", "null"]},
                    "quote": {"type": "string"},
                },
                "required": ["instrument", "quote"],
            },
        }
    },
    "required": ["annotations"],
}


def annotate_maturities(instruments: list[DebtInstrument], ft: FilingText,
                        asof: Optional[str] = None) -> Optional[str]:
    """One LLM call: maturity strings + floating-rate bases for the XBRL instrument list.
    Mutates the instruments in place; returns an error string or None."""
    if not instruments:
        return None
    window = ft.debt_window()
    if not window:
        return "no debt footnote text"
    names = "\n".join(f"- {i.instrument}" for i in instruments)
    user = (
        f"Filing: {ft.form_type} filed {ft.filing_date} (period {ft.period_of_report}).\n"
        f"Instruments (from XBRL — annotate these exact names):\n{names}\n\n"
        f"--- DEBT FOOTNOTE TEXT ---\n{window}"
    )
    result = extract_structured(
        system=_ANNOTATE_SYSTEM,
        user=user,
        tool_name="annotate_debt_instruments",
        tool_description="Maturity and floating-rate base per instrument, quote-cited. "
                         "Never supply amounts or rates as numbers.",
        input_schema=_ANNOTATE_SCHEMA,
        max_tokens=4000,
    )
    if result is None:
        return "LLM unavailable"
    if "__error__" in result:
        return result["__error__"]

    asof_year = int(str(asof)[:4]) if asof and str(asof)[:4].isdigit() else None
    by_name = {a.get("instrument", "").strip().lower(): a for a in result.get("annotations", [])}
    for inst in instruments:
        ann = by_name.get(inst.instrument.strip().lower())
        if not ann:
            continue
        maturity = ann.get("maturity")
        if maturity and asof_year:
            years = [int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", str(maturity))]
            if years and max(years) < asof_year:   # sanity: annotation contradicts carrying > 0
                maturity = None
        if maturity:
            inst.maturity = maturity
            inst.citation = Citation(
                accession_no=ft.accession_no, form_type=ft.form_type,
                filing_date=ft.filing_date, section="Long-term debt footnote",
                source_url=ft.source_url, quote=ann.get("quote", ""),
            )
        base = (ann.get("rate_base") or "").strip()
        if base and inst.rate_type == "floating":
            inst.rate_base = base
    return None
