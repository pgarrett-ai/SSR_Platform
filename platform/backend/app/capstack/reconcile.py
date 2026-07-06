"""XBRL tie-out reconciliation (brief §Phase-4.3, the v1 confidence score).

Does the LLM's footnote reading agree with the structured XBRL facts? For the three categories
that appear in both places — leases, pension/OPEB, and debt — compare the LLM total to its XBRL
concept. A mismatch >5% is a warning chip on the number (extraction may be wrong, or the two
measure subtly different things). A match is a green tick that the footnote number ties out.
"""
from __future__ import annotations

from typing import Optional

from ..edgar import YearFacts, raw_value
from ..edgar.client import index_url_for
from ..edgar.facts import fmt_money_millions
from ..schemas import DebtInstrument, TieOut
from .forensic import total_debt
from .obs_llm import ObsExtraction

_MATCH_THRESHOLD = 0.05   # ≤5% ⇒ ties out


def _tie(label: str, llm: Optional[float], xbrl: Optional[float],
         concept: Optional[str], cik: str, accession: Optional[str]) -> TieOut:
    if llm is None or xbrl is None or xbrl == 0:
        status, delta = "no_xbrl", None
    else:
        delta = round(100 * (llm - xbrl) / abs(xbrl), 1)
        status = "match" if abs(delta) <= _MATCH_THRESHOLD * 100 else "mismatch"
    return TieOut(
        label=label,
        llm_value=llm, llm_display=fmt_money_millions(llm) if llm is not None else None,
        xbrl_value=xbrl, xbrl_display=fmt_money_millions(xbrl) if xbrl is not None else None,
        xbrl_concept=concept, delta_pct=delta, status=status,
        source_url=index_url_for(cik, accession) if accession else None,
    )


def pension_tie_out(latest: YearFacts, cik: str, llm_deficit: Optional[float]) -> Optional[TieOut]:
    """LLM pension/OPEB deficit vs the XBRL *funded status* (a deficit is a negative funded
    status; take the magnitude). Only tie out against the funded-status concept — comparing a
    deficit to the gross benefit obligation would be a false mismatch."""
    pf = latest.get("pension_benefit_obligation")
    concept = getattr(pf, "concept", "") or ""
    if not llm_deficit or "FundedStatus" not in concept:
        return None
    xbrl = abs(getattr(pf, "numeric_value", 0) or 0)
    return _tie("Pension / OPEB deficit", llm_deficit, xbrl, concept, cik,
                getattr(pf, "accession", None))


def build_tie_outs(series, obs_items: list[ObsExtraction],
                   debt_schedule: list[DebtInstrument]) -> tuple[list[TieOut], list[str]]:
    """Reconcile leases, pension, and debt totals. Returns (tie_outs, warnings)."""
    latest = series.latest() if series else None
    if latest is None:
        return [], []
    cik = series.cik
    out: list[TieOut] = []

    # --- leases: LLM footnote reading vs XBRL lease liabilities ---
    llm_leases = sum(i.amount_usd for i in obs_items
                     if i.category in ("lease_operating", "lease_finance") and i.amount_usd)
    lease_keys = ("op_lease_noncurrent", "op_lease_current",
                  "fin_lease_noncurrent", "fin_lease_current")
    xbrl_leases = sum(v for v in (raw_value(latest, k) for k in lease_keys) if v)
    if llm_leases and xbrl_leases:
        acc = getattr(latest.get("op_lease_noncurrent")
                      or latest.get("fin_lease_noncurrent"), "accession", None)
        out.append(_tie("Leases (operating + finance)", llm_leases, xbrl_leases,
                        "OperatingLease/FinanceLease liabilities (XBRL)", cik, acc))

    # --- pension: LLM deficit vs XBRL funded status ---
    llm_pension = sum(i.amount_usd for i in obs_items
                      if i.category == "pension_opeb" and i.amount_usd)
    pt = pension_tie_out(latest, cik, llm_pension or None)
    if pt is not None:
        out.append(pt)

    # --- debt: LLM debt-schedule sum vs XBRL reported total debt ---
    llm_debt = sum((inst.outstanding or inst.principal).value
                   for inst in debt_schedule
                   if (inst.outstanding or inst.principal) and (inst.outstanding or inst.principal).value)
    xbrl_debt, _ = total_debt(latest)
    if llm_debt and xbrl_debt:
        acc = getattr(latest.get("lt_debt_noncurrent"), "accession", None)
        out.append(_tie("Debt schedule vs reported debt", llm_debt, xbrl_debt,
                        "LongTermDebt + ShortTermBorrowings (XBRL)", cik, acc))

    warnings = [
        f"XBRL tie-out: {t.label} — footnote {t.llm_display} vs XBRL {t.xbrl_display} "
        f"({t.delta_pct:+.1f}%), above the 5% threshold."
        for t in out if t.status == "mismatch"
    ]
    return out, warnings
