"""FastAPI app: the unified distressed-credit platform API (one process, three modules).

Endpoints
  GET  /api/health                      — liveness + module configuration
  GET  /api/company/{ticker}            — canonical snapshot: capstack + hazard sections
  POST /api/company/{ticker}/recovery/simulate — fulcrum Monte Carlo on the extracted cap table
  GET  /api/overview?ticker=&years=     — capstack Overview (JSON) [legacy route, kept]
  GET  /api/overview/stream?ticker=...  — SSE: progress events, then the final overview
  GET  /api/filings?ticker=&years=      — just the filing/exhibit list
"""
from __future__ import annotations

import asyncio
import json
import queue
import re
import secrets
import threading
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

import datetime as dt

from . import models
from . import models_events
from .core.cache import cached_tickers, load_latest_overview, load_overview, safe_ticker
from .core.config import CACHE_DIR, get_settings, set_llm_runtime_enabled
from .core.db import init_db, session_scope
from .edgar.client import NoFilingsError, TickerNotFoundError
from .fulcrum import CapitalStructure, Entity, SimConfig, Tranche
from .fulcrum import analyze as fulcrum_analyze
from .fulcrum.adapter import overview_to_structure
from .fulcrum.waterfall import run_waterfall
from .hazard.pipeline import analyze as hazard_analyze
from .pipeline import run_overview
from .store import update_snapshot_risk
from .events.heartbeat import worker_status
from .core.progress import ProgressEvent, ProgressLog


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Capital Structure & Hidden-Leverage Analyzer",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_MAX_BODY_BYTES = 5_000_000   # reject oversized POST bodies before Starlette buffers/parses them


@app.middleware("http")
async def _limit_request_body(request, call_next):
    cl = request.headers.get("content-length")
    if cl is not None and cl.isdigit() and int(cl) > _MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"error": "request body too large (max 5 MB)"})
    return await call_next(request)


@app.middleware("http")
async def _bearer_auth(request, call_next):
    """Auth v1 (plan §11): when PLATFORM_API_TOKEN is set, every /api/* request needs
    `Authorization: Bearer <token>`. /api/health stays open — liveness probes and the
    token-entry UI need a pre-auth signal, and it carries operational metadata only.
    EventSource can't send headers, so the platform_token cookie is an equivalent
    bearer for the SSE routes. Unset token (localhost default) = open. Registered after
    _limit_request_body so Starlette runs it outermost."""
    token = get_settings().platform_api_token.strip()
    path = request.url.path
    if token and path.startswith("/api/") and path != "/api/health":
        auth = request.headers.get("authorization", "")
        supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        supplied = supplied or request.cookies.get("platform_token", "")
        # byte comparison: str compare_digest raises TypeError on non-ASCII (an
        # attacker-triggerable 500 in the auth path); bytes never raise, still timing-safe
        if not secrets.compare_digest(supplied.encode("utf-8"), token.encode("utf-8")):
            return JSONResponse(status_code=401, content={"error": "unauthorized"},
                                headers={"WWW-Authenticate": "Bearer"})
    return await call_next(request)


@app.get("/", include_in_schema=False)
def root():
    """The API has no UI — send stray visitors (e.g. a preview tab on :8001) to the docs."""
    return RedirectResponse("/docs")


_QUIET_AFTER_H = 6.0


def _zero_ingest_alarm(worker: dict, now: dt.datetime) -> bool:
    """Poller silent-death alarm (plan §10), from PR-2b's raw worker_status() gauges.
    True when: the heartbeat exists but is dead, OR the worker is alive yet detected
    nothing for 6h inside weekday filing hours (EDGAR runs 3-6k filings/business day —
    a quiet afternoon means a broken poller, not a quiet market). Never alarms before
    the first beat: not-deployed != dead.
    ponytail: fixed 13-23 UTC weekday window ≈ EDGAR filing hours; add a holiday
    calendar only if false alarms actually annoy."""
    if worker.get("heartbeat_age_s") is None:
        return False                       # never deployed
    if not worker.get("alive"):
        return True
    if now.weekday() >= 5 or not 13 <= now.hour < 23:
        return False
    last = worker.get("last_event_hours")
    return last is None or last > _QUIET_AFTER_H


@app.get("/api/health")
def health() -> dict:
    s = get_settings()
    try:   # worker gauges are read once and must never take health down (uptime probe)
        w = worker_status()
        alarm = _zero_ingest_alarm(w, dt.datetime.utcnow())
    except Exception:
        w, alarm = {"alive": False}, False
    return {
        "status": "ok",
        "llm_enabled": s.llm_enabled,
        "llm_key_set": s.llm_key_set,   # lets the UI tell "toggled off" from "no key"
        "auth_required": bool(s.platform_api_token.strip()),   # PR-6: SPA shows the token box
        "hero_tickers": sorted(s.hero_ticker_set),
        "cached": cached_tickers(),
        "sec_user_agent_set": bool(s.sec_user_agent and "example.com" not in s.sec_user_agent),
        "worker": w,                       # PR-2b raw gauges (kept)
        "zero_ingest_alarm": alarm,        # PR-6 filing-hours-aware alarm from those gauges
    }


class LlmToggleBody(BaseModel):
    enabled: bool


@app.post("/api/settings/llm")
def set_llm(body: LlmToggleBody) -> dict:
    set_llm_runtime_enabled(body.enabled)
    s = get_settings()
    return {"llm_enabled": s.llm_enabled, "llm_key_set": s.llm_key_set}


def _handle_pipeline_errors(fn):
    try:
        return fn()
    except TickerNotFoundError as exc:
        return JSONResponse(status_code=404, content={"error": "ticker_not_found", "detail": str(exc)})
    except NoFilingsError as exc:
        return JSONResponse(status_code=404, content={"error": "no_filings", "detail": str(exc)})
    except Exception as exc:  # graceful failure — app stays up (brief §9)
        return JSONResponse(
            status_code=500, content={"error": "pipeline_error", "detail": str(exc)}
        )


@app.get("/api/overview")
def overview(
    ticker: str = Query(..., min_length=1, max_length=12, pattern=r"^[A-Za-z0-9.\-]+$"),
    years: int = Query(3, ge=1, le=10),
    live: bool = Query(False),
):
    def _run():
        ov = run_overview(ticker, years, live=live)
        return JSONResponse(content=json.loads(ov.model_dump_json()))

    return _handle_pipeline_errors(_run)


@app.get("/api/filings")
def filings(
    ticker: str = Query(..., min_length=1, max_length=12, pattern=r"^[A-Za-z0-9.\-]+$"),
    years: int = Query(3, ge=1, le=10),
):
    def _run():
        ov = run_overview(ticker, years)
        return JSONResponse(
            content={
                "header": json.loads(ov.header.model_dump_json()),
                "sources": [json.loads(s.model_dump_json()) for s in ov.sources],
                "warnings": ov.warnings,
            }
        )

    return _handle_pipeline_errors(_run)


def _native(obj):
    """Recursively convert numpy scalars/arrays (hazard payloads) to JSON-safe types."""
    if isinstance(obj, dict):
        return {k: _native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_native(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return None
    return obj


def _hazard_section(ticker: str, years: int, live: bool, progress: ProgressLog | None = None) -> dict:
    """Same-day disk cache around the hazard pipeline. Market data moves daily and EDGAR on
    filings, so a day-fresh payload serves page reloads instantly instead of re-running the
    ~30s pipeline; live=True bypasses. Kept in its own subdir so the overview-cache globs
    (TICKER_*y.json) never pick these up."""
    try:
        safe_t = safe_ticker(ticker)   # trust boundary: keep request-derived ticker out of the path
    except ValueError:
        # invalid ticker -> skip the cache path entirely; hazard_analyze resolves it and raises cleanly
        return jsonable(_native(hazard_analyze(ticker, years, progress=progress)))
    p = CACHE_DIR / "hazard" / f"{safe_t}_{int(years)}y.json"
    today = dt.date.today().isoformat()
    if not live and p.exists():
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
            if blob.get("as_of") == today:
                if progress:
                    progress.emit("Served from today's hazard cache.", step="cache", pct=100)
                return blob["data"]
        except Exception:
            pass
    data = jsonable(_native(hazard_analyze(ticker, years, progress=progress)))
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"as_of": today, "data": data}), encoding="utf-8")
    except Exception:
        pass  # caching is best-effort; never fail a request over it
    return data


@app.get("/api/company/{ticker}")
def company(
    ticker: str,
    years: int = Query(3, ge=1, le=10),
    live: bool = Query(False),
    sections: str = Query("capstack,hazard"),
):
    """The canonical company snapshot: each requested module contributes a section.

    A section failure degrades to {"error": ...} instead of failing the whole payload
    (the same graceful-degradation pattern the capstack pipeline uses internally).
    """
    requested = {s.strip() for s in sections.split(",") if s.strip()}
    out: dict = {"ticker": ticker.strip().upper(), "years": years, "sections": {}}

    if "capstack" in requested:
        try:
            ov = run_overview(ticker, years, live=live)
            out["sections"]["capstack"] = json.loads(ov.model_dump_json())
        except (TickerNotFoundError, NoFilingsError) as exc:
            return JSONResponse(status_code=404, content={"error": str(exc)})
        except Exception as exc:
            out["sections"]["capstack"] = {"error": str(exc)}

    if "hazard" in requested:
        try:
            out["sections"]["hazard"] = _hazard_section(ticker, years, live)
        except TickerNotFoundError as exc:
            return JSONResponse(status_code=404, content={"error": str(exc)})
        except Exception as exc:
            out["sections"]["hazard"] = {"error": str(exc)}
        else:
            try:   # fill the screening index's risk columns; never fail the request
                with session_scope() as session:
                    update_snapshot_risk(session, out["ticker"], out["sections"]["hazard"])
            except Exception:
                pass

    return JSONResponse(content=jsonable(out))


