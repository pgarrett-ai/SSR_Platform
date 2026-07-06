"""Exhibit 21 (Subsidiaries of the Registrant) → structured legal-entity list (Phase 4.5).

Exhibit 21 is a semi-structured list (name, jurisdiction, occasionally tier/ownership) whose
format varies wildly across filers, so we parse it with the same structured-LLM approach as the
other footnote extractors rather than a brittle regex. The parsed entities seed Fulcrum's entity
table so the analyst can model structural subordination; ev_share stays user-assigned because
Exhibit 21 carries no financials, and guarantee placement stays manual (the exhibit lists entities,
not who guarantees what).
"""
from __future__ import annotations

from typing import Optional

from ..core.llm import extract_structured
from ..schemas import Citation, Subsidiary

_SYSTEM = (
    "You parse a company's Exhibit 21 'Subsidiaries of the Registrant' into structured rows. "
    "Use ONLY entity names present in the text; never invent a subsidiary. Copy the jurisdiction "
    "of incorporation exactly as written. Capture a parent or ownership percentage only if the "
    "exhibit makes it explicit (indentation or a column); otherwise leave them null."
)

_TOOL_DESCRIPTION = (
    "Record each subsidiary listed in the exhibit. `jurisdiction` is the state/country of "
    "incorporation. `parent` is the immediate parent entity name ONLY if the exhibit shows it "
    "(indentation/column); else null. `percent_owned` is the ownership percentage as a number "
    "(e.g. 100, 51) if shown; else null."
)

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "subsidiaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "jurisdiction": {"type": ["string", "null"]},
                    "parent": {"type": ["string", "null"]},
                    "percent_owned": {"type": ["number", "null"]},
                },
                "required": ["name"],
            },
        }
    },
    "required": ["subsidiaries"],
}


def find_exhibit21(company, max_check: int = 3) -> Optional[tuple[str, Citation]]:
    """Locate the EX-21 attachment text from a recent 10-K, with a citation."""
    try:
        filings = company.get_filings(form="10-K")
    except Exception:
        return None
    checked = 0
    for f in filings:                       # edgartools yields newest first
        if checked >= max_check:
            break
        checked += 1
        try:
            atts = f.attachments
        except Exception:
            continue
        for a in atts:
            dtp = (getattr(a, "document_type", "") or "").upper()
            if not dtp.startswith("EX-21"):
                continue
            try:
                text = a.text()
            except Exception:
                continue
            if isinstance(text, str) and len(text) > 40:
                cit = Citation(
                    accession_no=str(f.accession_no),
                    form_type=str(f.form),
                    filing_date=str(getattr(f, "filing_date", "")) or None,
                    exhibit=getattr(a, "document_type", None),
                    section="Exhibit 21 — Subsidiaries of the Registrant",
                    source_url=getattr(a, "url", None),
                )
                return text, cit
    return None


def coerce_subsidiaries(raw_items, citation: Optional[Citation] = None,
                        cap: int = 200) -> list[Subsidiary]:
    """Normalize raw LLM rows → deduped, cleaned Subsidiary list (pure; unit-tested)."""
    out: list[Subsidiary] = []
    seen: set[str] = set()
    for r in raw_items or []:
        name = (r.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        pct = r.get("percent_owned")
        try:
            pct = float(pct) if pct is not None else None
        except (TypeError, ValueError):
            pct = None
        out.append(Subsidiary(
            name=name[:160],
            jurisdiction=(r.get("jurisdiction") or "").strip() or None,
            parent=(r.get("parent") or "").strip() or None,
            percent_owned=pct,
            citation=citation,
        ))
        if len(out) >= cap:
            break
    return out


def extract_subsidiaries(company) -> tuple[list[Subsidiary], Optional[str]]:
    """Fetch Exhibit 21 and parse it into subsidiaries. Returns (items, error).
    A missing exhibit is not an error — many issuers file it inconsistently."""
    found = find_exhibit21(company)
    if found is None:
        return [], None
    text, citation = found
    result = extract_structured(
        system=_SYSTEM,
        user=("Below is the Exhibit 21 'Subsidiaries of the Registrant'. Extract every subsidiary "
              "with its jurisdiction (and parent / percent owned if shown).\n\n"
              f"--- EXHIBIT 21 ---\n{text[:40000]}"),
        tool_name="record_subsidiaries",
        tool_description=_TOOL_DESCRIPTION,
        input_schema=_INPUT_SCHEMA,
        max_tokens=4000,
    )
    if result is None:
        return [], "LLM unavailable"
    if "__error__" in result:
        return [], result["__error__"]
    return coerce_subsidiaries(result.get("subsidiaries", []), citation), None
