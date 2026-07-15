"""The Economic Debt bridge (brief §6c) — the headline output.

Reported Debt
  + operating & finance lease liabilities      (from XBRL, precise)
  + pension / OPEB deficit (underfunded)        (from the footnote, via LLM)
  + supplier / supply-chain finance balances    (LLM)
  + guarantees of external/JV/SPE debt          (LLM)
  + securitized / factored receivables w/ recourse (LLM)
  + take-or-pay / purchase commitments           (LLM)
  + environmental / litigation reserves (debt-like) (LLM)
= Economic (Adjusted) Debt

Every line is citation-linked; reported-debt/EBITDA sits next to economic-debt/EBITDA so the
hidden leverage is obvious. Lease amounts come from XBRL (exact) to avoid double-counting the
LLM's lease reading; everything else comes from the footnote extraction with a verbatim quote.

This module also builds the EBITDA box: the net-income → EBITDA walk plus the issuer's own
covenant add-back categories, XBRL-quantified where a matching concept is tagged.
"""
from __future__ import annotations

from typing import Optional

from ..edgar.documents import FilingText
from ..edgar.facts import (
    FinancialSeries,
    YearFacts,
    fmt_money_millions,
    fmt_ratio,
    raw_value,
)
from ..schemas import (
    BridgeLine,
    Citation,
    CitedValue,
    EbitdaAddback,
    EbitdaBuild,
    EconomicDebtBridge,
    ObsItem as ObsItemSchema,
)
from .forensic import total_debt
from .obs_llm import ObsExtraction

# Categories whose amounts feed the bridge by design (the brief's structural lines). Judgment
# categories (guarantee/take_or_pay/litigation_env/vie/related_party/other) defer to the LLM's
# include_in_bridge call so we don't pull in intercompany guarantees or pure disclosures.
_STRUCTURAL_BRIDGE_CATEGORIES = {"pension_opeb", "supplier_finance", "securitization"}

_LINE_LABELS = {
    "operating_leases": "Operating lease liabilities",
    "finance_leases": "Finance lease liabilities",
    "pension_opeb": "Pension / OPEB deficit (underfunded)",
    "supplier_finance": "Supplier / supply-chain finance",
    "guarantee": "Guarantees of external / JV / SPE debt",
    "securitization": "Securitized / factored receivables (recourse)",
    "take_or_pay": "Take-or-pay / purchase commitments",
    "litigation_env": "Environmental / litigation reserves",
    "vie": "Variable interest entity obligations",
    "related_party": "Related-party financing",
    "other": "Other off-balance-sheet obligations",
}

# Order the non-lease LLM categories appear in the waterfall.
_LLM_LINE_ORDER = [
    "pension_opeb", "supplier_finance", "guarantee", "securitization",
    "take_or_pay", "litigation_env", "vie", "related_party", "other",
]


def _xbrl_cite(yf: YearFacts, key: str, cik: str) -> Optional[Citation]:
    fact = yf.get(key)
    if fact is None:
        return None
    from ..edgar.client import index_url_for

    return Citation(
        accession_no=getattr(fact, "accession", None),
        form_type=getattr(fact, "form_type", None),
        filing_date=str(getattr(fact, "filing_date", "")) or None,
        section=f"XBRL concept {getattr(fact, 'concept', '')}",
        source_url=index_url_for(cik, getattr(fact, "accession", "")),
        quote=f"{getattr(fact, 'label', '')} (as of {getattr(fact, 'period_end', '')}) [XBRL]",
    )


def _lease_line(yf: YearFacts, cik: str, noncurrent_key: str, current_key: str,
                label: str) -> Optional[CitedValue]:
    nc = raw_value(yf, noncurrent_key)
    cu = raw_value(yf, current_key)
    if nc is None and cu is None:
        return None
    total = (nc or 0) + (cu or 0)
    if total <= 0:
        return None
    # Cite the (larger) noncurrent fact and note the current portion is added in.
    citation = _xbrl_cite(yf, noncurrent_key, cik) or _xbrl_cite(yf, current_key, cik)
    return CitedValue(
        value=total,
        display=fmt_money_millions(total),
        citation=citation,
        note=(f"{label}: noncurrent {fmt_money_millions(nc)} + current {fmt_money_millions(cu)} "
              f"per the balance sheet (XBRL). On-balance-sheet under ASC 842 but excluded from "
              f"headline 'debt'."),
    )


