"""Covenant extraction from credit agreements / indentures (brief §5), single issuer.

Credit docs carry terse exhibit descriptions (just "EX-10.1"), so we classify by document content,
not metadata. The agreements are 500k-850k characters, so we don't chunk blindly: we window the
text around the negative-covenant clauses AND the definitions of the key defined terms they cite
(Consolidated EBITDA, leverage ratio, Restricted Payment, Unrestricted Subsidiary, …) — a pragmatic
cross-reference resolution — then feed that to Claude with the fixed §5 schema. `clause_text` is
stored so a vector index can be added later (not built now).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..edgar.documents import window_by_keywords
from ..core.llm import extract_structured
from ..schemas import Citation, CovenantSummary

# Forms whose EX-10.x / EX-4.x exhibits carry credit agreements & indentures.
_CREDIT_FORMS = ["8-K", "10-K", "S-4", "S-1"]

_COVENANT_KEYWORDS = (
    "restricted payment", "limitation on indebtedness", "limitation on liens", "negative covenant",
    "financial covenant", "leverage ratio", "consolidated ebitda", "interest coverage",
    "unrestricted subsidiar", "restricted subsidiar", "most favored nation", "mfn", "available amount",
    "builder basket", "permitted investment", "permitted indebtedness", "incremental",
    "j.crew", "serta", "uptier", "dropdown", "drop down", "designate", "investments in",
    "consolidated total leverage", "first lien net leverage", "fixed charge coverage",
)

_DOC_CLASSES = {
    "INDENTURE": "indenture",
    "DEBTOR-IN-POSSESSION": "dip_credit_agreement",
    "DEBTOR IN POSSESSION": "dip_credit_agreement",
    "CREDIT AGREEMENT": "credit_agreement",
    "NOTE PURCHASE AGREEMENT": "note_purchase_agreement",
}


@dataclass
class CreditDoc:
    accession: str
    form_type: str
    filing_date: Optional[str]
    exhibit_type: Optional[str]
    url: Optional[str]
    doc_class: str
    title: str
    text: str


def _classify(head: str) -> Optional[str]:
    up = head.upper()
    # check more specific (DIP, indenture) before generic credit agreement
    for needle in ("DEBTOR-IN-POSSESSION", "DEBTOR IN POSSESSION", "INDENTURE",
                   "NOTE PURCHASE AGREEMENT", "CREDIT AGREEMENT"):
        if needle in up:
            return _DOC_CLASSES[needle]
    return None


def find_credit_documents(company, years: int, max_check: int = 8,
                          max_keep: int = 2) -> list[CreditDoc]:
    """Fetch + content-classify candidate EX-10.x/EX-4.x exhibits; keep the best credit docs."""
    import datetime as dt

    today = dt.date.today()
    start = today.replace(year=today.year - max(1, years))
    try:
        filings = company.get_filings(form=_CREDIT_FORMS).filter(
            date=f"{start.isoformat()}:{today.isoformat()}"
        )
    except Exception:
        return []

    # Collect candidate attachments (most recent first), dedup by (accession, exhibit).
    candidates = []
    seen = set()
    for f in filings:
        try:
            atts = f.attachments
        except Exception:
            continue
        for a in atts:
            dt_type = (getattr(a, "document_type", "") or "").upper()
            if not dt_type.startswith(("EX-10", "EX-4")) or dt_type.startswith("EX-101"):
                continue
            key = (str(f.accession_no), dt_type, getattr(a, "document", ""))
            if key in seen:
                continue
            seen.add(key)
            candidates.append((f, a))

    kept: list[CreditDoc] = []
    checked = 0
    for f, a in candidates:
        if checked >= max_check or len(kept) >= max_keep:
            break
        checked += 1
        try:
            text = a.text()
        except Exception:
            continue
        if not isinstance(text, str) or len(text) < 4000:
            continue
        doc_class = _classify(text[:2500])
        if doc_class is None:
            continue
        title = " ".join(text[:240].split())
        kept.append(CreditDoc(
            accession=str(f.accession_no),
            form_type=str(f.form),
            filing_date=str(getattr(f, "filing_date", "")) or None,
            exhibit_type=getattr(a, "document_type", None),
            url=getattr(a, "url", None),
            doc_class=doc_class,
            title=title,
            text=text,
        ))
    return kept


# --- LLM extraction (brief §5 schema) ---------------------------------------

_SYSTEM = (
    "You are a distressed-credit lawyer-analyst reading a credit agreement or indenture. You extract "
    "the covenant package precisely, resolving defined terms from the Definitions provided in the "
    "text. You quote verbatim and never invent thresholds. If a term is not present (e.g. the deal is "
    "covenant-lite with no maintenance financial covenant), say so explicitly rather than guessing."
)

_TOOL_DESCRIPTION = (
    "Record the covenant package for this agreement. For leverage_covenant_type, state the maintenance "
    "financial covenant type or 'None (covenant-lite)'. For thresholds, give the ratio and any springing "
    "trigger. j_crew_blocker_present is true if the agreement restricts transferring material IP/assets "
    "to unrestricted subsidiaries (a J.Crew blocker). Capture LME-relevant flexibility (unrestricted-"
    "subsidiary designation, drop-down/uptier capacity, MFN protection and its sunset)."
)

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "agreement_type": {"type": "string"},
        "leverage_covenant_type": {"type": "string"},
        "leverage_ratio_threshold": {"type": "string"},
        "ebitda_addback_categories": {"type": "array", "items": {"type": "string"}},
        "restricted_payments_basket_size": {"type": "string"},
        "mfn_sunset_period": {"type": "string"},
        "j_crew_blocker_present": {"type": ["boolean", "null"]},
        "unrestricted_subsidiary_designation_flexibility": {"type": "string"},
        "lme_risk_notes": {"type": "string", "description": "uptier/drop-down/priming vulnerability"},
        "key_quote": {"type": "string", "description": "one verbatim clause that anchors the read"},
    },
    "required": ["leverage_covenant_type", "key_quote"],
}


def extract_covenant_summary(doc: CreditDoc) -> tuple[Optional[CovenantSummary], Optional[str], Optional[str]]:
    """Return (summary, clause_text_window, error)."""
    window = window_by_keywords(doc.text, _COVENANT_KEYWORDS, radius=1500, max_chars=55000)
    if not window:
        window = doc.text[:55000]
    # Prepend the title/parties region for context.
    payload = doc.text[:2500] + "\n…\n" + window

    user = (
        f"Document: {doc.exhibit_type} ({doc.doc_class}) from {doc.form_type} filed {doc.filing_date}.\n"
        "Below are the title/parties block and the covenant clauses with the definitions they cite. "
        "Extract the covenant package per the schema, resolving defined terms from the text.\n\n"
        f"--- AGREEMENT TEXT ---\n{payload}"
    )
    result = extract_structured(
        system=_SYSTEM,
        user=user,
        tool_name="record_covenant_package",
        tool_description=_TOOL_DESCRIPTION,
        input_schema=_INPUT_SCHEMA,
        max_tokens=2500,
    )
    if result is None:
        return None, None, "LLM unavailable"
    if "__error__" in result:
        return None, None, result["__error__"]

    # Claude occasionally returns a scalar where the schema asks for a list (e.g. "Not present").
    addbacks = result.get("ebitda_addback_categories")
    if isinstance(addbacks, str):
        addbacks = [addbacks] if addbacks and "not present" not in addbacks.lower() else []
    elif not isinstance(addbacks, list):
        addbacks = []

    citation = Citation(
        accession_no=doc.accession,
        form_type=doc.form_type,
        filing_date=doc.filing_date,
        exhibit=doc.exhibit_type,
        section="Negative covenants / Definitions",
        source_url=doc.url,
        quote=result.get("key_quote"),
    )
    summary = CovenantSummary(
        agreement_type=result.get("agreement_type") or doc.doc_class,
        leverage_covenant_type=result.get("leverage_covenant_type"),
        leverage_ratio_threshold=result.get("leverage_ratio_threshold"),
        ebitda_addback_categories=addbacks,
        restricted_payments_basket_size=result.get("restricted_payments_basket_size"),
        mfn_sunset_period=result.get("mfn_sunset_period"),
        j_crew_blocker_present=result.get("j_crew_blocker_present"),
        unrestricted_subsidiary_designation_flexibility=result.get(
            "unrestricted_subsidiary_designation_flexibility"
        ),
        lme_risk_notes=result.get("lme_risk_notes"),
        citation=citation,
    )
    return summary, window, result.get("lme_risk_notes")