def _distress_badge(session, ticker: str, last_price: Optional[float]) -> Optional[bool]:
    """Moyer fact pattern (ch. 1): equity de minimis (< $1) AND any unsecured quote < 60
    (> 40% discount). Live against the drop-file; None when either input is missing."""
    from .capstack.quotes import match_quotes
    from .hazard.trace import get_issuer_bonds

    if last_price is None:
        return None
    bonds = get_issuer_bonds(ticker).get("bonds") or []
    if not bonds:
        return None
    rows = (session.query(models.DebtInstrumentRow)
            .filter(models.DebtInstrumentRow.ticker == ticker).all())
    sched = [{"instrument": r.instrument, "coupon_pct": r.coupon_pct,
              "maturity": r.maturity} for r in rows if r.secured is False]
    matches, _ = match_quotes(sched, bonds)
    prices = [q.get("last_price") for q in matches.values() if q.get("last_price") is not None]
    if not prices:
        return None
    return bool(last_price < 1.0 and min(prices) < 60.0)


@app.get("/api/screen")
def screen() -> JSONResponse:
    """Every analyzed company's headline metrics — filtering happens client-side."""
    from sqlalchemy import desc, nulls_last

    with session_scope() as session:
        rows = (session.query(models.Snapshot)
                .order_by(nulls_last(desc(models.Snapshot.economic_leverage))).all())
        out = []
        for r in rows:
            try:
                badge = _distress_badge(session, r.ticker, r.last_price)
            except Exception:
                badge = None
            out.append({
                "ticker": r.ticker, "issuer": r.issuer, "last_updated": r.last_updated,
                "reported_leverage": r.reported_leverage,
                "economic_leverage": r.economic_leverage,
                "net_market_leverage": r.net_market_leverage,
                "creation_multiple_fulcrum": r.creation_multiple_fulcrum,
                "ebitda_capex_leverage": r.ebitda_capex_leverage,
                "flag_count": r.flag_count,
                "overall_risk": r.overall_risk, "trained_pd": r.trained_pd,
                "implied_rating": r.implied_rating,
                "distress_badge": badge,
            })
        return JSONResponse(content=jsonable(out))


@app.get("/api/company/{ticker}/bonds")
def issuer_bonds(ticker: str) -> JSONResponse:
    """Per-issuer TRACE quotes from the manual drop-file (graceful when absent)."""
    from .hazard.trace import get_issuer_bonds

    return JSONResponse(content=jsonable(get_issuer_bonds(ticker)))


@app.get("/api/company/{ticker}/capacity")
def credit_capacity(ticker: str, years: int = Query(3, ge=1, le=10)):
    """Credit-capacity card (Moyer ch. 6): cash-sweep repayment %, leverage×growth
    heatmap, cycle-severity slices. Deterministic, from the cached overview."""
    from .capstack.capacity import build_capacity

    def _run():
        ov = json.loads(run_overview(ticker, years).model_dump_json())
        return JSONResponse(content=jsonable(build_capacity(ov)))

    return _handle_pipeline_errors(_run)


@app.get("/api/company/{ticker}/capital/ladder")
def capital_ladder(ticker: str, years: int = Query(3, ge=1, le=10),
                   recast_mezz: int = Query(0, ge=0, le=1)):
    """Creation-multiple ladder (Moyer): cumulative claims through each class at face and
    at market ÷ EBITDA. On-demand (not cached in the overview) so a drop-file refresh
    reprices without a pipeline run. recast_mezz=1 appends temporary equity as a
    preferred claim before the structure is derived (Moyer ch. 6)."""
    from .capstack.basis import build_basis
    from .capstack.creation import (build_creation_ladder, detect_capacity_avoidance,
                                    mezz_recast_row)
    from .capstack.quotes import spread_bps
    from .hazard.trace import get_issuer_bonds

    def _run():
        ov = json.loads(run_overview(ticker, years).model_dump_json())
        feed = get_issuer_bonds(ticker)
        bonds = feed.get("bonds") or []
        if recast_mezz:
            mezz_row = mezz_recast_row(ov)
            if mezz_row:
                ov["debt_schedule"] = [*(ov.get("debt_schedule") or []), mezz_row]
        payload = build_creation_ladder(ov, bonds)

        # per-quote spread flags (F7c) off the coarse 3-point treasury curve;
        # equity price (detector input) from the same session
        treasuries: dict[str, float] = {}
        equity_price = None
        try:
            from .rates import get_key_rates, refresh_if_stale
            with session_scope() as session:
                refresh_if_stale(session)
                for row in get_key_rates(session):
                    treasuries[row["series"]] = row["value"]
                snap = session.get(models.Snapshot, ticker.strip().upper())
                equity_price = snap.last_price if snap else None
        except Exception:
            pass
        quotes_out = []
        for b in bonds:
            spr = spread_bps(b, treasuries) if treasuries else None
            quotes_out.append({**b, "spread_bps": spr,
                               "wide_spread": bool(spr is not None and spr > 1000)})
        payload["quotes"] = quotes_out
        payload["quote_feed"] = {k: v for k, v in feed.items() if k != "bonds"}
        payload["basis"] = build_basis(ov, bonds)
        payload["detector"] = detect_capacity_avoidance(ov, equity_price, bonds)
        return JSONResponse(content=jsonable(payload))

    return _handle_pipeline_errors(_run)


@app.get("/api/company/{ticker}/capital/refi")
def capital_refi(ticker: str, years: int = Query(3, ge=1, le=10)):
    """Refi-wall sequencing (Moyer ch. 6/10): per maturity bucket, internal repayability
    (sequential sweep funding), the conditional-PD leg from the cached hazard inputs,
    and the drop-file market overlay. On-demand so a quote refresh reprices without a
    pipeline run."""
    from .capstack.refi import build_refi_wall, hazard_inputs
    from .hazard.trace import get_issuer_bonds

    def _run():
        ov = json.loads(run_overview(ticker, years).model_dump_json())
        bonds = get_issuer_bonds(ticker).get("bonds") or []
        return JSONResponse(content=jsonable(
            build_refi_wall(ov, bonds, hazard_inputs(ticker))))

    return _handle_pipeline_errors(_run)


@app.get("/api/company/{ticker}/telegraph")
def telegraph(ticker: str, years: int = Query(3, ge=1, le=10)):
    """Bank-position triage + filing-telegraph signals (Moyer ch. 8) in one payload.
    An empty debt schedule (ATUS/TSE) degrades to bank.available: false while the
    disclosure/payables signals still evaluate."""
    from .capstack.triage import bank_triage, filing_telegraph

    def _run():
        ov = json.loads(run_overview(ticker, years).model_dump_json())
        bank = bank_triage(ov)
        with session_scope() as session:   # FTS phrase scan
            tel = filing_telegraph(ov, session)
        return JSONResponse(content=jsonable(
            {"available": True, "bank": bank, "telegraph": tel}))

    return _handle_pipeline_errors(_run)


@app.get("/api/company/{ticker}/options")
def company_options(ticker: str, years: int = Query(3, ge=1, le=10)):
    """Company-options feasibility card (Moyer ch. 11): the clock, the buyback math,
    the exchange gate, and the asset-sale explorer inputs. On-demand so a drop-file
    refresh reprices without a pipeline run."""
    from .capstack.options import build_options
    from .hazard.trace import get_issuer_bonds

    def _run():
        ov = json.loads(run_overview(ticker, years).model_dump_json())
        bonds = get_issuer_bonds(ticker).get("bonds") or []
        return JSONResponse(content=jsonable(build_options(ov, bonds)))

    return _handle_pipeline_errors(_run)


@app.get("/api/rates")
def key_rates() -> JSONResponse:
    """Latest key reference rates (SOFR, EFFR, Fed Funds target, prime, T-bill, 10Y/30Y) —
    stored in the DB, refreshed when stale, served with their observation dates."""
    from .rates import get_key_rates, refresh_if_stale

    with session_scope() as session:
        refresh_if_stale(session)
        return JSONResponse(content={"rates": get_key_rates(session)})


@app.get("/api/company/{ticker}/holders")
def known_holders(ticker: str) -> JSONResponse:
    """Registered-fund holders of the issuer's debt (N-PORT data set, when ingested),
    grouped by matched instrument, largest positions first."""
    from sqlalchemy import desc, nulls_last

    from .nport import COVERAGE_NOTE

    with session_scope() as session:
        rows = (session.query(models.NportHolding)
                .filter(models.NportHolding.ticker == ticker.upper())
                .order_by(nulls_last(desc(models.NportHolding.value_usd)))
                .limit(500).all())
        return JSONResponse(content=jsonable({
            "note": COVERAGE_NOTE,
            "quarter": rows[0].report_quarter if rows else None,
            "holdings": [{
                "fund_name": r.fund_name, "title": r.title, "instrument": r.instrument,
                "value_usd": r.value_usd, "pct_of_fund": r.pct_of_fund, "cusip": r.cusip,
            } for r in rows],
        }))