# When we add a structural category to the bridge, give it a consistent rationale rather than the
# LLM's (which sometimes argues "already on the balance sheet" — true, but the bridge adds claims
# not captured in the *reported debt* line, regardless of where else they sit on the balance sheet).
_INCLUDED_RATIONALE = {
    "pension_opeb": "Underfunded pension/OPEB deficit — a debt-like claim on the enterprise not "
                    "included in the reported long-term-debt line.",
    "supplier_finance": "Supplier / supply-chain finance — economically a callable bank facility, "
                        "disclosed as trade payables rather than debt.",
    "securitization": "Securitized / factored receivables with recourse — financing that is not "
                      "presented as debt.",
}


def _rationale(item: ObsExtraction, included: bool) -> Optional[str]:
    if included:
        return _INCLUDED_RATIONALE.get(
            item.category, "Debt-like obligation added to reach economic debt."
        )
    return item.bridge_rationale


def _llm_cited_value(item: ObsExtraction, ft: Optional[FilingText],
                     included: Optional[bool] = None) -> CitedValue:
    citation = Citation(
        accession_no=ft.accession_no if ft else None,
        form_type=ft.form_type if ft else None,
        filing_date=ft.filing_date if ft else None,
        section=item.section,
        source_url=ft.source_url if ft else None,
        quote=item.quote,
    )
    note = _rationale(item, included) if included is not None else item.bridge_rationale
    return CitedValue(
        value=item.amount_usd,
        display=fmt_money_millions(item.amount_usd) if item.amount_usd is not None else None,
        citation=citation,
        note=note,
    )


def _include_in_bridge(item: ObsExtraction) -> bool:
    if item.category in ("lease_operating", "lease_finance"):
        return False  # leases handled from XBRL
    if item.amount_usd is None or item.amount_usd <= 0:
        return False
    if item.category in _STRUCTURAL_BRIDGE_CATEGORIES:
        if item.category == "securitization" and item.recourse == "nonrecourse":
            return False
        return True
    return bool(item.include_in_bridge)


