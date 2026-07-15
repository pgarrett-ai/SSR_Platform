"""Targeted gap-fill re-search over the persisted corpora.

After the main annotation pass, instruments can still lack a maturity (name-match miss,
window truncation, facility terms living in a credit agreement rather than the debt
footnote). For each gap we search the FTS corpus we already persisted (filing notes +
covenant clause text), window the matching documents around the instrument's names, and
ask a small model to state ONLY the missing facts from those snippets. Matches feed the
extraction-alias knowledge base so the next run hits deterministically.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text as sql

from ..core.llm import extract_structured
from ..edgar.documents import FilingText, window_by_keywords
from ..schemas import DebtInstrument
from ..store import record_aliases
from .debt_schedule import _ANNOTATE_SCHEMA, _apply_annotations
from .debt_xbrl import prettify_member

_GAPFILL_MODEL = "claude-haiku-4-5"   # short, structured extraction — no need for the big model

_SYSTEM = (
    "You are annotating an issuer's debt schedule from search snippets of its own filings "
    "and credit agreements. For each listed instrument, state its maturity (as written: a "
    "year, 'April 1, 2031', or a range) and, for floating-rate facilities, the reference "
    "rate — ONLY if the snippets state it. Attach a short verbatim quote per annotation. "
    "Never guess, never supply amounts; skip instruments the snippets don't cover."
)


def _search_windows(session, ticker: str, terms: list[str],
                    per_doc_chars: int = 3000, max_docs: int = 3) -> str:
    """Best-matching corpus documents for the terms, windowed around the hits."""
    q = " OR ".join('"' + t.replace('"', " ").strip() + '"' for t in terms if t and t.strip())
    if not q:
        return ""
    try:
        rows = session.execute(sql(
            "SELECT text, source_kind FROM search "
            "WHERE search MATCH :q AND ticker = :t AND source_kind IN ('notes','covenant') "
            "ORDER BY bm25(search) LIMIT :n"
        ), {"q": q, "t": ticker, "n": max_docs}).fetchall()
    except Exception:
        return ""   # FTS unavailable or query syntax rejected -> no snippets, no fill
    parts = []
    for text_, kind in rows:
        w = window_by_keywords(text_ or "", terms, radius=1200, max_chars=per_doc_chars)
        if w:
            parts.append(f"[{kind}] {w}")
    return "\n---\n".join(parts)


def gap_fill_maturities(session, ticker: str, instruments: list[DebtInstrument],
                        ft: Optional[FilingText], asof: Optional[str],
                        aliases: Optional[dict[str, list[str]]] = None,
                        ) -> tuple[int, Optional[str]]:
    """Fill maturities (and floating-rate bases) for gap instruments from corpus snippets.
    Returns (number filled, error-or-None)."""
    gaps = [i for i in instruments
            if not i.maturity and i.outstanding and (i.outstanding.value or 0) > 0]
    if not gaps or ft is None:
        return 0, None

    blocks, budget = [], 24000
    for inst in gaps:
        terms = [inst.instrument]
        if inst.xbrl_member:
            pretty = prettify_member(inst.xbrl_member)
            if pretty.lower() != inst.instrument.lower():
                terms.append(pretty)
        terms += (aliases or {}).get(inst.xbrl_member or "", [])
        snippets = _search_windows(session, ticker, terms)
        if not snippets:
            continue
        block = f"### {inst.instrument}\n{snippets}"
        if budget - len(block) < 0:
            break
        budget -= len(block)
        blocks.append(block)
    if not blocks:
        return 0, None

    names = "\n".join(f"- {i.instrument}" for i in gaps)
    user = (
        f"Instruments still missing a maturity (annotate these exact names):\n{names}\n\n"
        "Search snippets from the issuer's filings and credit agreements, grouped per "
        "instrument:\n\n" + "\n\n".join(blocks)
    )
    result = extract_structured(
        system=_SYSTEM,
        user=user,
        tool_name="annotate_debt_instruments",
        tool_description="Maturity and floating-rate base per instrument, quote-cited. "
                         "Never supply amounts or rates as numbers.",
        input_schema=_ANNOTATE_SCHEMA,
        max_tokens=2000,
        model=_GAPFILL_MODEL,
    )
    if result is None:
        return 0, "LLM unavailable"
    if "__error__" in result:
        return 0, result["__error__"]

    before = sum(1 for i in gaps if i.maturity)
    learned = _apply_annotations(gaps, result.get("annotations", []), ft, asof, aliases,
                                 section="gap-fill re-search (persisted corpus)")
    if learned:
        record_aliases(session, ticker, learned, source="gapfill")
    return sum(1 for i in gaps if i.maturity) - before, None
