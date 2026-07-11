"""ORM models. Citations are the spine: every extracted value points at a Citation row,
or is explicitly marked `derived` with a formula. Nothing fakes a source.

Tables (per brief §2/§4): filings, exhibits, citations, extracted_facts, covenants,
obs_items, forensic_flags, mdna_sections, snapshots (screening index).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Filing(Base):
    """One EDGAR filing pulled into the window."""

    __tablename__ = "filings"
    __table_args__ = (UniqueConstraint("accession_no", name="uq_filing_accession"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cik: Mapped[str] = mapped_column(String(16), index=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    accession_no: Mapped[str] = mapped_column(String(32), index=True)
    form_type: Mapped[str] = mapped_column(String(16), index=True)
    filing_date: Mapped[Optional[date]] = mapped_column(Date)
    period_of_report: Mapped[Optional[date]] = mapped_column(Date)
    primary_doc_url: Mapped[Optional[str]] = mapped_column(Text)
    filing_index_url: Mapped[Optional[str]] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    exhibits: Mapped[list["Exhibit"]] = relationship(
        back_populates="filing", cascade="all, delete-orphan"
    )


class Exhibit(Base):
    """An attachment on a filing — credit agreements (EX-10.x), indentures (EX-4.x), etc."""

    __tablename__ = "exhibits"

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filings.id"), index=True)
    exhibit_type: Mapped[Optional[str]] = mapped_column(String(32), index=True)  # e.g. EX-10.1
    description: Mapped[Optional[str]] = mapped_column(Text)
    document: Mapped[Optional[str]] = mapped_column(Text)  # filename within the filing
    url: Mapped[Optional[str]] = mapped_column(Text)
    is_credit_doc: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    local_path: Mapped[Optional[str]] = mapped_column(Text)

    filing: Mapped["Filing"] = relationship(back_populates="exhibits")


class Citation(Base):
    """The non-negotiable provenance object (brief §4). Referenced by facts/covenants/obs."""

    __tablename__ = "citations"

    id: Mapped[int] = mapped_column(primary_key=True)
    accession_no: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    form_type: Mapped[Optional[str]] = mapped_column(String(16))
    filing_date: Mapped[Optional[date]] = mapped_column(Date)
    exhibit: Mapped[Optional[str]] = mapped_column(String(32))
    section: Mapped[Optional[str]] = mapped_column(Text)
    page: Mapped[Optional[int]] = mapped_column(Integer)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    quote: Mapped[Optional[str]] = mapped_column(Text)  # verbatim sentence the value came from

    def to_dict(self) -> dict[str, Any]:
        return {
            "accession_no": self.accession_no,
            "form_type": self.form_type,
            "filing_date": self.filing_date.isoformat() if self.filing_date else None,
            "exhibit": self.exhibit,
            "section": self.section,
            "page": self.page,
            "source_url": self.source_url,
            "quote": self.quote,
        }


class ExtractedFact(Base):
    """A numeric capital-structure fact (from XBRL or a footnote). Cited or derived."""

    __tablename__ = "extracted_facts"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    concept: Mapped[str] = mapped_column(String(64), index=True)  # e.g. TotalDebt, CashAndEquiv
    taxonomy_tag: Mapped[Optional[str]] = mapped_column(String(96))  # us-gaap concept if XBRL
    period_end: Mapped[Optional[date]] = mapped_column(Date, index=True)
    fiscal_year: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    value: Mapped[Optional[float]] = mapped_column(Float)
    unit: Mapped[Optional[str]] = mapped_column(String(16), default="USD")
    derived: Mapped[bool] = mapped_column(Boolean, default=False)
    formula: Mapped[Optional[str]] = mapped_column(Text)  # shown when derived=True
    citation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("citations.id"))
    citation: Mapped[Optional["Citation"]] = relationship()


class Covenant(Base):
    """An extracted covenant clause from a credit agreement / indenture (brief §5)."""

    __tablename__ = "covenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    accession_no: Mapped[Optional[str]] = mapped_column(String(32))
    agreement_type: Mapped[Optional[str]] = mapped_column(String(48))  # credit agreement / indenture

    leverage_covenant_type: Mapped[Optional[str]] = mapped_column(String(96))
    leverage_ratio_threshold: Mapped[Optional[str]] = mapped_column(String(96))
    ebitda_addback_categories: Mapped[Optional[list]] = mapped_column(JSON)
    restricted_payments_basket_size: Mapped[Optional[str]] = mapped_column(String(128))
    mfn_sunset_period: Mapped[Optional[str]] = mapped_column(String(96))
    j_crew_blocker_present: Mapped[Optional[bool]] = mapped_column(Boolean)
    unrestricted_subsidiary_designation_flexibility: Mapped[Optional[str]] = mapped_column(Text)

    # Kept verbatim so a vector index can be added later (brief §5: v2 hook, not built now).
    clause_text: Mapped[Optional[str]] = mapped_column(Text)
    citation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("citations.id"))
    citation: Mapped[Optional["Citation"]] = relationship()


class ObsItem(Base):
    """An off-balance-sheet / economic-debt item that feeds the bridge (brief §6b/§6c)."""

    __tablename__ = "obs_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    # lease / pension_opeb / supplier_finance / guarantee / securitization /
    # take_or_pay / vie / related_party / litigation_env / other
    category: Mapped[str] = mapped_column(String(48), index=True)
    label: Mapped[Optional[str]] = mapped_column(Text)
    amount: Mapped[Optional[float]] = mapped_column(Float)
    unit: Mapped[Optional[str]] = mapped_column(String(16), default="USD")
    period_end: Mapped[Optional[date]] = mapped_column(Date)
    fiscal_year: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    recourse: Mapped[Optional[str]] = mapped_column(String(32))  # recourse / nonrecourse / partial
    include_in_bridge: Mapped[bool] = mapped_column(Boolean, default=True)
    derived: Mapped[bool] = mapped_column(Boolean, default=False)
    formula: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    citation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("citations.id"))
    citation: Mapped[Optional["Citation"]] = relationship()


class ForensicFlag(Base):
    """A quantitative 'where is the cash coming from?' divergence flag (brief §6a)."""

    __tablename__ = "forensic_flags"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    # cash_up_no_debt / ap_outrunning_revenue / dpo_climbing / ebitda_vs_ocf_divergence
    flag_type: Mapped[str] = mapped_column(String(48), index=True)
    severity: Mapped[Optional[str]] = mapped_column(String(16))  # info / watch / high
    fiscal_year: Mapped[Optional[int]] = mapped_column(Integer)
    metrics: Mapped[Optional[dict]] = mapped_column(JSON)  # the numbers that triggered it
    narrative: Mapped[Optional[str]] = mapped_column(Text)
    pointer: Mapped[Optional[str]] = mapped_column(Text)  # footnote/MD&A to read next


class MdnaSection(Base):
    """Extracted MD&A (and liquidity/going-concern) text per filing for §7 drift."""

    __tablename__ = "mdna_sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    accession_no: Mapped[Optional[str]] = mapped_column(String(32))
    form_type: Mapped[Optional[str]] = mapped_column(String(16))
    period_end: Mapped[Optional[date]] = mapped_column(Date, index=True)
    section_name: Mapped[Optional[str]] = mapped_column(String(64))
    text: Mapped[Optional[str]] = mapped_column(Text)
    drift_from_prior: Mapped[Optional[float]] = mapped_column(Float)  # cosine distance
    liquidity_tone_score: Mapped[Optional[float]] = mapped_column(Float)  # zero-shot Claude score


class DebtInstrumentRow(Base):
    """Queryable copy of the latest debt schedule per ticker (replaced on each run) —
    feeds cross-company screens and N-PORT holder matching."""

    __tablename__ = "debt_instruments"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    instrument: Mapped[str] = mapped_column(Text)
    xbrl_member: Mapped[Optional[str]] = mapped_column(String(128))
    outstanding: Mapped[Optional[float]] = mapped_column(Float)          # USD
    coupon: Mapped[Optional[str]] = mapped_column(String(80))            # display string
    coupon_pct: Mapped[Optional[float]] = mapped_column(Float)
    spread_pct: Mapped[Optional[float]] = mapped_column(Float)
    effective_rate_pct: Mapped[Optional[float]] = mapped_column(Float)
    rate_type: Mapped[Optional[str]] = mapped_column(String(16))
    rate_base: Mapped[Optional[str]] = mapped_column(String(16))
    maturity: Mapped[Optional[str]] = mapped_column(String(64))
    secured: Mapped[Optional[bool]] = mapped_column(Boolean)
    seniority: Mapped[Optional[str]] = mapped_column(String(64))
    obligor: Mapped[Optional[str]] = mapped_column(String(128))
    governed_by: Mapped[Optional[str]] = mapped_column(String(160))
    asof: Mapped[Optional[str]] = mapped_column(String(10))


class Rate(Base):
    """Key reference-rate observations (SOFR, EFFR, prime, treasuries) — one row per
    (series, date), refreshed by app.rates when stale."""

    __tablename__ = "rates"

    series: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    value: Mapped[float] = mapped_column(Float)
    fetched_at: Mapped[Optional[str]] = mapped_column(String(40))


class Scenario(Base):
    """A saved fulcrum run: structure + assumptions + summary results, for side-by-side compare."""

    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64))
    sim: Mapped[dict] = mapped_column(JSON)          # SimConfig kwargs
    structure: Mapped[dict] = mapped_column(JSON)    # entities / tranches / admin_fees
    results: Mapped[Optional[dict]] = mapped_column(JSON)  # summary stats at save time
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Snapshot(Base):
    """Headline metrics of the latest saved overview per ticker — the cross-company
    screening index. Full snapshots stay as JSON files in app/cache/; this row is the
    queryable summary, upserted on every save_overview. Risk columns fill in when a
    Default Risk run happens for the ticker (nullable until then)."""

    __tablename__ = "snapshots"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    issuer: Mapped[Optional[str]] = mapped_column(Text)
    cik: Mapped[Optional[str]] = mapped_column(String(16))
    years: Mapped[Optional[int]] = mapped_column(Integer)
    last_updated: Mapped[Optional[str]] = mapped_column(String(40))
    saved_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    reported_leverage: Mapped[Optional[float]] = mapped_column(Float)
    economic_leverage: Mapped[Optional[float]] = mapped_column(Float)
    flag_count: Mapped[Optional[int]] = mapped_column(Integer)
    overall_risk: Mapped[Optional[float]] = mapped_column(Float)        # hazard composite
    trained_pd: Mapped[Optional[float]] = mapped_column(Float)          # calibrated 12m PD
    implied_rating: Mapped[Optional[str]] = mapped_column(String(8))