def build_bridge(
    series: FinancialSeries,
    obs_items: list[ObsExtraction],
    ft: Optional[FilingText],
) -> tuple[Optional[EconomicDebtBridge], list[ObsItemSchema]]:
    """Assemble the waterfall + return the OBS findings list (all extracted items, cited)."""
    latest = series.latest() if series else None
    etr = _effective_tax_rate(latest, series.cik) if latest is not None else None
    obs_schema = _obs_findings(obs_items, ft, etr)
    if latest is None:
        return None, obs_schema

    cik = series.cik
    lines: list[BridgeLine] = []

    # --- base: reported debt (sum of cited XBRL components) ---
    rep_val, parts = total_debt(latest)
    if rep_val is None:
        return None, obs_schema
    reported_cv = CitedValue(
        value=rep_val,
        display=fmt_money_millions(rep_val),
        derived=True,
        formula=" + ".join(parts),
        note="Reported borrowings = long-term debt (noncurrent + current) + short-term borrowings, "
             "each component cited from the balance sheet (XBRL). Excludes leases.",
    )
    lines.append(BridgeLine(key="reported_debt", label="Reported debt", amount=reported_cv,
                            is_total=True))
    running = rep_val

    # --- leases from XBRL ---
    op_cv = _lease_line(latest, cik, "op_lease_noncurrent", "op_lease_current", "Operating leases")
    if op_cv:
        lines.append(BridgeLine(key="operating_leases", label=_LINE_LABELS["operating_leases"],
                                amount=op_cv))
        running += op_cv.value
    fin_cv = _lease_line(latest, cik, "fin_lease_noncurrent", "fin_lease_current", "Finance leases")
    if fin_cv:
        lines.append(BridgeLine(key="finance_leases", label=_LINE_LABELS["finance_leases"],
                                amount=fin_cv))
        running += fin_cv.value

    # --- LLM categories (aggregate by category, sum included items) ---
    included = [it for it in obs_items if _include_in_bridge(it)]
    by_cat: dict[str, list[ObsExtraction]] = {}
    for it in included:
        by_cat.setdefault(it.category, []).append(it)

    for cat in _LLM_LINE_ORDER:
        items = by_cat.get(cat)
        if not items:
            continue
        subtotal = sum(it.amount_usd for it in items if it.amount_usd)
        if subtotal <= 0:
            continue
        # one citation if single item, else cite the largest and note the count
        primary = max(items, key=lambda x: x.amount_usd or 0)
        cv = _llm_cited_value(primary, ft, included=True)
        cv.value = subtotal
        cv.display = fmt_money_millions(subtotal)
        if len(items) > 1:
            cv.note = f"Sum of {len(items)} {cat} items; largest shown. " + (cv.note or "")
        lines.append(BridgeLine(key=cat, label=_LINE_LABELS.get(cat, cat), amount=cv))
        running += subtotal

    # --- economic debt total ---
    econ_cv = CitedValue(
        value=running,
        display=fmt_money_millions(running),
        derived=True,
        formula=f"reported debt {fmt_money_millions(rep_val)} + "
                + " + ".join(l.amount.display for l in lines[1:] if l.amount and l.amount.display),
        note="Economic (adjusted) debt = reported borrowings + every debt-like obligation above.",
    )
    lines.append(BridgeLine(key="economic_debt", label="Economic (adjusted) debt", amount=econ_cv,
                            is_total=True))

    # --- leverage. Reported = reported debt / EBITDA; economic = economic debt / EBITDA.
    # Lease liabilities sit in the numerator with no rent add-back to the denominator by
    # design (user call: plain EBITDA) — heavy lessees read conservatively high.
    ebitda = _ebitda_value(latest)
    rep_lev, econ_lev = _leverage_ratios(rep_val, running, ebitda)

    bridge = EconomicDebtBridge(
        lines=lines,
        reported_debt=reported_cv,
        economic_debt=econ_cv,
        ebitda=ebitda,
        reported_leverage=rep_lev,
        economic_leverage=econ_lev,
    )
    return bridge, obs_schema


def _leverage_ratios(rep_val: float, econ_val: float,
                     ebitda: Optional[CitedValue]) -> tuple[Optional[CitedValue], Optional[CitedValue]]:
    rep_lev = econ_lev = None
    if ebitda is None or ebitda.value is None:
        return rep_lev, econ_lev
    if ebitda.value <= 0:
        # Debt over negative (or zero) EBITDA is not a ratio — a sign-flipped multiple
        # reads MORE debt as LESS levered. Present-but-valueless = "n.m."; the dollar
        # bridge lines carry the story for these issuers.
        note = f"not meaningful — negative EBITDA ({ebitda.display})"
        rep_lev = CitedValue(
            value=None, display="n.m.", derived=True,
            formula=f"reported debt {fmt_money_millions(rep_val)} / EBITDA {ebitda.display}",
            note=note,
        )
        econ_lev = CitedValue(
            value=None, display="n.m.", derived=True,
            formula=f"economic debt {fmt_money_millions(econ_val)} / EBITDA {ebitda.display}",
            note=note,
        )
        return rep_lev, econ_lev
    rep_lev = CitedValue(
        value=rep_val / ebitda.value, display=fmt_ratio(rep_val / ebitda.value), derived=True,
        formula=f"reported debt {fmt_money_millions(rep_val)} / EBITDA {ebitda.display}",
    )
    econ_lev = CitedValue(
        value=econ_val / ebitda.value, display=fmt_ratio(econ_val / ebitda.value),
        derived=True,
        formula=f"economic debt {fmt_money_millions(econ_val)} / EBITDA {ebitda.display}",
        note="Economic debt (incl. lease liabilities) over plain EBITDA — no rent "
             "add-back to the denominator.",
    )
    return rep_lev, econ_lev


