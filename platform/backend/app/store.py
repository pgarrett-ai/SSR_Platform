"""Persistence helpers: write EDGAR filings/exhibits into SQLite, dedup by accession.
Also owns the screening index (snapshots table) and the FTS5 sync (rebuild_fts)."""
from __future__ import annotations

from sqlalchemy import select, text
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
    rebuild_fts(session, ticker)


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
    rebuild_fts(session, ticker)


def persist_mdna(session: Session, ticker: str, periods) -> None:
    """Store MD&A section text per period (replace prior rows for this ticker)."""
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
        ))
    rebuild_fts(session, ticker)


def persist_debt_instruments(session: Session, ticker: str, instruments,
                             asof) -> None:
    """Replace the ticker's queryable debt-schedule rows with the latest run's."""
    for row in session.scalars(
        select(models.DebtInstrumentRow).where(models.DebtInstrumentRow.ticker == ticker)
    ).all():
        session.delete(row)
    for i in instruments:
        cv = i.outstanding or i.principal
        session.add(models.DebtInstrumentRow(
            ticker=ticker,
            instrument=i.instrument,
            xbrl_member=i.xbrl_member,
            outstanding=cv.value if cv else None,
            coupon=i.coupon,
            coupon_pct=i.coupon_pct,
            spread_pct=i.spread_pct,
            effective_rate_pct=i.effective_rate_pct,
            rate_type=i.rate_type,
            rate_base=i.rate_base,
            maturity=i.maturity,
            secured=i.secured,
            seniority=i.seniority,
            obligor=i.obligor,
            asof=str(asof) if asof else None,
        ))


def upsert_snapshot(session: Session, ticker: str, overview) -> None:
    """Refresh the ticker's screening-index row from an Overview. merge() replaces every
    column, so hazard risk values from a prior Default Risk run are carried over."""
    def _val(cv):
        return cv.value if cv is not None else None

    bridge = overview.economic_debt_bridge
    prior = session.get(models.Snapshot, ticker)
    session.merge(models.Snapshot(
        ticker=ticker,
        issuer=overview.header.issuer,
        cik=overview.header.cik,
        years=overview.header.years,
        last_updated=overview.header.last_updated,
        reported_leverage=_val(bridge.reported_leverage) if bridge else None,
        economic_leverage=_val(bridge.economic_leverage) if bridge else None,
        flag_count=len(overview.forensic_flags),
        overall_risk=prior.overall_risk if prior else None,
        trained_pd=prior.trained_pd if prior else None,
        implied_rating=prior.implied_rating if prior else None,
    ))


def update_snapshot_risk(session: Session, ticker: str, hz: dict) -> None:
    """Fill the hazard columns on an existing snapshot row after a Default Risk run.
    No snapshot row yet (never capstack-analyzed) -> no-op; the screener lists analyzed
    companies only."""
    row = session.get(models.Snapshot, ticker)
    if row is None:
        return
    row.overall_risk = (hz.get("executive_summary") or {}).get("overall_risk")
    trained = (hz.get("scores") or {}).get("Trained hazard") or {}
    row.trained_pd = trained.get("value")
    row.implied_rating = trained.get("implied_rating")


def rebuild_fts(session: Session, ticker: str) -> None:
    """Re-sync the ticker's rows in the FTS5 `search` table from the source tables.
    Runs inside the caller's transaction right after a persist_* delete-then-insert, so
    the index can never drift from the tables. Idempotent. No-op when FTS5 is absent."""
    from .core.db import FTS_AVAILABLE
    if not FTS_AVAILABLE:
        return
    session.flush()   # SessionLocal is autoflush=False; INSERT..SELECT must see new rows
    session.execute(text("DELETE FROM search WHERE ticker = :t"), {"t": ticker})
    session.execute(text("""
        INSERT INTO search(text, source_kind, ticker, ref_id)
        SELECT clause_text, 'covenant', ticker, id FROM covenants
          WHERE ticker = :t AND clause_text IS NOT NULL
        UNION ALL
        SELECT text, 'mdna', ticker, id FROM mdna_sections
          WHERE ticker = :t AND text IS NOT NULL
        UNION ALL
        SELECT COALESCE(label,'') || ' ' || COALESCE(notes,''), 'obs', ticker, id
          FROM obs_items WHERE ticker = :t AND (label IS NOT NULL OR notes IS NOT NULL)
    """), {"t": ticker})


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
