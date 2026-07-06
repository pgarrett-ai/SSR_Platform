"""Persistence helpers: write EDGAR filings/exhibits into SQLite, dedup by accession."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .edgar.client import FilingInfo
from .schemas import CovenantSummary, FilingRef
from .schemas import ObsItem as ObsItemSchema


def upsert_filings(
    session: Session, ticker: str, cik: str, filings: list[FilingInfo]
) -> list[models.Filing]:
    """Insert filings + exhibits that aren't already stored. Returns the persisted rows."""
    stored: list[models.Filing] = []
    for fi in filings:
        existing = session.scalar(
            select(models.Filing).where(models.Filing.accession_no == fi.accession_no)
        )
        if existing is None:
            row = models.Filing(
                cik=str(cik),
                ticker=ticker,
                accession_no=fi.accession_no,
                form_type=fi.form_type,
                filing_date=fi.filing_date,
                period_of_report=fi.period_of_report,
                primary_doc_url=fi.primary_doc_url,
                filing_index_url=fi.filing_index_url,
            )
            for ex in fi.exhibits:
                row.exhibits.append(
                    models.Exhibit(
                        exhibit_type=ex.exhibit_type,
                        description=ex.description,
                        document=ex.document,
                        url=ex.url,
                        is_credit_doc=ex.is_credit_doc,
                    )
                )
            session.add(row)
            stored.append(row)
        else:
            stored.append(existing)
    session.flush()
    return stored


def persist_covenants(
    session: Session, ticker: str, items: list[tuple[CovenantSummary, str]]
) -> None:
    """Store extracted covenant packages, keeping `clause_text` so a vector index can be added
    later (brief §5 — embedding/clustering is v2; we just keep the raw clauses now)."""
    # Replace prior rows for this ticker so re-runs don't accumulate duplicates.
    for row in session.scalars(
        select(models.Covenant).where(models.Covenant.ticker == ticker)
    ).all():
        session.delete(row)
    for summ, clause_text in items:
        cit = summ.citation
        citation_row = None
        if cit is not None:
            citation_row = models.Citation(
                accession_no=cit.accession_no,
                form_type=cit.form_type,
                exhibit=cit.exhibit,
                section=cit.section,
                source_url=cit.source_url,
                quote=cit.quote,
            )
            session.add(citation_row)
            session.flush()
        session.add(models.Covenant(
            ticker=ticker,
            accession_no=cit.accession_no if cit else None,
            agreement_type=summ.agreement_type,
            leverage_covenant_type=summ.leverage_covenant_type,
            leverage_ratio_threshold=summ.leverage_ratio_threshold,
            ebitda_addback_categories=summ.ebitda_addback_categories,
            restricted_payments_basket_size=summ.restricted_payments_basket_size,
            mfn_sunset_period=summ.mfn_sunset_period,
            j_crew_blocker_present=summ.j_crew_blocker_present,
            unrestricted_subsidiary_designation_flexibility=(
                summ.unrestricted_subsidiary_designation_flexibility
            ),
            clause_text=clause_text,
            citation_id=citation_row.id if citation_row else None,
        ))


def persist_obs(session: Session, ticker: str, items: list[ObsItemSchema]) -> None:
    """Store extracted off-balance-sheet items (replace prior rows for this ticker)."""
    for row in session.scalars(
        select(models.ObsItem).where(models.ObsItem.ticker == ticker)
    ).all():
        session.delete(row)
    for it in items:
        amount = it.amount
        citation_id = None
        if amount is not None and amount.citation is not None:
            cit = amount.citation
            citation_row = models.Citation(
                accession_no=cit.accession_no,
                form_type=cit.form_type,
                section=cit.section,
                source_url=cit.source_url,
                quote=cit.quote,
            )
            session.add(citation_row)
            session.flush()
            citation_id = citation_row.id
        session.add(models.ObsItem(
            ticker=ticker,
            category=it.category,
            label=it.label,
            amount=amount.value if amount else None,
            recourse=it.recourse,
            include_in_bridge=it.include_in_bridge,
            derived=bool(amount.derived) if amount else False,
            formula=amount.formula if amount else None,
            notes=it.notes,
            citation_id=citation_id,
        ))


def persist_mdna(session: Session, ticker: str, periods) -> None:
    """Store MD&A sections + computed drift/tone for §7 (replace prior rows for this ticker)."""
    for row in session.scalars(
        select(models.MdnaSection).where(models.MdnaSection.ticker == ticker)
    ).all():
        session.delete(row)
    for p in periods:
        session.add(models.MdnaSection(
            ticker=ticker,
            accession_no=p.accession,
            form_type=p.form_type,
            period_end=p.period_end,
            section_name="MD&A",
            text=(p.text or "")[:200000],
            drift_from_prior=p.drift_from_prior,
            liquidity_tone_score=p.tone,
        ))


def filing_refs(filings: list[models.Filing]) -> list[FilingRef]:
    refs: list[FilingRef] = []
    for f in filings:
        refs.append(
            FilingRef(
                accession_no=f.accession_no,
                form_type=f.form_type,
                filing_date=f.filing_date.isoformat() if f.filing_date else None,
                period_of_report=(
                    f.period_of_report.isoformat() if f.period_of_report else None
                ),
                primary_doc_url=f.primary_doc_url,
                filing_index_url=f.filing_index_url,
                n_exhibits=len(f.exhibits),
                n_credit_docs=sum(1 for e in f.exhibits if e.is_credit_doc),
            )
        )
    # newest first
    refs.sort(key=lambda r: (r.filing_date or ""), reverse=True)
    return refs