@app.get("/api/company/{ticker}/mdna")
def mdna_periods(ticker: str) -> JSONResponse:
    """Stored MD&A sections for the ticker, newest first — the reader's table of contents."""
    from sqlalchemy import desc, nulls_last

    from .edgar.client import index_url_for

    with session_scope() as session:
        snap = session.get(models.Snapshot, ticker.upper())
        cik = snap.cik if snap else None
        rows = (session.query(models.MdnaSection)
                .filter(models.MdnaSection.ticker == ticker.upper())
                .order_by(nulls_last(desc(models.MdnaSection.period_end))).all())
        return JSONResponse(content=jsonable([{
            "accession_no": r.accession_no, "form_type": r.form_type,
            "period_end": r.period_end, "n_chars": len(r.text or ""),
            "source_url": index_url_for(cik, r.accession_no) if cik and r.accession_no else None,
        } for r in rows]))


@app.get("/api/company/{ticker}/mdna/{accession_no}")
def mdna_text(ticker: str, accession_no: str) -> JSONResponse:
    """Full stored MD&A text for one filing period."""
    with session_scope() as session:
        row = (session.query(models.MdnaSection)
               .filter(models.MdnaSection.ticker == ticker.upper(),
                       models.MdnaSection.accession_no == accession_no).first())
        if row is None:
            raise HTTPException(status_code=404, detail="No stored MD&A for that filing")
        return JSONResponse(content=jsonable({
            "accession_no": row.accession_no, "form_type": row.form_type,
            "period_end": row.period_end, "text": row.text or "",
        }))


@app.get("/api/search")
def search(q: str = Query(..., min_length=1, max_length=200),
           ticker: Optional[str] = Query(None, max_length=12, pattern=r"^[A-Za-z0-9.\-]+$"),
           limit: int = Query(20, ge=1, le=100)) -> JSONResponse:
    """BM25 full-text search over covenant clauses, MD&A, and OBS narratives (FTS5),
    optionally scoped to one issuer."""
    from sqlalchemy import text as sql

    from .core.db import FTS_AVAILABLE
    if not FTS_AVAILABLE:
        return JSONResponse(content={"hits": [], "note": "FTS5 unavailable in this build"})
    # Trust boundary: quote each token so user input can't hit FTS query syntax
    # (implicit AND between quoted tokens is preserved).
    match = " ".join(f'"{tok.replace(chr(34), chr(34) * 2)}"' for tok in q.split())
    tk_filter = " AND ticker = :t" if ticker else ""
    params = {"q": match, "n": limit}
    if ticker:
        params["t"] = ticker.strip().upper()
    with session_scope() as session:
        rows = session.execute(sql(
            "SELECT source_kind, ticker, ref_id, "
            "snippet(search, 0, '<mark>', '</mark>', ' ... ', 24) AS snip "
            f"FROM search WHERE search MATCH :q{tk_filter} ORDER BY bm25(search) LIMIT :n"),
            params).all()
        return JSONResponse(content={"hits": [
            {"source_kind": r[0], "ticker": r[1], "ref_id": r[2], "snippet": r[3]}
            for r in rows]})


# ---- events feed + company timeline (Phase 6 event store; PR-5) --------------------

_EVENT_TYPE_RE = re.compile(r"^[a-z0-9_.\-]{1,48}$")   # detector slugs; trust boundary


def _pad_cik(cik) -> Optional[str]:
    """Any CIK form ('6201', 'CIK0000006201') -> the event store's canonical 10-digit
    zero-padded key (eightk.py convention); None for junk/empty."""
    if not cik:
        return None
    digits = str(cik).strip().upper().lstrip("CIK").lstrip("0")
    return digits.zfill(10) if digits.isdigit() and digits else None


def _resolve_cik(session, ticker: str) -> Optional[str]:
    """ticker -> event-store CIK: raw CIK forms pass through; universe first
    (daily-refreshed); snapshots fallback (names analyzed before the universe job ran)."""
    t = ticker.strip().upper()
    if t.lstrip("CIK").isdigit():          # raw CIK typed/linked directly
        return _pad_cik(t)
    row = (session.query(models_events.UniverseCompany)
           .filter(models_events.UniverseCompany.ticker == t).first())
    if row is not None:
        return _pad_cik(row.cik)
    snap = session.get(models.Snapshot, t)
    return _pad_cik(snap.cik) if snap is not None else None


def _event_dict(e, ticker: Optional[str] = None) -> dict:
    return {
        "id": e.id, "cik": e.cik, "ticker": ticker,
        "event_type": e.event_type, "subtype": e.subtype,
        "severity": e.severity, "confidence": e.confidence,
        "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
        "detected_at": e.detected_at.isoformat() if e.detected_at else None,
        "source": e.source, "source_form": e.source_form,
        "accession_no": e.accession_no, "source_url": e.source_url,
        "title": e.title, "payload": e.payload,
    }


@app.get("/api/events")
def list_events(
    cik: Optional[str] = Query(None, min_length=1, max_length=10, pattern=r"^\d+$"),
    ticker: Optional[str] = Query(None, min_length=1, max_length=12,
                                  pattern=r"^[A-Za-z0-9.\-]+$"),
    event_type: Optional[list[str]] = Query(None),
    min_severity: Optional[int] = Query(None, ge=1, le=5),
    since: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    until: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=100_000),
):
    """Event-store firehose (plan §9). Ordered detected_at DESC NULLS LAST — live
    detections first, honest backfill (detected_at NULL) last. since/until filter
    occurred_at (the world-time axis; backfill rows have no detected_at).
    No total count: the client pages by 'came back full'."""
    from sqlalchemy import desc, nulls_last

    for t in event_type or []:
        if not _EVENT_TYPE_RE.fullmatch(t):
            return JSONResponse(status_code=400,
                                content={"error": f"bad event_type {t!r}"})
    try:
        since_d = dt.date.fromisoformat(since) if since else None
        until_d = dt.date.fromisoformat(until) if until else None
    except ValueError as exc:              # pattern passed but not a real date
        return JSONResponse(status_code=400, content={"error": str(exc)})

    with session_scope() as session:
        the_cik = _pad_cik(cik) if cik else None
        if cik and the_cik is None:        # supplied CIK padded to nothing (e.g. all-zeros):
            return JSONResponse(content={  # don't fall through to the unfiltered firehose
                "events": [], "limit": limit, "offset": offset})
        if the_cik is None and ticker:
            the_cik = _resolve_cik(session, ticker)
            if the_cik is None:
                return JSONResponse(status_code=404, content={
                    "error": "ticker_not_found",
                    "detail": f"{ticker.strip().upper()!r} is in neither the universe "
                              f"nor the snapshot index"})
        q = (session.query(models_events.Event, models_events.UniverseCompany.ticker)
             .outerjoin(models_events.UniverseCompany,
                        models_events.UniverseCompany.cik == models_events.Event.cik))
        if the_cik:
            q = q.filter(models_events.Event.cik == the_cik)
        if event_type:
            q = q.filter(models_events.Event.event_type.in_(event_type))
        if min_severity:
            q = q.filter(models_events.Event.severity >= min_severity)
        if since_d:
            q = q.filter(models_events.Event.occurred_at
                         >= dt.datetime.combine(since_d, dt.time.min))
        if until_d:                        # inclusive end date
            q = q.filter(models_events.Event.occurred_at
                         < dt.datetime.combine(until_d + dt.timedelta(days=1), dt.time.min))
        rows = (q.order_by(nulls_last(desc(models_events.Event.detected_at)),
                           desc(models_events.Event.occurred_at),
                           desc(models_events.Event.id))
                .offset(offset).limit(limit).all())
        return JSONResponse(content=jsonable({
            "events": [_event_dict(e, tk) for e, tk in rows],
            "limit": limit, "offset": offset}))


def _change_period_end(items: list[dict]) -> Optional[str]:
    """Date a what-changed card for the sorted vertical: quarter-end from 'Q3 2025'
    labels, else FY-end. Derived-display only — never presented as a filing date."""
    it = items[0]
    m = re.match(r"^Q([1-4])\s+(\d{4})$", it.get("latest_label") or "")
    if m:
        q, y = int(m.group(1)), int(m.group(2))
        return f"{y}-{q * 3:02d}-{[31, 30, 30, 31][q - 1]:02d}"
    fy = it.get("latest_fy")
    return f"{fy}-12-31" if fy else None


