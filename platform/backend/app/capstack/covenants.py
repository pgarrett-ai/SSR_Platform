"""Covenant extraction from credit agreements / indentures (brief §5), single issuer.

Credit docs carry terse exhibit descriptions (just "EX-10.1"), so we classify by document content,
not metadata. The agreements are 500k-850k characters, so we don't chunk blindly: we window the
text around the negative-covenant clauses AND the definitions of the key defined terms they cite
(Consolidated EBITDA, leverage ratio, Restricted Payment, Unrestricted Subsidiary, …) — a pragmatic
cross-reference resolution — then feed that to Claude with the fixed §5 schema. `clause_text` is
stored so a vector index can be added later (not built now).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from ..edgar.documents import window_by_keywords
from ..core.llm import extract_structured
from ..schemas import Citation, CovenantFact, CovenantPackage, FinancialCovenant, LmeVector

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


def find_credit_documents(company, years: int, max_check: int = 40,
                          max_keep: int = 24) -> list[CreditDoc]:
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


# --- LLM extraction ----------------------------------------------------------

_PROMPT_VERSION = "v2"   # bump to invalidate the per-doc extraction cache

_SYSTEM = (
    "You are a distressed-credit lawyer-analyst reading a credit agreement or indenture. You extract "
    "the covenant package precisely, resolving defined terms from the Definitions provided in the "
    "text. You quote verbatim and never invent thresholds. If a term is not present (e.g. the deal is "
    "covenant-lite with no maintenance financial covenant), say so explicitly rather than guessing."
)

_TOOL_DESCRIPTION = (
    "Record the covenant package for this agreement. financial_covenants: every maintenance or "
    "springing financial covenant with its threshold (including step-downs) and test frequency. "
    "baskets: the key negative-covenant capacities (restricted payments, incremental/ratio debt, "
    "liens, investments, asset sales) with their sizes. j_crew_blocker_present is true if the "
    "agreement restricts transferring material IP/assets to unrestricted subsidiaries. "
    "anchor_clause: THE single verbatim clause a distressed analyst must read (sacred rights, "
    "priming carve-out, or the loosest basket). lme_vectors: assess ONLY what this text actually "
    "addresses — for each of uptier_priming, dropdown_jcrew, incremental_debt, rp_leakage give "
    "risk protected/open/unclear (or not_addressed when the text is silent), a one-sentence "
    "rationale, the covenant facts it rests on (basis), and a short verbatim quote. Never imply "
    "a liability-management exercise exists; you are describing contractual capacity only."
)

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "financial_covenants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "threshold": {"type": "string"},
                    "test_frequency": {"type": "string"},
                    "springing_trigger": {"type": ["string", "null"]},
                    "quote": {"type": "string"},
                },
                "required": ["kind"],
            },
        },
        "baskets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["name"],
            },
        },
        "ebitda_addback_categories": {"type": "array", "items": {"type": "string"}},
        "mfn_sunset_period": {"type": ["string", "null"]},
        "j_crew_blocker_present": {"type": ["boolean", "null"]},
        "unrestricted_subsidiary_designation_flexibility": {"type": ["string", "null"]},
        "anchor_clause": {"type": "string"},
        "lme_vectors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "vector": {"type": "string",
                               "enum": ["uptier_priming", "dropdown_jcrew",
                                        "incremental_debt", "rp_leakage"]},
                    "risk": {"type": "string",
                             "enum": ["protected", "open", "unclear", "not_addressed"]},
                    "rationale": {"type": "string"},
                    "basis": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["vector", "risk"],
            },
        },
        "admin_agent": {"type": ["string", "null"]},
        "trustee": {"type": ["string", "null"]},
        "collateral_agent": {"type": ["string", "null"]},
        "guarantors": {
            "type": ["string", "null"],
            "description": "who guarantees the obligations, as the agreement defines them "
                           "(usually a class, e.g. 'all wholly-owned domestic restricted "
                           "subsidiaries'); named entities if listed",
        },
    },
    "required": ["anchor_clause", "lme_vectors"],
}

_CREDITOR_NOTE = (
    "Agents/trustee as named in the agreement. Beneficial holders of loans and bonds are not "
    "public; registered-fund holdings (N-PORT) are a partial view shown separately when available."
)


def _extract_cache_path(doc: CreditDoc):
    from ..core.cache import CACHE_DIR
    d = CACHE_DIR / "covenant_extracts"
    d.mkdir(parents=True, exist_ok=True)
    ex = (doc.exhibit_type or "EX").replace("/", "-").replace(".", "_")
    return d / f"{doc.accession}_{ex}_{_PROMPT_VERSION}.json"


def _extract_raw(doc: CreditDoc) -> tuple[Optional[dict], Optional[str], Optional[str]]:
    """(raw LLM result, clause window, error). Filed agreements never change, so results
    cache per (accession, exhibit, prompt version) — repeat runs pay only for new docs."""
    path = _extract_cache_path(doc)
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            return saved["result"], saved.get("window"), None
        except Exception:
            pass

    window = window_by_keywords(doc.text, _COVENANT_KEYWORDS, radius=1500, max_chars=55000)
    if not window:
        window = doc.text[:55000]
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
        max_tokens=4000,
    )
    if result is None:
        return None, None, "LLM unavailable"
    if "__error__" in result:
        return None, None, result["__error__"]
    try:
        path.write_text(json.dumps({"result": result, "window": window}), encoding="utf-8")
    except Exception:
        pass
    return result, window, None


def _as_list(v) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v and "not present" not in v.lower():
        return [v]
    return []


def extract_covenant_package(family) -> tuple[Optional[CovenantPackage], Optional[str], Optional[str]]:
    """Extract the covenant package for an agreement family's operative document.
    Returns (package, clause_text_window, error)."""
    op = family.operative
    doc = op.doc
    result, window, err = _extract_raw(doc)
    if result is None:
        return None, None, err

    fincovs = [FinancialCovenant(**{k: fc.get(k) for k in
                                    ("kind", "threshold", "test_frequency",
                                     "springing_trigger", "quote")})
               for fc in result.get("financial_covenants") or [] if isinstance(fc, dict)]
    baskets = [CovenantFact(name=b.get("name", ""), value=b.get("value"), quote=b.get("quote"))
               for b in result.get("baskets") or [] if isinstance(b, dict) and b.get("name")]
    vectors = [LmeVector(vector=v.get("vector", ""), risk=v.get("risk", "not_addressed"),
                         rationale=v.get("rationale"), basis=v.get("basis"),
                         quote=v.get("quote"))
               for v in result.get("lme_vectors") or [] if isinstance(v, dict) and v.get("vector")]

    citation = Citation(
        accession_no=doc.accession,
        form_type=doc.form_type,
        filing_date=doc.filing_date,
        exhibit=doc.exhibit_type,
        section="Negative covenants / Definitions",
        source_url=doc.url,
        quote=result.get("anchor_clause"),
    )
    package = CovenantPackage(
        family_label=family.label,
        doc_class=family.doc_class,
        operative_date=doc.filing_date,
        amendment_count=len(family.amendments),
        base_missing=family.base_missing,
        governs_instruments=list(family.governs_instruments),
        # deterministic head-parse wins; the LLM fills gaps
        admin_agent=op.roles.get("admin_agent") or result.get("admin_agent"),
        trustee=op.roles.get("trustee") or result.get("trustee"),
        collateral_agent=op.roles.get("collateral_agent") or result.get("collateral_agent"),
        creditor_note=_CREDITOR_NOTE,
        guarantors=result.get("guarantors"),
        financial_covenants=fincovs,
        baskets=baskets,
        ebitda_addback_categories=_as_list(result.get("ebitda_addback_categories")),
        mfn_sunset_period=result.get("mfn_sunset_period"),
        j_crew_blocker_present=result.get("j_crew_blocker_present"),
        unrestricted_subsidiary_designation_flexibility=result.get(
            "unrestricted_subsidiary_designation_flexibility"),
        anchor_clause=result.get("anchor_clause"),
        lme_vectors=vectors,
        citation=citation,
        # legacy display fields, filled for DB/FTS continuity
        agreement_type=family.doc_class,
        leverage_covenant_type=fincovs[0].kind if fincovs else None,
        leverage_ratio_threshold=fincovs[0].threshold if fincovs else None,
    )
    return package, window, None
