"""Pydantic response models. `Citation` and `CitedValue` are the spine of the API: every
number the UI renders arrives either with a citation or flagged `derived` with a formula.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class Citation(BaseModel):
    accession_no: Optional[str] = None
    form_type: Optional[str] = None
    filing_date: Optional[str] = None
    exhibit: Optional[str] = None
    section: Optional[str] = None
    page: Optional[int] = None
    source_url: Optional[str] = None
    quote: Optional[str] = None


class TieOut(BaseModel):
    """Reconciliation of an LLM footnote reading against its XBRL concept (Phase 4.3). `status`
    is match (≤5%) / mismatch (>5%) / no_xbrl. This is the v1 confidence score for a number."""

    label: str
    llm_value: Optional[float] = None
    llm_display: Optional[str] = None
    xbrl_value: Optional[float] = None
    xbrl_display: Optional[str] = None
    xbrl_concept: Optional[str] = None
    delta_pct: Optional[float] = None      # (llm − xbrl) / |xbrl| × 100
    status: str = "no_xbrl"
    source_url: Optional[str] = None


class CitedValue(BaseModel):
    """A single number plus its provenance. Either `citation` is set, or `derived` is True
    and `formula` explains the computation. The UI never shows an uncited hard number."""

    value: Optional[float] = None
    display: Optional[str] = None          # pre-formatted string (e.g. "$4,210M", "3.2x")
    unit: Optional[str] = "USD"
    citation: Optional[Citation] = None
    derived: bool = False
    formula: Optional[str] = None
    note: Optional[str] = None
    tie_out: Optional[TieOut] = None       # XBRL reconciliation chip, when this number has an analog


class FilingRef(BaseModel):
    accession_no: str
    form_type: str
    filing_date: Optional[str] = None
    period_of_report: Optional[str] = None
    primary_doc_url: Optional[str] = None
    filing_index_url: Optional[str] = None
    n_exhibits: int = 0
    n_credit_docs: int = 0


class IssuerHeader(BaseModel):
    issuer: Optional[str] = None
    ticker: str                              # the symbol the analyst entered
    resolved_ticker: Optional[str] = None    # current EDGAR ticker (may differ after rename/delisting)
    cik: Optional[str] = None
    years: int
    n_filings: int = 0
    last_updated: Optional[str] = None
    from_cache: bool = False
    llm_enabled: bool = False


class DebtInstrument(BaseModel):
    instrument: str
    principal: Optional[CitedValue] = None
    outstanding: Optional[CitedValue] = None
    coupon: Optional[str] = None
    maturity: Optional[str] = None
    secured: Optional[bool] = None
    seniority: Optional[str] = None
    citation: Optional[Citation] = None


class ForensicTableRow(BaseModel):
    fiscal_year: int
    period_end: Optional[str] = None
    total_debt: Optional[CitedValue] = None
    cash: Optional[CitedValue] = None
    free_cash_flow: Optional[CitedValue] = None
    capex: Optional[CitedValue] = None
    accounts_payable: Optional[CitedValue] = None
    inventory: Optional[CitedValue] = None
    revenue: Optional[CitedValue] = None
    cogs: Optional[CitedValue] = None
    ebitda: Optional[CitedValue] = None
    operating_cash_flow: Optional[CitedValue] = None
    dpo: Optional[CitedValue] = None       # days payable outstanding (derived)


class ForensicFlag(BaseModel):
    flag_type: str
    severity: str = "info"
    fiscal_year: Optional[int] = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    narrative: str
    pointer: Optional[str] = None


class ObsItem(BaseModel):
    category: str
    label: str
    amount: Optional[CitedValue] = None      # gross, as disclosed
    tax_effect: Optional[CitedValue] = None  # gross × latest effective tax rate (uniform, v1)
    net: Optional[CitedValue] = None         # gross − tax effect
    recourse: Optional[str] = None
    include_in_bridge: bool = True
    notes: Optional[str] = None


class BridgeLine(BaseModel):
    """One step of the reported-debt → economic-debt waterfall."""

    key: str
    label: str
    amount: Optional[CitedValue] = None
    is_total: bool = False                  # True for the 'Reported Debt' base and 'Economic Debt' total


class EconomicDebtBridge(BaseModel):
    lines: list[BridgeLine] = Field(default_factory=list)
    reported_debt: Optional[CitedValue] = None
    economic_debt: Optional[CitedValue] = None
    net_economic_debt: Optional[CitedValue] = None   # economic debt − cash − restricted cash
    ebitda: Optional[CitedValue] = None
    ebitdar: Optional[CitedValue] = None             # EBITDA + operating lease cost, when found
    reported_leverage: Optional[CitedValue] = None   # reported debt / EBITDA
    economic_leverage: Optional[CitedValue] = None   # economic debt / EBITDAR (EBITDA fallback)


class CovenantSummary(BaseModel):
    agreement_type: Optional[str] = None
    leverage_covenant_type: Optional[str] = None
    leverage_ratio_threshold: Optional[str] = None
    ebitda_addback_categories: list[str] = Field(default_factory=list)
    restricted_payments_basket_size: Optional[str] = None
    mfn_sunset_period: Optional[str] = None
    j_crew_blocker_present: Optional[bool] = None
    unrestricted_subsidiary_designation_flexibility: Optional[str] = None
    lme_risk_notes: Optional[str] = None
    citation: Optional[Citation] = None


class Subsidiary(BaseModel):
    """One legal entity from Exhibit 21 (Subsidiaries of the Registrant)."""

    name: str
    jurisdiction: Optional[str] = None
    parent: Optional[str] = None            # immediate parent, only when the exhibit is explicit
    percent_owned: Optional[float] = None
    citation: Optional[Citation] = None


class DriftPoint(BaseModel):
    period_end: Optional[str] = None
    form_type: Optional[str] = None
    drift_from_prior: Optional[float] = None
    liquidity_tone_score: Optional[float] = None


class LeverageTimelinePoint(BaseModel):
    fiscal_year: int
    label: Optional[str] = None            # quarterly points: "Q3 2025" (TTM EBITDA)
    period_end: Optional[str] = None
    reported_debt: Optional[float] = None
    ebitda: Optional[float] = None         # annual for FY points, TTM for quarterly points
    leverage: Optional[float] = None       # reported debt / EBITDA


class MaturityBucket(BaseModel):
    year: int
    face: float
    instruments: list[str] = Field(default_factory=list)


class ChangeItem(BaseModel):
    """One year-over-year move for the Overview 'what changed' card."""

    metric: str
    unit: Optional[str] = None             # 'USD' | 'x'
    prior: Optional[float] = None
    latest: Optional[float] = None
    delta_pct: Optional[float] = None
    direction: str = "flat"                # 'worse' | 'better' | 'flat'
    prior_fy: Optional[int] = None
    latest_fy: Optional[int] = None
    prior_label: Optional[str] = None      # quarterly comparisons: "Q2 2025"
    latest_label: Optional[str] = None


class Overview(BaseModel):
    header: IssuerHeader
    economic_debt_bridge: Optional[EconomicDebtBridge] = None
    debt_schedule: list[DebtInstrument] = Field(default_factory=list)
    forensic_table: list[ForensicTableRow] = Field(default_factory=list)
    forensic_flags: list[ForensicFlag] = Field(default_factory=list)
    obs_items: list[ObsItem] = Field(default_factory=list)
    covenants: list[CovenantSummary] = Field(default_factory=list)
    mdna_drift: list[DriftPoint] = Field(default_factory=list)
    subsidiaries: list[Subsidiary] = Field(default_factory=list)
    xbrl_tie_outs: list[TieOut] = Field(default_factory=list)
    leverage_timeline: list[LeverageTimelinePoint] = Field(default_factory=list)
    maturity_wall: list[MaturityBucket] = Field(default_factory=list)
    what_changed: list[ChangeItem] = Field(default_factory=list)   # latest FY vs prior FY
    sources: list[FilingRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # set when the LLM is off and sections were spliced from a prior snapshot (or none exists)
    llm_fallback_note: Optional[str] = None