@app.get("/api/company/{ticker}/timeline")
def company_timeline(ticker: str, years: int = Query(3, ge=1, le=10),
                     limit: int = Query(300, ge=1, le=500)):
    """Company Timeline tab (plan §9): event-store rows + the cached overview's filings
    (`sources`) and what-changed card, one merged vertical, newest first. Cache+DB only —
    a page mount must never launch a live pipeline run (PR-B). Filings that already
    exist as events (same accession) are dropped in favor of the richer event row."""
    from sqlalchemy import desc, nulls_last

    ov = _cached_overview(ticker, years)   # {} when the company was never built
    items: list[dict] = []
    seen_acc: set[str] = set()
    with session_scope() as session:
        cik = _pad_cik(_ov_cik(ov)) or _resolve_cik(session, ticker)
        if cik:
            evs = (session.query(models_events.Event)
                   .filter(models_events.Event.cik == cik)
                   .order_by(nulls_last(desc(models_events.Event.detected_at)),
                             desc(models_events.Event.occurred_at))
                   .limit(limit).all())
            for e in evs:
                if e.accession_no:
                    seen_acc.add(e.accession_no)
                items.append({"kind": "event",
                              "date": e.occurred_at.date().isoformat()
                                      if e.occurred_at else None,
                              **_event_dict(e)})
    for s in ov.get("sources") or []:
        if s.get("accession_no") in seen_acc:
            continue
        items.append({"kind": "filing", "date": s.get("filing_date"),
                      "form_type": s.get("form_type"),
                      "accession_no": s.get("accession_no"),
                      "url": s.get("filing_index_url") or s.get("primary_doc_url")})
    changes = ov.get("what_changed") or []
    if changes:
        c0 = changes[0]
        label = (f"{c0.get('latest_label') or c0.get('latest_fy')} vs "
                 f"{c0.get('prior_label') or c0.get('prior_fy')}")
        items.append({"kind": "changes", "date": _change_period_end(changes),
                      "label": label, "items": changes})
    items.sort(key=lambda x: x.get("date") or "", reverse=True)   # None-dated last
    note = None if ov else ("no cached overview — open this company's Overview tab once "
                            "to add its filings and what-changed items to the timeline")
    return JSONResponse(content=jsonable({
        "ticker": ticker.strip().upper(), "cik": cik, "items": items, "note": note}))


def jsonable(obj):
    """dates and other non-JSON scalars -> strings (filing dicts carry datetime.date)."""
    return json.loads(json.dumps(obj, default=str))


class SimulateBody(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)   # reject Infinity/NaN floats at the boundary
    sim: dict = {}                      # SimConfig overrides (base_ebitda, corr, n_draws, ...)
    structure: Optional[dict] = None    # explicit {entities, tranches, admin_fees}; else derived
    petition_date: Optional[str] = None  # derives accrual_years vs debt_schedule_asof (Moyer:
                                         # unsecured interest tolls at the petition date)
    attack: Optional[str] = None         # priority-attack scenario (fulcrum.attacks)
    attack_target: Optional[str] = None  # tranche name; default = all secured
    mode: Optional[str] = None           # "liquidation" forces the asset-based waterfall
    priming: Optional[dict] = None       # {face ($mm), entity?} — rank-0 secured layer
                                         # (fulcrum.proforma.prime, Moyer ch. 9)


def _structure_dict(structure: CapitalStructure) -> dict:
    return {
        "name": structure.name,
        "entities": [e.__dict__ for e in structure.entities],
        "tranches": [t.__dict__ for t in structure.tranches],
        "admin_fees": structure.admin_fees,
        "admin_pct": structure.admin_pct,
    }


def _derive_structure(ticker: str, years: int) -> tuple[CapitalStructure, Optional[float], str, dict, list, dict]:
    """Cap table from the capstack overview (cache-first). If no debt schedule was extracted,
    seed one editable tranche from the forensic table's latest cited total debt. Also returns the
    Exhibit 21 subsidiary list (Recovery editor entity seed) and the raw overview dict."""
    ov = json.loads(run_overview(ticker, years).model_dump_json())
    structure, ebitda, citations = overview_to_structure(ov)
    subsidiaries = ov.get("subsidiaries") or []
    source = "filed debt schedule"
    if not structure.tranches:
        total_debt, citations = None, {}
        for row in reversed(ov.get("forensic_table") or []):
            cv = row.get("total_debt")
            if cv and cv.get("value"):
                total_debt = float(cv["value"]) / 1e6
                if cv.get("citation"):
                    citations["Total debt (XBRL seed)"] = cv["citation"]
                break
        structure = CapitalStructure(
            name=structure.name,
            entities=[Entity("OpCo", ev_share=1.0, parent=None)],
            tranches=[Tranche("Total debt (XBRL seed)", "OpCo",
                              face=total_debt or 100.0, lien_rank=1, secured=True)],
        )
        source = "XBRL total-debt seed" if total_debt else "manual seed"
    return structure, ebitda, source, citations, subsidiaries, ov


def _structure_from_body(ticker: str, s: dict) -> CapitalStructure:
    return CapitalStructure(
        name=s.get("name") or ticker.upper(),
        entities=[Entity(**e) for e in s.get("entities", [])],
        tranches=[Tranche(**t) for t in s.get("tranches", [])],
        admin_fees=float(s.get("admin_fees", 0.0)),
        admin_pct=float(s.get("admin_pct", 0.0)),
    )


def _ov_cik(ov: dict) -> Optional[str]:
    """CIK from a serialized Overview — it lives under header.cik, not at the top level."""
    return (ov.get("header") or {}).get("cik")


def _cached_overview(ticker: str, years: int) -> dict:
    """Overview from cache ONLY — never triggers a live pipeline run. Returns {} when the
    company hasn't been built yet. Used by the read-only case/crisis screens, which self-fetch
    on page mount and must not launch a ~3-min LLM+EDGAR run just from a page view."""
    ov = load_overview(ticker, years) or load_latest_overview(ticker)
    return ov.model_dump(mode="json") if ov is not None else {}


def _accrual_from_petition(petition_date: str, ov: dict) -> float:
    """accrual_years = (petition − debt_schedule_asof)/365.25, floored at 0. The schedule
    as-of is when accrued interest was last settled on the balance sheet."""
    asof = ov.get("debt_schedule_asof")
    if not asof:
        return 0.0
    petition = dt.date.fromisoformat(petition_date)
    start = dt.date.fromisoformat(str(asof)[:10])
    return max((petition - start).days, 0) / 365.25


def _suggested_mezzanine(ov: dict) -> Optional[dict]:
    """Temporary-equity carrying ($mm) — the pre-seed for the 'mezzanine recast as debt'
    row (Moyer ch. 6: debt-like redemption obligations dressed as equity)."""
    cv = ov.get("mezzanine") or {}
    if not cv.get("value") or float(cv["value"]) <= 0:
        return None
    return {"value": round(float(cv["value"]) / 1e6, 1),
            "formula": "temporary-equity carrying amount",
            "note": "recast as a preferred claim — pays after debt, before common "
                    "(Moyer ch. 6); carrying ≈ liquidation preference + accrued "
                    "dividends and may include redeemable NCI"}


def _suggested_other_claims(ov: dict) -> Optional[dict]:
    """Σ bridge-included OBS items ($mm) — the pre-seed for the 'other unsecured claims'
    dilution row (rejection damages, pension, leases dilute the unsecured pool)."""
    items = [it for it in ov.get("obs_items") or []
             if it.get("include_in_bridge") and (it.get("amount") or {}).get("value")]
    if not items:
        return None
    total = sum(float(it["amount"]["value"]) for it in items) / 1e6
    return {"value": round(total, 1),
            "formula": " + ".join(f"{it.get('category')} ({it.get('label', '')[:40]})"
                                  for it in items),
            "note": "bridge-included OBS extractions — lease/pension/claim amounts that can "
                    "dilute the unsecured pool in chapter 11 (Moyer ch. 12)"}


@app.get("/api/company/{ticker}/recovery/structure")
def recovery_structure(ticker: str, years: int = Query(3, ge=1, le=10)):
    """The editable cap table for the Recovery page — derived, never re-entered."""
    try:
        structure, ebitda, source, citations, subsidiaries, ov = _derive_structure(ticker, years)
    except (TickerNotFoundError, NoFilingsError) as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    available_entities = [{"name": s.get("name"), "jurisdiction": s.get("jurisdiction")}
                          for s in subsidiaries if s.get("name")]
    return JSONResponse(content=jsonable({
        "structure": _structure_dict(structure), "base_ebitda": ebitda, "source": source,
        "citations": citations, "available_entities": available_entities,
        "suggested_other_claims": _suggested_other_claims(ov),
        "suggested_mezzanine": _suggested_mezzanine(ov),
        "asset_snapshot": ov.get("asset_snapshot"),
        # priming pre-seed: the covenant-dollars liens read (suggested_priming inside)
        "liens_headroom": ov.get("liens_headroom"),
    }))


def _liquidation_response(ticker: str, structure: CapitalStructure, ov: dict,
                          accrual_years: float, note: str, body_rates=None,
                          body_admin=None, body_assets=None) -> JSONResponse:
    """Asset-based waterfall payload (Moyer: cash-flow metrics are irrelevant when positive
    EBITDA is unattainable). Degrades with a note when no asset snapshot was extracted."""
    from .fulcrum.liquidation import assets_from_snapshot, liquidate

    assets = body_assets or assets_from_snapshot(ov.get("asset_snapshot"))
    if assets is None:
        return JSONResponse(content=jsonable({
            "mode": "liquidation", "available": False,
            "structure": _structure_dict(structure), "note": note,
            "detail": "no balance-sheet asset snapshot in this cached overview — "
                      "re-run the pipeline (Run live) to extract asset categories"}))
    out = liquidate(assets, structure, rates=body_rates, admin_pct=body_admin,
                    accrual_years=accrual_years)
    out.update({"available": True, "structure": _structure_dict(structure), "note": note,
                "asset_snapshot": ov.get("asset_snapshot")})
    return JSONResponse(content=jsonable(out))


