"""Pipeline orchestration for a single (ticker, years) run.

Phase 1 covers issuer resolution + filing/exhibit retrieval + persistence, and assembles the
header + sources of the Overview. Later phases (XBRL facts, economic-debt bridge, covenants,
MD&A sections) attach their sections here. The function takes a ProgressLog so the API can
stream "what it's doing" to the UI.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .capstack.bridge import build_bridge
from .capstack.covenants import extract_covenant_summary, find_credit_documents
from .core.cache import is_hero, load_latest_overview, load_overview, save_overview
from .capstack.debt_schedule import extract_debt_schedule
from .capstack.forensic import build_forensic_table, detect_flags
from .capstack.mdna import build_mdna_series
from .capstack.obs_llm import extract_obs_items
from .core.config import get_settings
from .core.db import session_scope
from .edgar.client import (
    EdgarClient,
    NoFilingsError,
    TickerNotFoundError,
)
from .edgar.documents import get_filing_text
from .edgar.facts import build_financial_series
from .core.progress import ProgressLog
from .schemas import IssuerHeader, Overview
from .store import (
    filing_refs,
    persist_covenants,
    persist_mdna,
    persist_obs,
    upsert_filings,
)


def run_overview(
    ticker: str,
    years: int,
    progress: Optional[ProgressLog] = None,
    live: bool = False,
) -> Overview:
    """Run the pipeline and return a structured Overview.

    Phase 1: header + sources populated; analytical sections come online in later phases.
    Raises TickerNotFoundError / NoFilingsError for clean handling by the API layer.
    """
    settings = get_settings()
    progress = progress or ProgressLog()
    ticker = ticker.strip().upper()
    years = max(1, min(int(years), 10))
    warnings: list[str] = []

    # Demo safety: serve hero names (and any previously-cached run) instantly unless "Run live".
    if not live:
        cached = load_overview(ticker, years)
        if cached is not None:
            tag = "hero" if is_hero(ticker) else "recent"
            progress.emit(
                f"Served {ticker} from the pre-computed {tag} cache "
                f"(toggle 'Run live' to re-run against EDGAR).", step="cache", pct=100,
            )
            return cached

    progress.emit(f"Resolving ticker {ticker} → CIK…", step="resolve", pct=5)
    client = EdgarClient()
    company = client.resolve_company(ticker)
    cik = str(company.cik)
    issuer_name = getattr(company, "name", ticker)
    resolved_ticker = client.current_ticker(company)
    rename_note = ""
    if resolved_ticker and resolved_ticker.upper() != ticker:
        rename_note = f" — now trades/files as {resolved_ticker}"
    progress.emit(
        f"Resolved {ticker} → {issuer_name} (CIK {cik}){rename_note}.", step="resolve", pct=10
    )

    progress.emit(
        f"Fetching 10-K / 10-Q / 8-K / S-1 / S-4 filings for the last {years} year(s)…",
        step="filings",
        pct=20,
    )
    filings = client.get_filings_in_window(company, years)
    n_credit = sum(f.n_credit_docs for f in filings)
    progress.emit(
        f"Fetched {len(filings)} filings ({n_credit} candidate credit-agreement/indenture exhibits).",
        step="filings",
        pct=40,
    )

    with session_scope() as session:
        rows = upsert_filings(session, ticker, cik, filings)
        sources = filing_refs(rows)

    # --- Phase 2: XBRL forensic cash-vs-debt table + auto-flags (§6a) ---
    forensic_table = []
    forensic_flags = []
    series = None
    progress.emit("Pulling XBRL financial facts (debt, cash, FCF, capex, AP, leases)…",
                  step="xbrl", pct=45)
    try:
        series = build_financial_series(company, years)
        if series.years:
            forensic_table = build_forensic_table(series)
            forensic_flags = detect_flags(series)
            progress.emit(
                f"Built forensic table over FY{series.years[0].fiscal_year}-"
                f"FY{series.years[-1].fiscal_year}; {len(forensic_flags)} divergence flag(s) fired.",
                step="xbrl", pct=60,
            )
        else:
            warnings.append("No annual XBRL financial facts found in the window — forensic table skipped.")
            progress.emit("No usable annual XBRL facts found.", step="xbrl", pct=60)
    except Exception as exc:  # never let an XBRL hiccup take down the overview
        warnings.append(f"XBRL forensic step failed: {exc}")
        progress.emit(f"XBRL forensic step failed: {exc}", step="xbrl", pct=60)

    # --- Phase 3: footnote/OBS LLM extraction → economic-debt bridge + debt schedule (§6b/§6c/§8.3) ---
    economic_debt_bridge = None
    obs_items = []
    obs_extractions = []
    debt_schedule = []
    covenants = []
    subsidiaries = []
    # Document text is fetched even when the LLM is off (runtime toggle or no key) so the
    # EDGAR disk cache stays warm and re-enabling analyzes instantly.
    ft = None
    try:
        progress.emit("Fetching latest 10-K text…", step="obs", pct=65)
        latest_10k = company.get_filings(form="10-K").latest(1)
        ft = get_filing_text(latest_10k) if latest_10k is not None else None
    except Exception as exc:
        warnings.append(f"10-K text fetch failed: {exc}")
    if settings.llm_enabled:
        try:
            progress.emit("Extracting footnotes & MD&A (leases, pension, supplier finance, "
                          "guarantees, VIEs)…", step="obs", pct=68)
            if ft is None:
                warnings.append("Could not extract 10-K text — OBS bridge skipped.")
            else:
                obs_extractions, obs_err = extract_obs_items(ft)
                if obs_err:
                    warnings.append(f"OBS extraction error: {obs_err}")
                economic_debt_bridge, obs_items = build_bridge(series, obs_extractions, ft)
                if obs_items:
                    with session_scope() as session:
                        persist_obs(session, ticker, obs_items)
                instruments, debt_err = extract_debt_schedule(ft)
                debt_schedule = instruments
                if debt_err:
                    warnings.append(f"Debt-schedule extraction error: {debt_err}")
                n_lines = len(economic_debt_bridge.lines) if economic_debt_bridge else 0
                progress.emit(
                    f"Built economic-debt bridge ({n_lines} lines), {len(obs_items)} OBS findings, "
                    f"{len(debt_schedule)} debt instruments.", step="obs", pct=80,
                )
        except Exception as exc:
            warnings.append(f"OBS/bridge step failed: {exc}")
            progress.emit(f"OBS/bridge step failed: {exc}", step="obs", pct=80)

    # --- Phase 4: covenant extraction from credit agreements / indentures (§5) ---
    credit_docs = []
    try:   # locating + fetching the exhibits is EDGAR-only — always runs (warms cache)
        progress.emit("Locating credit agreements / indentures…", step="covenants", pct=83)
        credit_docs = find_credit_documents(company, years)
    except Exception as exc:
        warnings.append(f"Credit-document fetch failed: {exc}")
    if settings.llm_enabled:
        try:
            progress.emit("Extracting covenants from credit documents…",
                          step="covenants", pct=85)
            clause_texts: list[tuple] = []
            for d in credit_docs[:2]:
                summ, clause, _lme = extract_covenant_summary(d)
                if summ:
                    covenants.append(summ)
                    clause_texts.append((summ, clause))
            if clause_texts:
                with session_scope() as session:
                    persist_covenants(session, ticker, clause_texts)
            progress.emit(
                f"Extracted {len(covenants)} covenant package(s) from "
                f"{len(credit_docs)} credit document(s).", step="covenants", pct=92,
            )
        except Exception as exc:
            warnings.append(f"Covenant step failed: {exc}")
            progress.emit(f"Covenant step failed: {exc}", step="covenants", pct=92)

        # --- Phase 4.5: Exhibit 21 legal-entity list (seeds Fulcrum entities) ---
        try:
            progress.emit("Parsing Exhibit 21 (legal-entity list)…", step="entities", pct=93)
            from .capstack.subsidiaries import extract_subsidiaries
            subsidiaries, sub_err = extract_subsidiaries(company)
            if sub_err:
                warnings.append(f"Exhibit 21 parse error: {sub_err}")
            else:
                progress.emit(f"Parsed {len(subsidiaries)} subsidiaries from Exhibit 21.",
                              step="entities", pct=94)
        except Exception as exc:
            warnings.append(f"Exhibit 21 step failed: {exc}")
    else:
        progress.emit("Skipping LLM extraction (LLM analysis is off).", step="obs", pct=88)

    # --- Phase 4.6: leverage timeline, maturity wall, what-changed ---
    # Quarterly TTM cadence when 10-Q XBRL supports it; annual FY-vs-FY fallback otherwise.
    from .capstack.timelines import (
        leverage_timeline, maturity_wall, quarterly_leverage_timeline,
        what_changed, what_changed_quarterly,
    )
    quarters = []
    try:
        progress.emit("Building quarterly TTM series from 10-Q XBRL…", step="quarterly", pct=95)
        from .edgar.facts import build_quarterly_series
        quarters = build_quarterly_series(company, years)
        progress.emit(f"Built {len(quarters)} quarter-end points (TTM flows).",
                      step="quarterly", pct=96)
    except Exception as exc:
        warnings.append(f"Quarterly XBRL step failed (annual cadence used): {exc}")
    lev_timeline = quarterly_leverage_timeline(quarters) or leverage_timeline(forensic_table)
    maturities = maturity_wall(debt_schedule)
    changes = what_changed_quarterly(quarters) or what_changed(forensic_table)

    # --- Phase 5: MD&A section retention — deterministic, runs even without an API key ---
    try:
        progress.emit("Storing MD&A sections per period…", step="mdna", pct=94)
        mdna_periods = build_mdna_series(company, years)
        if mdna_periods:
            with session_scope() as session:
                persist_mdna(session, ticker, mdna_periods)
        progress.emit(f"Stored MD&A for {len(mdna_periods)} period(s).", step="mdna", pct=98)
    except Exception as exc:
        warnings.append(f"MD&A step failed: {exc}")
        progress.emit(f"MD&A step failed: {exc}", step="mdna", pct=98)

    # LLM off → splice the LLM-derived sections from the newest cached snapshot so the
    # dashboard still shows analysis (clearly labeled as prior) instead of empty cards.
    llm_fallback_note = None
    if not settings.llm_enabled:
        prior = load_latest_overview(ticker)
        if prior is not None and (prior.economic_debt_bridge or prior.obs_items
                                  or prior.covenants or prior.subsidiaries
                                  or prior.debt_schedule):
            # ponytail: the note's date drifts forward if a spliced snapshot is itself
            # re-spliced later; per-section provenance tracking if that ever matters.
            snap_date = (prior.header.last_updated or "")[:10] or "an earlier run"
            llm_fallback_note = (f"Prior analysis from {snap_date} — LLM analysis is off. "
                                 "Re-enable to refresh.")
            economic_debt_bridge = economic_debt_bridge or prior.economic_debt_bridge
            obs_items = obs_items or prior.obs_items
            covenants = covenants or prior.covenants
            subsidiaries = subsidiaries or prior.subsidiaries
            debt_schedule = debt_schedule or prior.debt_schedule
        else:
            llm_fallback_note = "LLM analysis is off — re-enable to analyze."
        warnings.append(llm_fallback_note)
        if not settings.llm_key_set:
            warnings.append(
                "ANTHROPIC_API_KEY not set — covenant/OBS extraction skipped; XBRL debt "
                "schedule and forensic flags still run."
            )

    header = IssuerHeader(
        issuer=issuer_name,
        ticker=ticker,
        resolved_ticker=resolved_ticker,
        cik=cik,
        years=years,
        n_filings=len(sources),
        last_updated=datetime.now(timezone.utc).isoformat(),
        from_cache=False,
        llm_enabled=settings.llm_enabled,
    )

    overview = Overview(
        header=header,
        economic_debt_bridge=economic_debt_bridge,
        debt_schedule=debt_schedule,
        forensic_table=forensic_table,
        forensic_flags=forensic_flags,
        obs_items=obs_items,
        covenants=covenants,
        subsidiaries=subsidiaries,
        leverage_timeline=lev_timeline,
        maturity_wall=maturities,
        what_changed=changes,
        sources=sources,
        warnings=warnings,
        llm_fallback_note=llm_fallback_note,
    )

    # Write back so the next non-live request for this (ticker, years) is instant.
    save_overview(ticker, years, overview)
    progress.emit("Assembled overview (cached for instant re-open).", step="assemble", pct=100)
    return overview


__all__ = ["run_overview", "TickerNotFoundError", "NoFilingsError"]