# Bridge lines produced by pre-rework code that current semantics no longer include.
_LEGACY_LINE_KEYS = {"cash_offset", "restricted_cash_offset", "net_economic_debt"}


def renormalize_spliced_bridge(bridge: EconomicDebtBridge,
                               series: Optional[FinancialSeries]) -> EconomicDebtBridge:
    """A bridge spliced from a prior snapshot carries whatever ratio semantics its era used
    (EBITDAR denominator, cash-netting lines). The LLM-extracted content is reusable; the
    arithmetic is not — drop legacy lines and recompute both leverage ratios with the
    current formula over the current XBRL EBITDA."""
    bridge.lines = [l for l in bridge.lines if l.key not in _LEGACY_LINE_KEYS]
    latest = series.latest() if series else None
    if latest is None:
        return bridge   # no current XBRL — the whole bridge is prior analysis, leave it
    rep = bridge.reported_debt.value if bridge.reported_debt else None
    econ = bridge.economic_debt.value if bridge.economic_debt else None
    if rep is None or econ is None:
        return bridge
    ebitda = _ebitda_value(latest)
    bridge.ebitda = ebitda
    bridge.reported_leverage, bridge.economic_leverage = _leverage_ratios(rep, econ, ebitda)
    return bridge


def _effective_tax_rate(yf: YearFacts, cik: str) -> Optional[CitedValue]:
    """Latest effective tax rate: the tagged footnote ratio when present, else tax expense /
    pre-tax income. NOL / one-off years produce meaningless rates — outside (0, 0.6) → None,
    and OBS tax effects are simply skipped for that issuer."""
    direct = raw_value(yf, "effective_tax_rate")
    if direct is not None and 0.0 < direct < 0.6:
        return CitedValue(value=direct, display=f"{direct:.1%}", unit="ratio",
                          citation=_xbrl_cite(yf, "effective_tax_rate", cik))
    tax, pretax = raw_value(yf, "income_tax_expense"), raw_value(yf, "pretax_income")
    if tax is None or pretax is None or pretax <= 0:
        return None
    etr = tax / pretax
    if not (0.0 < etr < 0.6):
        return None
    return CitedValue(
        value=etr, display=f"{etr:.1%}", unit="ratio", derived=True,
        formula=f"income tax {fmt_money_millions(tax)} / pre-tax income "
                f"{fmt_money_millions(pretax)} (FY{yf.fiscal_year})",
        citation=_xbrl_cite(yf, "income_tax_expense", cik),
    )


# The walk from net income and the EBITDA box below must agree by construction: both are
# NI + (interest or 0) + (taxes or 0) + D&A, requiring only the NI and D&A anchors.
def _walk_components(yf: YearFacts) -> Optional[list[tuple[str, str, float]]]:
    ni, da = raw_value(yf, "net_income"), raw_value(yf, "d_and_a")
    if ni is None or da is None:
        return None
    out = [("net_income", "net income", ni)]
    for key, label in (("interest_expense", "interest"), ("income_tax_expense", "taxes")):
        v = raw_value(yf, key)
        if v is not None:
            out.append((key, label, v))
    out.append(("d_and_a", "D&A", da))
    return out


def _ebitda_value(yf: YearFacts) -> Optional[CitedValue]:
    """Canonical EBITDA: the net-income walk (NI + interest + taxes + D&A). Falls back to
    the operating-income proxy when the walk's anchors aren't tagged."""
    comps = _walk_components(yf)
    if comps is not None:
        v = sum(c[2] for c in comps)
        return CitedValue(
            value=v, display=fmt_money_millions(v), derived=True,
            formula=" + ".join(f"{label} {fmt_money_millions(val)}" for _, label, val in comps)
                    + f" (FY{yf.fiscal_year})",
        )
    oi, da = raw_value(yf, "operating_income"), raw_value(yf, "d_and_a")
    if oi is None or da is None:
        return None
    v = oi + da
    return CitedValue(
        value=v, display=fmt_money_millions(v), derived=True,
        formula=f"operating income {fmt_money_millions(oi)} + D&A {fmt_money_millions(da)} "
                f"(FY{yf.fiscal_year})",
        note="Proxy EBITDA (net-income walk components not all tagged).",
    )


