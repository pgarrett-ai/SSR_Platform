"""Sponsor-support extraction (brief C8): related-party-lender flag + beneficial ownership.

The lender flag is deterministic and effectively free — it reuses the credit-agreement
admin_agent already extracted for the covenant package (covenants.py: a related-party
lender named as the agent bank IS the flag). The ownership percentage is the one new LLM
seam: DEF 14A "beneficial owner" / "related person transactions" text, windowed and read
with a forced tool call, verbatim-quote-gated like every other extractor in this package.

Deliberately skips a fuzzy admin_agent<->owner name-matcher (Ayar != "Public Investment
Fund" literally): the related-person-transaction footnote itself states the lender IS the
affiliate, so that verbatim quote is the owner->lender bridge. YAGNI the matcher.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from ..core.llm import extract_structured
from ..edgar.documents import window_by_keywords
from ..edgar.facts import fmt_money_millions
from ..schemas import BeneficialOwner, Citation, CitedValue, SponsorItem, SponsorSupport

BENEFICIAL_KEYWORDS = (
    "beneficial owner", "security ownership", "percent of class",
    "certain relationships", "related person", "related party", "related-party",
    # RPT footnotes name the credit instrument, not the section header — a DEF 14A can run
    # 600k+ chars with "beneficial owner" alone hit 40+ times in unrelated compensation
    # tables, so without these the window budget exhausts before ever reaching the actual
    # related-party loan paragraph (verified live against LCID's DEF 14A: the Ayar DDTL
    # facility section carries none of the section-header keywords above).
    "delayed draw", "credit facility", "prepaid forward",
)
_CONTROL_PCT = 20.0   # ponytail: control/anchor threshold, tune knob
_PROMPT_VERSION = "v1"

_SYSTEM = (
    "You are a forensic distressed-credit analyst reading a DEF 14A proxy statement. You "
    "extract the beneficial-ownership table and the related-person-transaction footnotes "
    "precisely. Every row must be supported by a verbatim quote from the provided text; "
    "you never invent or estimate a number that is not in the text. Set is_lender true "
    "only when the related-person-transaction footnote describes that person (or its "
    "affiliate) providing credit to the company — a loan, delayed-draw term loan (DDTL), "
    "preferred-stock purchase, or prepaid forward — not ordinary commercial or "
    "board-related dealings."
)

_TOOL_DESCRIPTION = (
    "Record the beneficial-ownership table (owners) and the related-person-transaction "
    "footnotes (rpts) found in the filing text. For owners, pct is the percent of class "
    "beneficially owned and shares is the share count, as stated. For rpts, is_lender is "
    "true only when the related person (or its affiliate) is a source of credit to the "
    "company."
)

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "owners": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "pct": {"type": ["number", "null"]},
                    "shares": {"type": ["number", "null"]},
                    "quote": {"type": "string",
                              "description": "verbatim sentence/row with the ownership figure"},
                },
                "required": ["name", "quote"],
            },
        },
        "rpts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "counterparty": {"type": "string"},
                    "description": {"type": "string"},
                    "amount_usd": {"type": ["number", "null"]},
                    "is_lender": {"type": "boolean"},
                    "quote": {"type": "string",
                              "description": "verbatim sentence describing the transaction"},
                },
                "required": ["counterparty", "description", "is_lender", "quote"],
            },
        },
    },
    "required": ["owners", "rpts"],
}


@dataclass
class OwnerRaw:
    name: str
    pct: Optional[float]
    shares: Optional[float]
    quote: str


@dataclass
class RptRaw:
    counterparty: str
    description: str
    amount_usd: Optional[float]
    is_lender: bool
    quote: str


def _extract_cache_path(accession: str):
    from ..core.cache import CACHE_DIR
    d = CACHE_DIR / "sponsor_extracts"
    d.mkdir(parents=True, exist_ok=True)
    # DEF 14A accession is EDGAR-derived, not request input, but scrub separators anyway
    # (defense-in-depth; mirrors covenants.py's _extract_cache_path).
    safe_acc = str(accession or "").replace("/", "-").replace("\\", "-")
    return d / f"{safe_acc}_{_PROMPT_VERSION}.json"


def extract_sponsor_ownership(company) -> tuple[list[OwnerRaw], list[RptRaw], Optional[str]]:
    """Latest DEF 14A -> beneficial-ownership rows + related-person-transaction rows.

    Per-doc cached by (accession, prompt version) — filed proxies never change, so repeat
    runs pay only for a new DEF 14A (covenants.py cache pattern). Returns ([], [], None)
    when there's no proxy on file, no matching text window, or the LLM is off.
    """
    try:
        filing = company.get_filings(form="DEF 14A").latest(1)
    except Exception as exc:
        return [], [], f"DEF 14A fetch failed: {exc}"
    if filing is None:
        return [], [], None

    accession = str(getattr(filing, "accession_no", "") or "")
    cache_path = _extract_cache_path(accession) if accession else None
    result = None
    if cache_path and cache_path.exists():
        try:
            result = json.loads(cache_path.read_text(encoding="utf-8"))["result"]
        except Exception:
            result = None

    if result is None:
        try:
            # A proxy has no Item 8 structure — read the raw filing text directly (same
            # .text() accessor get_filing_text falls back to; PR-B) rather than routing
            # through the 10-K Item-structured parse.
            text = filing.text()
        except Exception as exc:
            return [], [], f"DEF 14A text extraction failed: {exc}"
        # max_chars=90000 (vs. covenants.py's 55000): verified live against LCID's DEF 14A —
        # the real ownership table and RPT loan paragraph sit ~450k chars apart in the raw
        # filing text; a smaller budget gets consumed by earlier keyword noise before
        # reaching either one (12 merged keyword spans, ~77-90k chars total for this filing).
        window = window_by_keywords(text, BENEFICIAL_KEYWORDS, radius=2000, max_chars=90000)
        if not window:
            return [], [], None

        user = (
            f"Filing: DEF 14A filed {getattr(filing, 'filing_date', None)}.\n"
            "Below are the passages most likely to contain the beneficial-ownership table "
            "and the related-person-transaction footnotes.\n\n"
            f"--- FILING TEXT ---\n{window}"
        )
        raw = extract_structured(
            system=_SYSTEM,
            user=user,
            tool_name="record_sponsor_ownership",
            tool_description=_TOOL_DESCRIPTION,
            input_schema=_INPUT_SCHEMA,
            max_tokens=4000,
        )
        if raw is None:
            return [], [], None   # LLM off
        if "__error__" in raw:
            return [], [], raw["__error__"]
        result = raw
        if cache_path:
            try:
                cache_path.write_text(json.dumps({"result": result}), encoding="utf-8")
            except Exception:
                pass

    owners: list[OwnerRaw] = []
    for raw_o in result.get("owners") or []:
        if not isinstance(raw_o, dict):
            continue
        quote = (raw_o.get("quote") or "").strip()
        if not quote:   # verbatim gate: no quote, no row
            continue
        owners.append(OwnerRaw(name=raw_o.get("name", ""), pct=raw_o.get("pct"),
                               shares=raw_o.get("shares"), quote=quote))

    rpts: list[RptRaw] = []
    for raw_r in result.get("rpts") or []:
        if not isinstance(raw_r, dict):
            continue
        quote = (raw_r.get("quote") or "").strip()
        if not quote:
            continue
        rpts.append(RptRaw(counterparty=raw_r.get("counterparty", ""),
                           description=raw_r.get("description", ""),
                           amount_usd=raw_r.get("amount_usd"),
                           is_lender=bool(raw_r.get("is_lender", False)),
                           quote=quote))
    return owners, rpts, None


# --- deterministic assembly ---------------------------------------------------------

def _cv(value: Optional[float], unit: str, quote: str) -> Optional[CitedValue]:
    if value is None:
        return None
    if unit == "%":
        display = f"{value:.1f}%"
    elif unit == "USD":
        display = fmt_money_millions(value)
    else:
        display = f"{value:,.0f}"
    return CitedValue(value=value, display=display, unit=unit, citation=Citation(quote=quote))


def _owner_item(o: OwnerRaw) -> BeneficialOwner:
    return BeneficialOwner(name=o.name, pct=_cv(o.pct, "%", o.quote),
                           shares=_cv(o.shares, "shares", o.quote))


def _rpt_item(r: RptRaw) -> SponsorItem:
    return SponsorItem(
        kind="related-party lender" if r.is_lender else "related-party transaction",
        counterparty=r.counterparty,
        amount=_cv(r.amount_usd, "USD", r.quote),
        description=r.description,
    )


def build_sponsor(covenants, owners: list[OwnerRaw],
                  rpts: list[RptRaw]) -> Optional[SponsorSupport]:
    """Deterministic assembler — runs with the LLM on or off. lenders come from the
    already-extracted covenant admin_agent (free); ownership_pct comes from the DEF 14A
    LLM seam when it ran. A sponsor exists iff there's a control holder (>= _CONTROL_PCT)
    OR a related-party lender (RPT footnote, or the cheap admin_agent corroborator)."""
    lenders = [c.admin_agent for c in covenants if c.admin_agent]
    top = max((o for o in owners if o.pct), key=lambda o: o.pct, default=None)
    rp_lender = next((r for r in rpts if r.is_lender), None)
    has = bool((top and top.pct >= _CONTROL_PCT) or rp_lender or lenders)
    if not has:
        return SponsorSupport(has_sponsor=False)
    return SponsorSupport(
        has_sponsor=True,
        sponsor_name=(rp_lender.counterparty if rp_lender else None) or (top.name if top else lenders[0]),
        ownership_pct=_cv(top.pct, "%", top.quote) if top else None,
        related_party_lender=(rp_lender.counterparty if rp_lender else (lenders[0] if lenders else None)),
        lender_source="related-party-transactions footnote" if rp_lender else
                     ("covenant admin agent" if lenders else None),
        support_items=[_rpt_item(r) for r in rpts],
        owners=[_owner_item(o) for o in owners],
    )