@app.post("/api/company/{ticker}/recovery/simulate")
def recovery_simulate(ticker: str, body: SimulateBody, years: int = Query(3, ge=1, le=10)):
    """Fulcrum Monte Carlo. Cap table comes from the request body if given, otherwise it is
    derived from the capstack overview (cache-first) — no manual re-entry.

    EBITDA ≤ 0 (or mode="liquidation") switches to the asset-based liquidation waterfall
    instead of failing: a going-concern EV simulation is meaningless below zero EBITDA."""
    sim_kwargs = dict(body.sim)
    try:
        ov: dict = {}
        if body.structure is not None:
            structure = _structure_from_body(ticker, body.structure)
            source = "request body"
            if body.petition_date or body.mode == "liquidation" or not sim_kwargs.get("base_ebitda"):
                try:   # cache-first; only needed for petition accrual / liquidation assets
                    ov = json.loads(run_overview(ticker, years).model_dump_json())
                except Exception:
                    ov = {}
        else:
            structure, ebitda, source, _, _, ov = _derive_structure(ticker, years)
            sim_kwargs.setdefault("base_ebitda", ebitda)

        if body.petition_date and "accrual_years" not in body.sim:
            sim_kwargs["accrual_years"] = _accrual_from_petition(body.petition_date, ov)

        base_ebitda = sim_kwargs.get("base_ebitda")
        if body.mode == "liquidation" or base_ebitda is None or base_ebitda <= 0:
            note = ("forced liquidation mode" if body.mode == "liquidation" else
                    "EBITDA ≤ 0 — going-concern EV simulation replaced by asset-based "
                    "liquidation (Moyer ch. 5)")
            return _liquidation_response(ticker, structure, ov,
                                         float(sim_kwargs.get("accrual_years") or 0.0), note)

        cfg = SimConfig(**sim_kwargs)
        # bound the PRODUCT, not just each factor: run_waterfall allocates one (n_draws,) array
        # per tranche (×3 for the base+attack+priming legs), so n_draws×tranches is the real
        # memory driver. 20M cells ≈ 160 MB/array — dwarfs any real (small tranche count) request.
        if cfg.n_draws * len(structure.tranches) > 20_000_000:
            raise ValueError("simulation too large: n_draws × tranches exceeds the cell budget")
        result = fulcrum_analyze(structure, cfg)
        attack_rows = None
        if body.attack:
            from .fulcrum.attacks import apply_attack
            attacked = apply_attack(structure, body.attack, body.attack_target)
            wf = run_waterfall(attacked, result.sim.ev, result.accrual_years)
            amap = {t.name: t for t in attacked.tranches}
            attack_rows = [
                {"tranche": n, "mean_recovery_%":
                    float(100 * (wf[n] / amap[n].claim(result.accrual_years)).mean())
                    if amap[n].claim(result.accrual_years) > 0 else None}
                for n in attacked.priority_order()]
        priming_rows = None
        primed_dict = None
        if body.priming is not None:   # attack-block mirror: same EV draws (Moyer ch. 9)
            from .fulcrum.proforma import prime
            primed = prime(structure, float(body.priming.get("face") or 0.0),
                           body.priming.get("entity"))
            wf = run_waterfall(primed, result.sim.ev, result.accrual_years)
            pmap = {t.name: t for t in primed.tranches}
            priming_rows = [
                {"tranche": n, "mean_recovery_%":
                    float(100 * (wf[n] / pmap[n].claim(result.accrual_years)).mean())
                    if pmap[n].claim(result.accrual_years) > 0 else None}
                for n in primed.priority_order()]
            primed_dict = _structure_dict(primed)
    except (TickerNotFoundError, NoFilingsError) as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except (ValueError, TypeError) as exc:   # engine validators = the input trust boundary
        return JSONResponse(status_code=400, content={"error": str(exc)})

    ev = result.sim.ev
    order = result.structure.priority_order()
    ay = result.accrual_years
    tmap = {t.name: t for t in result.structure.tranches}
    face = {n: tmap[n].face for n in order}
    claim = {n: tmap[n].claim(ay) for n in order}   # recovery % is against the allowed claim

    # Chart payloads, computed server-side so the response stays small (no raw draws).
    pct_grid = np.linspace(0, 100, 51)
    histograms, cdf = {}, {}
    for name in order:
        c = claim[name]
        pct = 100 * result.recoveries[name] / c if c > 0 else np.zeros_like(result.recoveries[name])
        counts, edges = np.histogram(pct, bins=20, range=(0.0, 100.0000001))
        histograms[name] = {"edges": edges.round(1).tolist(), "counts": counts.tolist()}
        cdf[name] = (pct[:, None] <= pct_grid[None, :]).mean(axis=0).round(4).tolist()
    ev_counts, ev_edges = np.histogram(ev, bins=40)
    med_wf = run_waterfall(result.structure, np.array([float(np.median(ev))]), accrual_years=ay)
    waterfall_at_median = [
        {"tranche": n, "face": face[n], "claim": claim[n], "recovery": float(med_wf[n][0]),
         "recovery_pct": 100 * float(med_wf[n][0]) / claim[n] if claim[n] > 0 else None}
        for n in order
    ]

    # §506 postpetition-interest headroom per secured tranche with a collateral value
    headroom_506 = {
        t.name: round(max(t.collateral_value - t.claim(ay), 0.0), 1)
        for t in structure.tranches if t.secured and t.collateral_value is not None}

    return JSONResponse(content=jsonable({
        "source": source,
        "structure": _structure_dict(structure),
        "sim": sim_kwargs,
        "ev": {"mean": float(ev.mean()), "median": float(np.median(ev)),
               "p10": float(np.percentile(ev, 10)), "p90": float(np.percentile(ev, 90)),
               "histogram": {"edges": ev_edges.round(1).tolist(), "counts": ev_counts.tolist()}},
        "total_face": structure.total_face(),
        "total_claim": float(sum(claim.values())),
        "accrual_years": ay,
        "fulcrum": result.fulcrum,
        "tranches": _native(result.table().to_dict("records")),
        "cdf": {"grid": pct_grid.tolist(), "series": cdf},
        "histograms": histograms,
        "waterfall_at_median": waterfall_at_median,
        "headroom_506": headroom_506,
        "attack": body.attack,
        "attack_tranches": attack_rows,
        "priming_tranches": priming_rows,
        "primed_structure": primed_dict,
    }))