# Covenant add-back categories that duplicate walk lines are dropped; the rest map to an
# XBRL concept where one is commonly tagged, else render as an unquantified category.
_WALK_DUPES = ("interest", "tax", "depreciation", "amortization", "d&a")
_ADDBACK_XBRL = (
    (("stock", "share"), "share_based_comp"),
    (("restructur", "severance"), "restructuring"),
    (("impair", "write-down", "write-off", "writedown"), "impairment"),
)

_WALK_LABELS = {
    "net_income": "Net income",
    "interest_expense": "+ Interest expense",
    "income_tax_expense": "+ Income tax expense",
    "d_and_a": "+ Depreciation & amortization",
}


def build_ebitda_box(series: FinancialSeries,
                     addback_categories: list[str]) -> Optional[EbitdaBuild]:
    """The EBITDA box: net-income → EBITDA walk (each line XBRL-cited) plus the issuer's own
    covenant add-back categories, quantified from XBRL where a matching concept is tagged."""
    latest = series.latest() if series else None
    if latest is None:
        return None
    cik = series.cik
    comps = _walk_components(latest)
    if comps is None:
        return None   # bridge falls back to proxy EBITDA; no box without the walk anchors

    lines = [
        BridgeLine(key=key, label=_WALK_LABELS[key], amount=CitedValue(
            value=val, display=fmt_money_millions(val), citation=_xbrl_cite(latest, key, cik)))
        for key, _label, val in comps
    ]
    ebitda = _ebitda_value(latest)
    lines.append(BridgeLine(key="ebitda", label="EBITDA", amount=ebitda, is_total=True))

    addbacks: list[EbitdaAddback] = []
    used_metrics: set[str] = set()
    for cat in addback_categories:
        lc = cat.lower()
        if any(w in lc for w in _WALK_DUPES):
            continue   # already a walk line above
        metric = next((k for kws, k in _ADDBACK_XBRL if any(w in lc for w in kws)), None)
        amount = None
        if metric and metric not in used_metrics:
            v = raw_value(latest, metric)
            if v:
                amount = CitedValue(value=v, display=fmt_money_millions(v),
                                    citation=_xbrl_cite(latest, metric, cik))
                used_metrics.add(metric)
        addbacks.append(EbitdaAddback(category=cat, label=cat, amount=amount))

    return EbitdaBuild(lines=lines, ebitda=ebitda, addbacks=addbacks)


def _obs_findings(obs_items: list[ObsExtraction], ft: Optional[FilingText],
                  etr: Optional[CitedValue] = None) -> list[ObsItemSchema]:
    """All extracted OBS items as cards. Leases are excluded here because they're represented in
    the bridge from XBRL (showing the LLM's lease reading too would be redundant/confusing)."""
    out: list[ObsItemSchema] = []
    for it in obs_items:
        if it.category in ("lease_operating", "lease_finance"):
            continue
        included = _include_in_bridge(it)
        cv = _llm_cited_value(it, ft, included=included)
        tax_cv = net_cv = None
        if it.amount_usd and it.amount_usd > 0 and etr and etr.value:
            tax = it.amount_usd * etr.value
            tax_cv = CitedValue(
                value=tax, display=fmt_money_millions(tax), derived=True,
                formula=f"gross {fmt_money_millions(it.amount_usd)} × effective tax rate "
                        f"{etr.display}",
                citation=etr.citation,
                note="Uniform tax effect (latest effective tax rate); refine per item later.",
            )
            net_cv = CitedValue(
                value=it.amount_usd - tax, display=fmt_money_millions(it.amount_usd - tax),
                derived=True, formula=f"gross {fmt_money_millions(it.amount_usd)} − tax effect "
                                      f"{fmt_money_millions(tax)}",
            )
        out.append(ObsItemSchema(
            category=it.category,
            label=it.label,
            amount=cv if it.amount_usd is not None else None,
            tax_effect=tax_cv,
            net=net_cv,
            recourse=it.recourse,
            include_in_bridge=included,
            notes=_rationale(it, included),
        ))
    return out
