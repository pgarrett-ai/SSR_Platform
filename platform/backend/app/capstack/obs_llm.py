"""Footnote / MD&A LLM extraction of off-balance-sheet / economic-debt items (brief §6b).

Runs Claude with structured output over the focused footnote window and returns items that are
economically debt-like but not labeled as debt — each with a verbatim quote and the note it came
from. No regex for semantics; low temperature; nothing is invented.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..edgar.documents import FilingText
from ..core.llm import extract_structured

OBS_CATEGORIES = [
    "lease_operating", "lease_finance", "pension_opeb", "supplier_finance", "guarantee",
    "securitization", "take_or_pay", "vie", "related_party", "litigation_env", "other",
]

_SYSTEM = (
    "You are a forensic distressed-credit analyst. You extract obligations that are economically "
    "debt-like but are NOT presented as debt on the face of the balance sheet, from SEC filing "
    "footnotes and MD&A. You are rigorous and conservative: every item must be supported by a "
    "verbatim quote from the provided text, and you never invent or estimate a number that is not "
    "in the text. If an obligation is disclosed without a dollar amount, include it with a null "
    "amount and say so."
)

_TOOL_DESCRIPTION = (
    "Record each off-balance-sheet / economic-debt item found in the filing text. Convert every "
    "amount to absolute US dollars in `amount_usd` (e.g. a value of 7,000 under an '(in millions)' "
    "heading -> 7000000000; '$3.2 billion' -> 3200000000). For pension/OPEB report the UNDERFUNDED "
    "(deficit) amount = benefit obligation minus plan assets, not the gross obligation. "
    "`include_in_bridge` is true only when the item should be ADDED to reported debt to reach "
    "economic debt (a real incremental claim on the enterprise); set it false for purely "
    "informational disclosures, overfunded plans, or amounts already inside reported debt."
)

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": OBS_CATEGORIES},
                    "label": {"type": "string", "description": "short human label"},
                    "amount_usd": {"type": ["number", "null"]},
                    "amount_text": {"type": ["string", "null"], "description": "amount as written"},
                    "period": {"type": ["string", "null"], "description": "e.g. 'as of 2025-12-31'"},
                    "recourse": {
                        "type": "string",
                        "enum": ["recourse", "nonrecourse", "partial", "unknown"],
                    },
                    "include_in_bridge": {"type": "boolean"},
                    "bridge_rationale": {"type": "string"},
                    "section": {"type": ["string", "null"], "description": "note/section title"},
                    "quote": {"type": "string", "description": "verbatim sentence(s) with the figure"},
                },
                "required": ["category", "label", "amount_usd", "include_in_bridge", "quote"],
            },
        }
    },
    "required": ["items"],
}


@dataclass
class ObsExtraction:
    category: str
    label: str
    amount_usd: Optional[float]
    amount_text: Optional[str]
    period: Optional[str]
    recourse: str
    include_in_bridge: bool
    bridge_rationale: Optional[str]
    section: Optional[str]
    quote: str


def extract_obs_items(ft: FilingText) -> tuple[list[ObsExtraction], Optional[str]]:
    """Return (items, error). error is non-None if the LLM call failed."""
    window = ft.obs_window()
    if not window:
        return [], None

    user = (
        f"Filing: {ft.form_type} filed {ft.filing_date} (period {ft.period_of_report}).\n"
        "Below are the footnotes and MD&A passages most likely to contain off-balance-sheet / "
        "economic-debt items (leases, pension & OPEB, supplier/supply-chain finance, guarantees, "
        "receivables securitization/factoring, take-or-pay & purchase commitments, variable "
        "interest entities, related-party financing, environmental/litigation reserves).\n\n"
        "Extract every such item with its amount (in absolute US dollars), the period, recourse, "
        "whether it belongs in the economic-debt bridge, and a verbatim quote.\n\n"
        f"--- FILING TEXT ---\n{window}"
    )

    result = extract_structured(
        system=_SYSTEM,
        user=user,
        tool_name="record_economic_debt_items",
        tool_description=_TOOL_DESCRIPTION,
        input_schema=_INPUT_SCHEMA,
        max_tokens=6000,
    )
    if result is None:
        return [], "LLM unavailable"
    if "__error__" in result:
        return [], result["__error__"]

    items: list[ObsExtraction] = []
    for raw in result.get("items", []):
        try:
            items.append(
                ObsExtraction(
                    category=raw.get("category", "other"),
                    label=raw.get("label", ""),
                    amount_usd=raw.get("amount_usd"),
                    amount_text=raw.get("amount_text"),
                    period=raw.get("period"),
                    recourse=raw.get("recourse", "unknown"),
                    include_in_bridge=bool(raw.get("include_in_bridge", False)),
                    bridge_rationale=raw.get("bridge_rationale"),
                    section=raw.get("section"),
                    quote=raw.get("quote", ""),
                )
            )
        except Exception:
            continue
    return items, None