@app.post("/api/company/{ticker}/recovery/explore")
def recovery_explore(ticker: str, body: SimulateBody, years: int = Query(3, ge=1, le=10)):
    """Deterministic EV explorer: per-tranche recovery curves over an EV grid, breakpoints,
    coverage-vs-multiple, and the 'market has not repriced' flag. Works at negative EBITDA."""
    from .capstack.quotes import match_quotes
    from .fulcrum.explore import explore
    from .hazard.trace import get_issuer_bonds

    try:
        ov: dict = {}
        ebitda = body.sim.get("base_ebitda")
        if body.structure is not None:
            structure = _structure_from_body(ticker, body.structure)
            try:
                ov = json.loads(run_overview(ticker, years).model_dump_json())
            except Exception:
                ov = {}
        else:
            structure, derived_ebitda, _, _, _, ov = _derive_structure(ticker, years)
            ebitda = ebitda if ebitda is not None else derived_ebitda
        accrual = float(body.sim.get("accrual_years") or 0.0)
        if body.petition_date and "accrual_years" not in body.sim:
            accrual = _accrual_from_petition(body.petition_date, ov)
        matches, _ = match_quotes(ov.get("debt_schedule") or [],
                                  get_issuer_bonds(ticker).get("bonds") or [])
        prices = [q["last_price"] for q in matches.values() if q.get("last_price") is not None]
        out = explore(structure, ebitda, accrual, quotes=prices or None)
    except (TickerNotFoundError, NoFilingsError) as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except (ValueError, TypeError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    out["structure"] = _structure_dict(structure)
    return JSONResponse(content=jsonable(out))


class ExchangeBody(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    sim: dict = {}                       # base_ebitda, accrual_years
    structure: Optional[dict] = None     # explicit (edited) cap table; else derived
    petition_date: Optional[str] = None
    target: str                          # tranche name to exchange
    ratio_pct: float                     # new face per 100 old face tendered
    participation_pct: float = 90.0      # the user's own scenario row
    seniority: str = "priming"           # priming | second_lien | unsecured
    coupon_pct: float = 0.0              # new-paper coupon (%)
    cash_per_100: float = 0.0            # cash consideration (face-valued, no EV depletion)
    equity_pct_at_full: float = 0.0      # e — equity to tendering holders at p=100
    min_tender_pct: Optional[float] = None
    exit_consent: bool = False           # stub contractually subordinated to the new paper


_EXCHANGE_GRID_P = (0.0, 25.0, 50.0, 75.0, 90.0, 100.0)


@app.post("/api/company/{ticker}/recovery/exchange")
def recovery_exchange(ticker: str, body: ExchangeBody, years: int = Query(3, ge=1, le=10)):
    """Exchange-offer analyzer (Moyer ch. 11): a calculator over typed offer terms —
    holdout-vs-tender payoff curves per participation level, direct run_waterfall per
    scenario over the BASE structure's EV grid (clones recovery_explore's shell)."""
    from .capstack.quotes import match_quotes
    from .edgar.facts import derived_value
    from .fulcrum.explore import _cross
    from .fulcrum.proforma import exchange_scenario
    from .hazard.trace import get_issuer_bonds

    try:
        ov: dict = {}
        ebitda = body.sim.get("base_ebitda")
        if body.structure is not None:
            structure = _structure_from_body(ticker, body.structure)
            try:
                ov = json.loads(run_overview(ticker, years).model_dump_json())
            except Exception:
                ov = {}
        else:
            structure, derived_ebitda, _, _, _, ov = _derive_structure(ticker, years)
            ebitda = ebitda if ebitda is not None else derived_ebitda
        accrual = float(body.sim.get("accrual_years") or 0.0)
        if body.petition_date and "accrual_years" not in body.sim:
            accrual = _accrual_from_petition(body.petition_date, ov)

        tmap = {t.name: t for t in structure.tranches}
        if body.target not in tmap:
            raise ValueError(f"unknown exchange target '{body.target}'")
        F = tmap[body.target].face
        total_claim = sum(t.claim(accrual) for t in structure.tranches)
        if total_claim <= 0:
            return JSONResponse(content={"available": False,
                                         "note": "no claims in the structure"})
        grid = np.linspace(0.0, 1.5 * total_claim, 241)
        base_wf = run_waterfall(structure, grid, accrual_years=accrual)
        c = tmap[body.target].claim(accrual)
        base_pct = (np.round(100 * base_wf[body.target] / c, 2) if c > 0
                    else np.zeros_like(grid))

        user_p = round(float(body.participation_pct), 4)
        scenarios = []
        for p in sorted(set(_EXCHANGE_GRID_P) | {user_p}):
            sc = exchange_scenario(
                structure, body.target, grid, ratio_pct=body.ratio_pct,
                participation_pct=p, seniority=body.seniority,
                coupon=body.coupon_pct / 100.0, exit_consent=body.exit_consent,
                cash_per_100=body.cash_per_100,
                equity_pct_at_full=body.equity_pct_at_full, accrual_years=accrual)
            tender, holdout = sc["tender"], sc["holdout"]
            face2 = sc["structure"].total_face()
            # EV where holding out overtakes tendering (piecewise-linear curves).
            # Skip the leading plateau where both payoffs are 0 (EV at/below the
            # admin-fee floor): diff >= 0 holds trivially there, so _cross over the
            # full grid reported a spurious crossover at ~0.
            crossover = None
            if tender is not None and holdout is not None:
                alive = (tender > 0) | (holdout > 0)
                if alive.any():
                    j = int(np.argmax(alive))
                    crossover = _cross(grid[j:], (holdout - tender)[j:], 0.0)
            scenarios.append({
                "participation_pct": p,
                "proforma_face": round(face2, 1),
                "proforma_leverage": (round(face2 / ebitda, 2)
                                      if ebitda is not None and ebitda > 0 else None),
                "stub_pct": (np.round(sc["stub_pct"], 2).tolist()
                             if sc["stub_pct"] is not None else None),
                "new_pct": (np.round(sc["new_pct"], 2).tolist()
                            if sc["new_pct"] is not None else None),
                "equity_mm": np.round(sc["equity"], 1).tolist(),
                "tender": np.round(tender, 2).tolist() if tender is not None else None,
                "holdout": (np.round(holdout, 2).tolist()
                            if holdout is not None else None),
                "crossover_ev": crossover,
                "fails": bool(body.min_tender_pct is not None
                              and p < body.min_tender_pct),
            })

        # reference EV for the scalar chips: 6.0x EBITDA when positive, else midpoint
        ref_idx = 120
        if ebitda is not None and ebitda > 0:
            ref_idx = int(np.clip(np.searchsorted(grid, 6.0 * ebitda),
                                  0, len(grid) - 1))

        # target quote premium (unquoted-degrading)
        matches, _ = match_quotes(ov.get("debt_schedule") or [],
                                  get_issuer_bonds(ticker).get("bonds") or [])
        tgt_key = body.target.rstrip(" *")
        price = next((q.get("last_price") for n, q in matches.items()
                      if n[:80] == tgt_key), None)
        user_sc = next(s for s in scenarios if s["participation_pct"] == user_p)
        premium = None
        if price is not None and user_sc["tender"] is not None:
            pkg = user_sc["tender"][ref_idx]
            premium = {"target_quote": price, "package_at_ref": pkg,
                       "premium_per_100": round(pkg - price, 2),
                       "ref_ev_mm": round(float(grid[ref_idx]), 1)}

        # holdout runway (ch. 11): can the estate carry a holdout fight?
        cash = burn = None
        for row in reversed(ov.get("forensic_table") or []):
            if cash is None and (row.get("cash") or {}).get("value") is not None:
                cash = float(row["cash"]["value"]) / 1e6
            fcv = (row.get("free_cash_flow") or {}).get("value")
            if burn is None and fcv is not None:
                burn = max(-float(fcv), 0.0) / 1e6
            if cash is not None and burn is not None:
                break
        runway = None
        if cash is not None and burn is not None and burn > 0:
            spend = body.cash_per_100 / 100.0 * user_p / 100.0 * F
            rq = (cash - spend) / (burn / 4.0)
            runway = derived_value(
                round(rq, 1),
                f"(cash ${cash:,.0f}M − tender cash spend ${spend:,.0f}M) ÷ quarterly "
                f"burn ${burn / 4.0:,.0f}M — quarters the company can carry a holdout "
                "fight (the book's cash-depletion point)",
                f"{rq:,.1f} qtrs").model_dump()
    except (TickerNotFoundError, NoFilingsError) as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except (ValueError, TypeError) as exc:   # engine validators = the 400 boundary
        return JSONResponse(status_code=400, content={"error": str(exc)})

    return JSONResponse(content=jsonable({
        "available": True,
        "ev_grid": np.round(grid, 1).tolist(),
        "multiple_grid": (np.round(grid / ebitda, 3).tolist()
                          if ebitda is not None and ebitda > 0 else None),
        "ebitda": ebitda,
        "accrual_years": accrual,
        "target": body.target,
        "target_face": round(F, 1),
        "base_pct": base_pct.tolist(),
        "scenarios": scenarios,
        "min_tender_pct": body.min_tender_pct,
        "quote_premium": premium,
        "holdout_runway_quarters": runway,
        "terms": {"ratio_pct": body.ratio_pct, "seniority": body.seniority,
                  "coupon_pct": body.coupon_pct, "cash_per_100": body.cash_per_100,
                  "equity_pct_at_full": body.equity_pct_at_full,
                  "exit_consent": body.exit_consent},
        "note": "maturity-based coercion is not modeled — seniority expresses through "
                "lien rank and exit-consent subordination; cash consideration is "
                "valued at face and does not deplete waterfall EV (going-concern "
                "convention)",
    }))


class PlanBody(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    sim: dict = {}                       # base_ebitda, accrual_years
    structure: Optional[dict] = None     # explicit (edited) cap table; else derived
    petition_date: Optional[str] = None
    reorg_ev: float                      # plan enterprise value ($mm)
    reorg_debt: float = 0.0              # post-reorg debt ($mm) — reorg equity = EV − debt
    reorg_shares: Optional[float] = None  # post-reorg share count (millions) — for rights math
    duration_years: Optional[float] = None
    plan: list[dict] = []                # per-class PlanConsideration dicts


@app.post("/api/company/{ticker}/recovery/plan")
def recovery_plan(ticker: str, body: PlanBody, years: int = Query(3, ge=1, le=10)):
    """Plan-of-reorganization recovery & ROI (Moyer ch. 12-13): value the typed package
    per class → recovery % of allowed claim → annualized ROI vs the market entry price,
    with a per-class delta vs the absolute-priority recovery at the same reorg EV. The
    plan is exogenous (never re-run through the waterfall). Clones recovery_exchange's shell."""
    from .capstack.quotes import match_quotes
    from .fulcrum.plan import PlanConsideration, evaluate_plan
    from .hazard.trace import get_issuer_bonds

    try:
        ov: dict = {}
        if body.structure is not None:
            structure = _structure_from_body(ticker, body.structure)
            try:
                ov = json.loads(run_overview(ticker, years).model_dump_json())
            except Exception:
                ov = {}
        else:
            structure, _, _, _, _, ov = _derive_structure(ticker, years)
        accrual = float(body.sim.get("accrual_years") or 0.0)
        if body.petition_date and "accrual_years" not in body.sim:
            accrual = _accrual_from_petition(body.petition_date, ov)

        # entry price per tranche (per 100 of face), unquoted-degrading — prefix-match
        # the drop-file quotes to tranche names (same n[:80] convention as the exchange shell)
        matches, _ = match_quotes(ov.get("debt_schedule") or [],
                                  get_issuer_bonds(ticker).get("bonds") or [])
        tnames = [t.name for t in structure.tranches]
        entry: dict = {}
        for n, q in matches.items():
            price = q.get("last_price")
            if price is None:
                continue
            for tn in tnames:
                if tn.rstrip(" *")[:80] == n[:80]:
                    entry[tn] = price
                    break

        if len(body.plan) > 500:   # request-derived list length; a plan can't exceed the tranches
            raise ValueError("plan too large (max 500 classes)")
        cons = [PlanConsideration(
                    tranche=c.get("tranche"), cash=float(c.get("cash") or 0.0),
                    new_debt_face=float(c.get("new_debt_face") or 0.0),
                    new_debt_haircut=(None if c.get("new_debt_haircut") in (None, "")
                                      else float(c["new_debt_haircut"])),
                    new_equity_pct=float(c.get("new_equity_pct") or 0.0),
                    warrant_value=float(c.get("warrant_value") or 0.0),
                    rights_shares=float(c.get("rights_shares") or 0.0),
                    rights_strike=float(c.get("rights_strike") or 0.0))
                for c in body.plan]
        out = evaluate_plan(structure, cons, reorg_ev=body.reorg_ev, reorg_debt=body.reorg_debt,
                            reorg_shares=body.reorg_shares, accrual_years=accrual,
                            entry_prices=entry, duration_years=body.duration_years)
    except (TickerNotFoundError, NoFilingsError) as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except (ValueError, TypeError) as exc:   # engine validators = the 400 boundary
        return JSONResponse(status_code=400, content={"error": str(exc)})

    out["structure"] = _structure_dict(structure)
    out["accrual_years"] = accrual
    return JSONResponse(content=jsonable(out))


@app.get("/api/company/{ticker}/recovery/case")
def recovery_case(ticker: str, years: int = Query(3, ge=1, le=10)):
    """Chapter-11 case card inputs (Moyer ch. 12): the cited petition date (the 8-K Item
    1.03 filing date, a proxy for the docket petition date) and a weak free-fall hint
    (revolver drawn ≈ $0 undrawn). The statutory clocks/benchmark are computed frontend."""
    from .capstack.eightk import petition_filing

    ov = _cached_overview(ticker, years)   # cache-only: a page-mount fetch must not launch a live run

    petition = None
    petition_error = False   # distinguish an EDGAR fetch failure from a genuine "no 1.03"
    cik = _ov_cik(ov)
    if cik:
        try:
            pf = petition_filing(cik)
        except Exception:
            pf, petition_error = None, True
        if pf and pf.get("date"):
            petition = {"value": pf["date"], "display": pf["date"], "derived": False,
                        "citation": {"form_type": "8-K",
                                     "section": "Item 1.03 — Bankruptcy or Receivership",
                                     "filing_date": pf["date"], "accession_no": pf.get("accession"),
                                     "source_url": pf.get("source_url"),
                                     "quote": "petition date proxied by the 8-K Item 1.03 filing date"}}

    liq = ov.get("liquidity") or {}
    undrawn = liq.get("undrawn_committed")
    note = ("petition date = the 8-K Item 1.03 filing date (proxy for the docket petition date); "
            "case type is the analyst's call. A pre-filing revolver drawdown weakly hints free-fall "
            "but isn't reliably tagged, so it is surfaced (undrawn figure) rather than auto-inferred.")
    if not ov:
        note = "open this company's Overview tab first to populate its liquidity signals. " + note
    return JSONResponse(content=jsonable({
        "petition_date": petition,
        "petition_error": petition_error,   # True = EDGAR lookup failed (NOT "no bankruptcy")
        "revolver_undrawn": undrawn,        # cited undrawn-committed figure; the analyst judges free-fall
        "note": note,
    }))


DOCKET_SUBTYPES = {   # Moyer ch.12 milestones -> severity 1-5
    "petition": 5, "first_day": 3, "dip": 4, "363_sale": 4,
    "disclosure_statement": 3, "plan": 4, "confirmation": 5,
    "effective": 4, "exclusivity_extension": 2}


class DocketBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subtype: str = Field(pattern=r"^[a-z0-9_]{1,32}$")
    occurred_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    title: str = Field(min_length=1, max_length=300)
    docket_no: Optional[str] = Field(None, max_length=32, pattern=r"^[A-Za-z0-9.\-]*$")
    source_url: Optional[str] = Field(None, max_length=500)


def _docket_event(cik, b: DocketBody):   # pure -> unit-testable, mirrors events_from_sd_rows
    from .events.types import Event

    # cik in synthetic accession because make_dedupe_key omits cik (models_events.py:~146)
    acc = f"manual:docket:{cik}:{b.occurred_at}:{b.subtype}" + (f":{b.docket_no}" if b.docket_no else "")
    return Event(cik=cik, event_type="docket", subtype=b.subtype,
                 severity=DOCKET_SUBTYPES[b.subtype], confidence=1.0,
                 occurred_at=b.occurred_at, source="manual", source_form="docket",
                 accession_no=acc, source_url=b.source_url, title=b.title, payload={})


@app.post("/api/company/{ticker}/recovery/docket")
def add_docket_event(ticker: str, body: DocketBody):
    """Layer A (manual) docket ingest (Moyer ch. 12 milestones) — a direct event-store
    write, not a registry-detector route (source='manual' already anticipated in
    models_events.py:~78). Re-POST of the same (cik, date, subtype[, docket_no]) is
    idempotent (inserted=0) — that IS the edit path. Rendering is free: /api/events and
    /api/company/{ticker}/timeline already select+emit these rows (TimelinePage.jsx:~50,
    EventsPage.jsx:~137 render the badge; TimelinePage.jsx:~52-53, EventsPage.jsx:~142-143
    render source_url as a raw <a href>)."""
    from .events.store import insert_events

    if body.subtype not in DOCKET_SUBTYPES:
        return JSONResponse(status_code=400, content={"error": f"unknown subtype {body.subtype!r}"})
    if body.source_url and not body.source_url.startswith(("http://", "https://")):
        return JSONResponse(status_code=400, content={"error": "source_url must be http(s)"})  # href XSS guard
    try:
        dt.date.fromisoformat(body.occurred_at)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "occurred_at is not a real calendar date"})
    with session_scope() as session:
        cik = _resolve_cik(session, ticker)
        if cik is None:
            return JSONResponse(status_code=404, content={"error": "ticker_not_found"})
        ev = _docket_event(cik, body)
        # ponytail: manual rows must be filtered source!='manual' at backtest time — not enforced here
        n = insert_events(session, [ev], detected_at=dt.datetime.utcnow())
    return JSONResponse(content={"inserted": n, "dedupe_key": ev.dedupe_key,
                                 "note": None if n else "already recorded (idempotent)"})


@app.delete("/api/events/{event_id}")
def delete_docket_event(event_id: int):
    """Undo for the manual docket surface only — never deletes a detector-sourced row."""
    with session_scope() as session:
        n = (session.query(models_events.Event)
             .filter(models_events.Event.id == event_id,
                     models_events.Event.source == "manual").delete())
        return JSONResponse(content={"deleted": n})


@app.get("/api/company/{ticker}/recovery/crisis")
def recovery_crisis(ticker: str, years: int = Query(3, ge=1, le=10)):
    """Crisis-of-confidence four-factor screen (Moyer ch. 8): a restatement/fraud 8-K
    trigger (Items 4.01/4.02/5.02) assessed against the four liquidity factors — cash,
    revolver reliance, acceleration/MAC language, and the immediate cash need. On-demand:
    an 8-K fetch must not gate every overview build or screener row."""
    from sqlalchemy import text as sql

    from .capstack.eightk import crisis_screen, crisis_triggers
    from .core.db import FTS_AVAILABLE

    ov = _cached_overview(ticker, years)   # cache-only: a page-mount fetch must not launch a live run

    triggers, trigger_error = [], False
    cik = _ov_cik(ov)
    if cik:
        try:
            triggers = crisis_triggers(cik)
        except Exception:
            trigger_error = True   # EDGAR lookup failed — NOT "no trigger"

    # factor 3: best-effort cross-default / MAC scan over the indexed covenant/notes corpus
    accel = {"clauses_found": 0, "sample": None, "available": FTS_AVAILABLE}
    if FTS_AVAILABLE:
        match = ('"cross default" OR "cross-default" OR "cross acceleration" OR '
                 '"material adverse change" OR "material adverse effect"')
        try:
            with session_scope() as session:
                rows = session.execute(sql(
                    "SELECT snippet(search, 0, '', '', ' … ', 20) FROM search "
                    "WHERE search MATCH :q AND ticker = :t "
                    "AND source_kind IN ('covenant', 'notes') "  # exclude MD&A/OBS boilerplate
                    "ORDER BY bm25(search) LIMIT 5"),
                    {"q": match, "t": ticker.strip().upper()}).all()
            accel = {"clauses_found": len(rows), "sample": rows[0][0] if rows else None,
                     "available": True}
        except Exception:
            accel = {"clauses_found": 0, "sample": None, "available": False}

    screen = crisis_screen(triggers, ov.get("liquidity"), ov.get("liquidity_events"), accel)
    screen["trigger_error"] = trigger_error
    return JSONResponse(content=jsonable(screen))


class Tax382Body(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    nol: Optional[float] = None          # $mm override; else the extracted gross NOL is used
    equity_fmv: float                    # $mm — bankruptcy §382(l)(6): post-reorg equity (plan EV − debt)
    rate: float = 0.045                  # §382 long-term tax-exempt rate (IRS, monthly) — user input
    tax_rate: float = 0.21               # marginal tax rate
    horizon_years: int = Field(20, ge=0, le=100)   # NOL usage horizon; bounded (loop bound in tax_asset_pv)
    discount_rate: float = 0.12          # PV discount rate


@app.post("/api/company/{ticker}/recovery/tax382")
def recovery_tax382(ticker: str, body: Tax382Body, years: int = Query(3, ge=1, le=10)):
    """NOL / §382 tax-asset read (Moyer ch. 11). Uses the extracted gross NOL (cited) unless
    the analyst overrides it, and the user-supplied §382 rate / equity FMV / tax rate to
    compute the annual limit, usable vs stranded NOL, and the PV of the tax shield."""
    from .capstack.tax382 import analyze_tax_asset
    from .edgar.facts import derived_value

    try:
        ov = json.loads(run_overview(ticker, years).model_dump_json())
    except (TickerNotFoundError, NoFilingsError) as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})

    extracted = ov.get("nol_carryforward")   # CitedValue dict (raw USD) or None
    if body.nol is not None:
        nol_mm = float(body.nol)
    elif extracted and extracted.get("value") is not None:
        nol_mm = float(extracted["value"]) / 1e6   # extracted is raw USD; card works in $mm
    else:
        return JSONResponse(content=jsonable({
            "available": False, "nol_extracted": extracted,
            "note": "no gross NOL carryforward tagged (OperatingLossCarryforwards is often "
                    "tagged only dimensioned-by-jurisdiction in the tax footnote, and is then "
                    "skipped) — enter the NOL manually to run the §382 read"}))

    r = analyze_tax_asset(nol_mm, body.equity_fmv, body.rate, body.tax_rate,
                          body.horizon_years, body.discount_rate)

    def dv(v, formula, display):
        return derived_value(v, formula, display).model_dump()

    return JSONResponse(content=jsonable({
        "available": True,
        "nol_extracted": extracted,
        "nol_used_mm": round(nol_mm, 1),
        "annual_limit": dv(r["annual_limit"],
                           f"§382 rate {body.rate:.3%} × equity FMV ${body.equity_fmv:,.0f}M",
                           f"${r['annual_limit']:,.1f}M/yr"),
        "usable_nol": dv(r["usable_nol"],
                         f"min(NOL ${nol_mm:,.0f}M, annual limit ${r['annual_limit']:,.1f}M × "
                         f"{body.horizon_years}y)", f"${r['usable_nol']:,.1f}M"),
        "stranded_nol": dv(r["stranded_nol"],
                           f"NOL ${nol_mm:,.0f}M − usable ${r['usable_nol']:,.1f}M",
                           f"${r['stranded_nol']:,.1f}M"),
        "undiscounted_shield": dv(r["undiscounted_shield"],
                                  f"usable ${r['usable_nol']:,.1f}M × tax rate {body.tax_rate:.1%}",
                                  f"${r['undiscounted_shield']:,.1f}M"),
        "tax_asset_pv": dv(r["tax_asset_pv"],
                           f"PV of usable NOL × tax rate {body.tax_rate:.1%} over "
                           f"{body.horizon_years}y at {body.discount_rate:.1%}",
                           f"${r['tax_asset_pv']:,.1f}M"),
        "assumptions": {"rate": body.rate, "tax_rate": body.tax_rate,
                        "horizon_years": body.horizon_years, "discount_rate": body.discount_rate,
                        "equity_fmv": body.equity_fmv, "nol_override": body.nol is not None},
        "note": "bankruptcy §382(l)(6): equity FMV = post-reorg (plan EV − post-reorg debt); the "
                "§382 rate is the IRS long-term tax-exempt rate (a user input, ~monthly). "
                "Post-2017 NOLs carry forward indefinitely — raise the horizon to model that. "
                "The 50% 'old-and-cold' exception (§382(l)(5)) needs 13D/13G history not sourced here.",
    }))


