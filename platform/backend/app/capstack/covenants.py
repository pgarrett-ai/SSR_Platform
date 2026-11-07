"""Covenant extraction from credit agreements / indentures, single issuer.

Credit docs carry terse exhibit descriptions (just "EX-10.1"), so we classify by document content,
not metadata. The agreements are 500k-850k characters, so we don't chunk blindly: we window the
text around the negative-covenant clauses AND the definitions of the key defined terms they cite
(Consolidated EBITDA, leverage ratio, Restricted Payment, Unrestricted Subsidiary, …) — a pragmatic
cross-reference resolution — then feed that to Claude with a fixed schema. `clause_text` is
stored so a vector index can be added later (not built now).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from ..edgar.documents import quote_in_text, window_by_keywords
from ..core.llm import extract_structured
from ..schemas import Citation, CovenantFact, CovenantPackage, FinancialCovenant, LmeVector

# Forms whose EX-10.x / EX-4.x exhibits carry credit agreements & indentures.
_CREDIT_FORMS = ["8-K", "10-K", "S-4", "S-1"]

# The census looks back further than the display window: a 2021 credit agreement still
# governs today's revolver. Expired families are filtered AFTER instrument mapping —
# widening the census costs EDGAR fetches only, never LLM tokens.
CENSUS_YEARS = 20
CENSUS_MAX_CHECK = 120   # exhibit texts fetched+classified per run (EDGAR-cached)
CENSUS_MAX_KEEP = 40     # classified credit docs retained for family grouping

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

# bump to re-extract every doc; _strip_commentary already cleans cached results on read
_PROMPT_VERSION = "v2"

_SYSTEM = (
    "You are a distressed-credit lawyer-analyst reading a credit agreement or indenture. You extract "
    "the covenant package precisely, resolving defined terms from the Definitions provided in the "
    "text. You quote verbatim and never invent thresholds. Every field holds a fact the agreement "
    "states. When the text does not state a field, return null (or an empty list) — never "
    "commentary about the text, such as 'not stated in the provided text' or 'not included in the "
    "excerpt'. A covenant-lite deal with no maintenance financial covenant has an empty "
    "financial_covenants list."
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
    # EDGAR-derived (not request input), but scrub separators anyway so a malformed value
    # can never escape covenant_extracts/ (defense-in-depth; '\' is a separator on Windows).
    ex = (doc.exhibit_type or "EX").replace("/", "-").replace("\\", "-").replace(".", "_")
    accession = str(doc.accession or "").replace("/", "-").replace("\\", "-")
    return d / f"{accession}_{ex}_{_PROMPT_VERSION}.json"


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
        return [s for s in map(_strip_commentary, v) if s]
    if isinstance(v, str) and v and "not present" not in v.lower():
        return [s for s in [_strip_commentary(v)] if s]
    return []


# The extractor sometimes narrates its input ("not stated in the provided text") instead of
# returning null. Only text that refers to the source document is removed, so a covenant fact
# that shares a sentence with the narration survives; cached extractions are cleaned the same
# way when a package is built, without re-calling the model.
_COMMENTARY_RE = re.compile(
    r"\b(?:provided|supplied) (?:text|excerpts?|clauses|language|sections?)\b"
    r"|\bexcerpt(?:s|ed)?\b|\bin (?:the|this) text(?: provided)?\b|\bprovided in the text\b"
    r"|\bnot reproduced\b", re.IGNORECASE)
_EMPTY_VALUE_RE = re.compile(
    r"^\s*(?:not (?:stated|specified|quantified|addressed|identified|determinable)"
    r"|none (?:stated|specified))\.?\s*$", re.IGNORECASE)
_PAREN_RE = re.compile(r"\s*\(([^()]*)\)")
# Party names arrive wrapped in boilerplate ("Lenders party thereto from time to time,
# JPMorgan Chase Bank, N.A."): keep the institution after the last boilerplate marker.
_PARTY_MARKER = r"(?:party (?:hereto|thereto)|from time to time|recitals|as guarantors?)"
_PARTY_LEAD_RE = re.compile(rf"^.*\b{_PARTY_MARKER}\b[^,]*,\s*", re.IGNORECASE)
_PARTY_ROLE_RE = re.compile(r",?\s+as (?:the )?(?:administrative |collateral |security )?"
                            r"(?:agent|trustee)\b.*$", re.IGNORECASE)
_BOILERPLATE_PARTY_RE = re.compile(
    r"^(?:the\s+)?(?:llc|inc|n\.?a|ltd|l\.?p|plc|corp|company|guarantors?|lenders?|borrowers?"
    rf"|(?:administrative |collateral |security )?(?:agent|trustee)s?)\.?$|\b{_PARTY_MARKER}\b",
    re.IGNORECASE)


def _clauses(v: str) -> list[str]:
    """Split after '.' or ';' (a closing quote may follow) outside parentheses, never after
    an initial ("J. Crew", "N.A."), at a dash, and before 'but' / 'though' / 'although' —
    "X is defined, but the baskets are not in the excerpt" keeps X."""
    parts, start, depth = [], 0, 0
    for i, ch in enumerate(v):
        depth += (ch == "(") - (ch == ")")
        j = i + 1 + (v[i + 1:i + 2] in ('"', "”", "'"))
        initial = ch == "." and v[i - 1:i].isupper() and not v[i - 2:i - 1].isalpha()
        if depth == 0 and ch in ".;" and v[j:j + 1].isspace() and not initial:
            parts.append(v[start:j])
            start = j
    parts.append(v[start:])
    return [c.strip() for p in parts
            for c in re.split(r"\s+[—–]\s+|,?\s+(?=(?:but|though|although)\b)", p)
            if c.strip()]


def _strip_commentary(v):
    """A string value minus the parentheticals and clauses that comment on the source text;
    None when it is only that (or a bare "Not stated"). Other values pass through."""
    if not isinstance(v, str):
        return v
    if _EMPTY_VALUE_RE.match(_PAREN_RE.sub("", v)):
        return None
    if not _COMMENTARY_RE.search(v):
        return v
    v = _PAREN_RE.sub(lambda m: "" if _COMMENTARY_RE.search(m.group(1)) else m.group(0), v)
    kept = " ".join(c for c in _clauses(v) if not _COMMENTARY_RE.search(c))
    kept = re.sub(r"^(?:however|instead|but|though|although),?\s+", "", kept.strip(" ;,—–"),
                  flags=re.I)
    if not kept or _EMPTY_VALUE_RE.match(kept):
        return None
    if v.rstrip().endswith(".") and kept[-1] not in ".\"'”":
        kept += "."   # a sentence cut short keeps its full stop
    return kept[:1].upper() + kept[1:]


def _party(v) -> Optional[str]:
    """An agent/trustee name with boilerplate stripped, or None when no institution is named."""
    if not isinstance(v, str):
        return None
    v = _PAREN_RE.sub(lambda m: "" if _COMMENTARY_RE.search(m.group(1)) else m.group(0), v)
    v = _PARTY_ROLE_RE.sub("", _PARTY_LEAD_RE.sub("", v)).strip(" ,;")
    return None if not v or _COMMENTARY_RE.search(v) or _BOILERPLATE_PARTY_RE.search(v) else v


def extract_covenant_package(family) -> tuple[Optional[CovenantPackage], Optional[str],
                                              Optional[str], int]:
    """Extract the covenant package for an agreement family's operative document.
    Returns (package, clause_text_window, error, quotes dropped as not verbatim)."""
    op = family.operative
    doc = op.doc
    result, window, err = _extract_raw(doc)
    if result is None:
        return None, None, err, 0
    dropped = [0]

    def verbatim(quote: Optional[str]) -> Optional[str]:
        """Checked against the agreement itself, so cached extractions are checked too: a
        quote the text does not contain is dropped, never shown as verbatim; its fact stays."""
        if quote and not quote_in_text(quote, doc.text):
            dropped[0] += 1
            return None
        return quote

    s = _strip_commentary
    fincovs = [FinancialCovenant(**{k: s(fc.get(k)) for k in
                                    ("kind", "threshold", "test_frequency",
                                     "springing_trigger")}, quote=verbatim(fc.get("quote")))
               for fc in result.get("financial_covenants") or [] if isinstance(fc, dict)]
    baskets = [CovenantFact(name=b.get("name", ""), value=s(b.get("value")),
                            quote=verbatim(b.get("quote")))
               for b in result.get("baskets") or [] if isinstance(b, dict) and b.get("name")]
    vectors = [LmeVector(vector=v.get("vector", ""), risk=v.get("risk", "not_addressed"),
                         rationale=s(v.get("rationale")), basis=s(v.get("basis")),
                         quote=verbatim(v.get("quote")))
               for v in result.get("lme_vectors") or [] if isinstance(v, dict) and v.get("vector")]
    anchor = verbatim(result.get("anchor_clause"))

    citation = Citation(
        accession_no=doc.accession,
        form_type=doc.form_type,
        filing_date=doc.filing_date,
        exhibit=doc.exhibit_type,
        section="Negative covenants / Definitions",
        source_url=doc.url,
        quote=anchor,
    )
    package = CovenantPackage(
        family_label=family.label,
        doc_class=family.doc_class,
        operative_date=doc.filing_date,
        amendment_count=len(family.amendments),
        base_missing=family.base_missing,
        governs_instruments=list(family.governs_instruments),
        # deterministic head-parse wins; the LLM fills gaps
        admin_agent=_party(op.roles.get("admin_agent")) or _party(result.get("admin_agent")),
        trustee=_party(op.roles.get("trustee")) or _party(result.get("trustee")),
        collateral_agent=(_party(op.roles.get("collateral_agent"))
                          or _party(result.get("collateral_agent"))),
        creditor_note=_CREDITOR_NOTE,
        guarantors=s(result.get("guarantors")),
        financial_covenants=fincovs,
        baskets=baskets,
        ebitda_addback_categories=_as_list(result.get("ebitda_addback_categories")),
        mfn_sunset_period=s(result.get("mfn_sunset_period")),
        j_crew_blocker_present=result.get("j_crew_blocker_present"),
        unrestricted_subsidiary_designation_flexibility=s(result.get(
            "unrestricted_subsidiary_designation_flexibility")),
        anchor_clause=anchor,
        lme_vectors=vectors,
        citation=citation,
        # flat display fields the covenants table and FTS index read
        agreement_type=family.doc_class,
        leverage_covenant_type=fincovs[0].kind if fincovs else None,
        leverage_ratio_threshold=fincovs[0].threshold if fincovs else None,
    )
    return package, window, None, dropped[0]
