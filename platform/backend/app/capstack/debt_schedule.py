"""As-reported debt schedule (brief §8.3) — instrument-level detail from the debt footnote.

XBRL gives aggregate debt; the instrument detail (coupon, maturity, secured/unsecured, seniority/
lien priority) lives in the long-term-debt footnote, so we extract it with structured LLM output,
each instrument carrying a verbatim quote.
"""
from __future__ import annotations

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