class LiquidationBody(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    structure: Optional[dict] = None
    rates: Optional[dict] = None        # {category: advance rate 0..1}; default orderly preset
    admin_pct: Optional[float] = None   # default 7% (ch11 orderly)
    accrual_years: float = 0.0
    assets: Optional[dict] = None       # {category: book $mm} override (manual entry)


@app.post("/api/company/{ticker}/recovery/liquidation")
def recovery_liquidation(ticker: str, body: LiquidationBody, years: int = Query(3, ge=1, le=10)):
    """Asset-based liquidation waterfall with editable advance rates and the
    ch11-orderly vs ch7-fire-sale comparison."""
    try:
        if body.structure is not None:
            structure = _structure_from_body(ticker, body.structure)
            ov = {}
            if body.assets is None:
                ov = json.loads(run_overview(ticker, years).model_dump_json())
        else:
            structure, _, _, _, _, ov = _derive_structure(ticker, years)
        return _liquidation_response(ticker, structure, ov, body.accrual_years,
                                     "liquidation analysis", body.rates, body.admin_pct,
                                     body.assets)
    except (TickerNotFoundError, NoFilingsError) as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except (ValueError, TypeError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


# ---- scenarios: save / list / delete (compare happens client-side) -----------------


class ScenarioBody(BaseModel):
    name: str
    sim: dict
    structure: dict
    results: Optional[dict] = None      # summary stats at save time (fulcrum, ev, tranches)


@app.get("/api/company/{ticker}/scenarios")
def list_scenarios(ticker: str):
    with session_scope() as session:
        rows = (session.query(models.Scenario)
                .filter(models.Scenario.ticker == ticker.strip().upper())
                .order_by(models.Scenario.created_at.desc()).all())
        return JSONResponse(content=jsonable([
            {"id": r.id, "name": r.name, "sim": r.sim, "structure": r.structure,
             "results": r.results, "created_at": r.created_at} for r in rows
        ]))


@app.post("/api/company/{ticker}/scenarios")
def save_scenario(ticker: str, body: ScenarioBody):
    with session_scope() as session:
        row = models.Scenario(ticker=ticker.strip().upper(), name=body.name.strip()[:64],
                              sim=body.sim, structure=body.structure, results=body.results)
        session.add(row)
        session.flush()
        return JSONResponse(content={"id": row.id, "name": row.name})


@app.delete("/api/scenarios/{scenario_id}")
def delete_scenario(scenario_id: int):
    with session_scope() as session:
        n = (session.query(models.Scenario)
             .filter(models.Scenario.id == scenario_id).delete())
        return JSONResponse(content={"deleted": n})


@app.get("/api/overview/stream")
async def overview_stream(
    ticker: str = Query(..., min_length=1, max_length=12, pattern=r"^[A-Za-z0-9.\-]+$"),
    years: int = Query(3, ge=1, le=10),
    live: bool = Query(False),
):
    """Stream progress events as SSE, then a final `overview` (or `error`) event."""
    event_q: "queue.Queue[dict]" = queue.Queue()

    def sink(evt: ProgressEvent) -> None:
        event_q.put({"event": "progress", "data": evt.to_dict()})

    def worker() -> None:
        log = ProgressLog(sink=sink)
        try:
            ov = run_overview(ticker, years, progress=log, live=live)
            event_q.put({"event": "overview", "data": json.loads(ov.model_dump_json())})
        except TickerNotFoundError as exc:
            event_q.put({"event": "error", "data": {"error": "ticker_not_found", "detail": str(exc)}})
        except NoFilingsError as exc:
            event_q.put({"event": "error", "data": {"error": "no_filings", "detail": str(exc)}})
        except Exception as exc:
            event_q.put({"event": "error", "data": {"error": "pipeline_error", "detail": str(exc)}})
        finally:
            event_q.put({"event": "__done__", "data": {}})

    threading.Thread(target=worker, daemon=True).start()

    async def event_generator():
        loop = asyncio.get_event_loop()
        while True:
            item = await loop.run_in_executor(None, event_q.get)
            if item["event"] == "__done__":
                break
            yield {"event": item["event"], "data": json.dumps(item["data"])}

    return EventSourceResponse(event_generator())


@app.get("/api/hazard/stream")
async def hazard_stream(
    ticker: str = Query(..., min_length=1, max_length=12, pattern=r"^[A-Za-z0-9.\-]+$"),
    years: int = Query(10, ge=1, le=10),
    live: bool = Query(False),
):
    """Stream hazard-pipeline progress as SSE, then a final `hazard` (or `error`) event.
    A same-day cache hit emits a single pct=100 event and resolves immediately."""
    event_q: "queue.Queue[dict]" = queue.Queue()

    def sink(evt: ProgressEvent) -> None:
        event_q.put({"event": "progress", "data": evt.to_dict()})

    def worker() -> None:
        log = ProgressLog(sink=sink)
        try:
            data = _hazard_section(ticker, years, live, progress=log)
            event_q.put({"event": "hazard", "data": data})
            try:   # fill the screening index's risk columns; never fail the stream
                with session_scope() as session:
                    update_snapshot_risk(session, ticker.strip().upper(), data)
            except Exception:
                pass
        except TickerNotFoundError as exc:
            event_q.put({"event": "error", "data": {"error": "ticker_not_found", "detail": str(exc)}})
        except Exception as exc:
            event_q.put({"event": "error", "data": {"error": "pipeline_error", "detail": str(exc)}})
        finally:
            event_q.put({"event": "__done__", "data": {}})

    threading.Thread(target=worker, daemon=True).start()

    async def event_generator():
        loop = asyncio.get_event_loop()
        while True:
            item = await loop.run_in_executor(None, event_q.get)
            if item["event"] == "__done__":
                break
            yield {"event": item["event"], "data": json.dumps(item["data"])}

    return EventSourceResponse(event_generator())
