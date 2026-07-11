"""Agreement families: group the census of credit documents into base-agreement families,
pick the operative version, and map debt instruments to the family that governs them.

Everything here is deterministic text parsing over the formulaic preamble of a credit
agreement / indenture (amendment ordinal, "AMENDED AND RESTATED", "dated as of …", note
series, ", as Administrative Agent/Trustee/Collateral Agent"). No LLM — unmatched documents
and instruments are surfaced honestly rather than guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .covenants import CreditDoc

_ORDINALS = {
    "FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "SIXTH": 6,
    "SEVENTH": 7, "EIGHTH": 8, "NINTH": 9, "TENTH": 10, "ELEVENTH": 11, "TWELFTH": 12,
    "THIRTEENTH": 13, "FOURTEENTH": 14, "FIFTEENTH": 15,
}

_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")
_DATE_RE = rf"((?:{'|'.join(_MONTHS)})\s+\d{{1,2}},\s+\d{{4}})"

# ", as Administrative Agent" style role assignments; party name = the capitalized run
# immediately before the role phrase.
_ROLE_RES = {
    "admin_agent": re.compile(
        r"([A-Z][A-Za-z0-9 .,&'\-]{2,70}?),?\s+as\s+(?:the\s+)?[Aa]dministrative\s+[Aa]gent"),
    "trustee": re.compile(
        r"([A-Z][A-Za-z0-9 .,&'\-]{2,70}?),?\s+as\s+(?:the\s+)?[Tt]rustee"),
    "collateral_agent": re.compile(
        r"([A-Z][A-Za-z0-9 .,&'\-]{2,70}?),?\s+as\s+(?:the\s+)?[Cc]ollateral\s+"
        r"(?:[Aa]gent|[Tt]rustee)"),
}

_NOTE_SERIES_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s+(?:[A-Za-z][A-Za-z /-]{0,40}\s+)?[Nn]otes\s+due\s+(\d{4})")

# Amendments large enough to embed the full restated agreement can serve as the operative text.
_EMBEDS_RESTATEMENT_CHARS = 250_000


@dataclass
class DocHead:
    doc: CreditDoc
    amendment_no: Optional[int] = None       # None = base document
    amended_restated: bool = False
    dated: Optional[str] = None              # this document's own "dated as of"
    base_date: Optional[str] = None          # the underlying agreement an amendment references
    note_coupon: Optional[float] = None
    note_due: Optional[int] = None
    roles: dict = field(default_factory=dict)
    embeds_restatement: bool = False


@dataclass
class AgreementFamily:
    key: str
    doc_class: str
    label: str
    operative: DocHead
    amendments: list[DocHead] = field(default_factory=list)
    base_missing: bool = False
    governs_instruments: list[str] = field(default_factory=list)


def _clean_party(name: str) -> str:
    # role regexes grab trailing connectives from party lists; trim at obvious boundaries
    name = re.split(r"\s+(?:and|AND)\s+", name)[-1]
    return name.strip(" ,;\n\t")


def parse_doc_head(doc: CreditDoc) -> DocHead:
    head = " ".join(doc.text[:4000].split())
    dh = DocHead(doc=doc)

    m = re.search(rf"({'|'.join(_ORDINALS)})\s+AMENDMENT", head.upper())
    if m:
        dh.amendment_no = _ORDINALS[m.group(1)]
    else:
        m = re.search(r"AMENDMENT\s+NO\.?\s*(\d+)", head.upper())
        if m:
            dh.amendment_no = int(m.group(1))
    dh.amended_restated = "AMENDED AND RESTATED" in head.upper()

    dates = re.findall(rf"dated\s+as\s+of\s+{_DATE_RE}", head, flags=re.IGNORECASE)
    if dates:
        dh.dated = dates[0]
        # an amendment's later "dated as of" usually names the underlying base agreement
        dh.base_date = dates[-1] if dh.amendment_no and len(dates) > 1 else (
            dates[0] if dh.amendment_no is None else None)
        if dh.amendment_no and dh.base_date is None:
            dh.base_date = dates[0]

    m = _NOTE_SERIES_RE.search(head)
    if m:
        dh.note_coupon = float(m.group(1))
        dh.note_due = int(m.group(2))

    for role, rx in _ROLE_RES.items():
        rm = rx.search(head)
        if rm:
            dh.roles[role] = _clean_party(rm.group(1))
    # a collateral-agent match often also matches the trustee regex fragment; keep both

    dh.embeds_restatement = len(doc.text) >= _EMBEDS_RESTATEMENT_CHARS
    return dh


def _family_key(dh: DocHead) -> str:
    if dh.doc.doc_class == "indenture" and dh.note_coupon is not None:
        return f"notes:{dh.note_coupon}%:{dh.note_due or ''}"
    anchor = dh.base_date or dh.dated or dh.doc.accession
    return f"{dh.doc.doc_class}:{anchor}"


def _year_of(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None
    m = re.search(r"(\d{4})", str(date_str))
    return m.group(1) if m else None


def group_families(docs: list[CreditDoc]) -> list[AgreementFamily]:
    heads = [parse_doc_head(d) for d in docs]
    by_key: dict[str, list[DocHead]] = {}
    for dh in heads:
        by_key.setdefault(_family_key(dh), []).append(dh)

    families: list[AgreementFamily] = []
    for key, members in by_key.items():
        bases = [m for m in members if m.amendment_no is None]
        amendments = sorted((m for m in members if m.amendment_no is not None),
                            key=lambda m: m.amendment_no or 0)
        if bases:
            # operative = the latest restatement (filing date orders A&Rs over the original)
            operative = sorted(bases, key=lambda m: (m.amended_restated,
                                                     m.doc.filing_date or ""))[-1]
            base_missing = False
        else:
            # amendment-only family: the biggest amendment usually embeds the restated text
            operative = sorted(amendments, key=lambda m: (m.embeds_restatement,
                                                          len(m.doc.text)))[-1]
            base_missing = not operative.embeds_restatement

        dc = operative.doc.doc_class
        if operative.note_coupon is not None:
            label = f"{operative.note_coupon:g}% notes due {operative.note_due or '?'}"
        else:
            anchor = operative.base_date or operative.dated
            label = dc.replace("_", " ")
            if anchor:
                label += f" dated {anchor}"
        families.append(AgreementFamily(
            key=key, doc_class=dc, label=label, operative=operative,
            amendments=amendments, base_missing=base_missing,
        ))
    families.sort(key=lambda f: f.operative.doc.filing_date or "", reverse=True)
    return families


def map_instruments(families: list[AgreementFamily], instruments) -> dict[str, Optional[str]]:
    """Deterministic instrument↔family mapping. Returns {instrument name: family label or None}
    and fills each family's governs_instruments. Unmatched stays None — shown honestly."""
    mapping: dict[str, Optional[str]] = {}
    for inst in instruments:
        name = inst.instrument or ""
        lc = name.lower()
        matched: Optional[AgreementFamily] = None

        if "payroll support" in lc or re.match(r"psp\d?", lc):
            mapping[name] = "U.S. Treasury promissory note (Payroll Support Program)"
            inst.governed_by = mapping[name]
            continue
        if any(t in lc for t in ("equipment trust", "eetc", "equipment loan",
                                 "special facility", "revenue bond")):
            mapping[name] = None   # governing docs typically not filed as EX-10/EX-4
            continue

        for fam in families:
            op = fam.operative
            # notes: coupon + due-year tokens
            if op.note_coupon is not None and inst.coupon_pct is not None:
                due_ok = (op.note_due is None or str(op.note_due) in f"{name} {inst.maturity or ''}")
                if abs(op.note_coupon - inst.coupon_pct) < 0.01 and due_ok:
                    matched = fam
                    break
            # facilities: vintage-year token ("2013 Revolving Facility" ↔ base dated 2013)
            year = _year_of(op.base_date or op.dated)
            if year and year in name and fam.doc_class != "indenture":
                matched = fam
                break
        if matched:
            matched.governs_instruments.append(name)
            mapping[name] = matched.label
            inst.governed_by = matched.label
        else:
            mapping[name] = None
    return mapping
