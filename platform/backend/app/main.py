"""FastAPI app: the unified distressed-credit platform API (one process, three modules).

Key endpoints
  GET  /api/health                      — liveness + module configuration
  GET  /api/screen                      — the screening index (front door of the funnel)
  GET  /api/overview?ticker=&years=     — capstack Overview (JSON, + last recovery summary)
  GET  /api/overview/stream?ticker=...  — SSE: progress events, then the final overview
  GET  /api/hazard/stream?ticker=...    — SSE: progress events, then the hazard payload
  POST /api/company/{ticker}/recovery/simulate — fulcrum Monte Carlo on the extracted cap table
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import queue
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from typing import NamedTuple, Optional

import numpy as np
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

import datetime as dt

from . import models
from . import models_events
from .core.cache import load_latest_overview, load_overview, safe_ticker, single_flight
from .core.config import CACHE_DIR, get_settings, set_llm_runtime_enabled
from .core.db import init_db, session_scope
from .edgar.client import NoFilingsError, TickerNotFoundError
from .fulcrum import CapitalStructure, Entity, SimConfig, Tranche
from .fulcrum import analyze as fulcrum_analyze
from .fulcrum.adapter import ltm_ebitda, overview_to_structure
from .fulcrum.waterfall import run_waterfall
from .hazard.pipeline import analyze as hazard_analyze
from .pipeline import run_overview
from .store import distress_badge, update_snapshot_recovery, update_snapshot_risk
from .events.heartbeat import worker_status, zero_ingest_alarm
from .events.store import DOCKET_SUBTYPES, docket_event
from .core.progress import ProgressEvent, ProgressLog

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # a no-op when the server already configured the root logger
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    yield


app = FastAPI(
    title="Distressed Credit API",
    version="0.1.0",
    lifespan=lifespan,
)


# Every route reports an unknown ticker, or one with nothing filed, the same way.
@app.exception_handler(TickerNotFoundError)
async def _ticker_not_found(request, exc):
    return JSONResponse(status_code=404, content={"error": "ticker_not_found", "detail": str(exc)})


@app.exception_handler(NoFilingsError)
async def _no_filings(request, exc):
    return JSONResponse(status_code=404, content={"error": "no_filings", "detail": str(exc)})


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
    if cl is None and request.method in ("POST", "PUT", "PATCH"):
        # a chunked body has no length to check, and request.json() would buffer all of it
        return JSONResponse(status_code=411, content={"error": "Content-Length required"})
    if cl is not None and cl.isdigit() and int(cl) > _MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"error": "request body too large (max 5 MB)"})
    return await call_next(request)


@app.middleware("http")
async def _bearer_auth(request, call_next):
    """When PLATFORM_API_TOKEN is set, every /api/* request needs
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


@app.get("/api/health")
def health() -> dict:
    s = get_settings()
    try:   # worker gauges are read once and must never take health down (uptime probe)
        w = worker_status()
        alarm = zero_ingest_alarm(w, dt.datetime.now(dt.UTC).replace(tzinfo=None))
    except Exception:
        log.exception("worker gauges unreadable")
        w, alarm = {"alive": False}, False
    return {
        "status": "ok",
        "llm_enabled": s.llm_enabled,
        "llm_key_set": s.llm_key_set,   # tells "toggled off" (POST /api/settings/llm) from "no key"
        "auth_required": bool(s.platform_api_token.strip()),   # the SPA shows the token box
        "sec_user_agent_set": bool(s.sec_user_agent and "example.com" not in s.sec_user_agent),
        "worker": w,                       # raw worker gauges
        "zero_ingest_alarm": alarm,        # filing-hours-aware alarm from those gauges
    }


class LlmToggleBody(BaseModel):
    enabled: bool


@app.post("/api/settings/llm")
def set_llm(body: LlmToggleBody) -> dict:
    set_llm_runtime_enabled(body.enabled)
    s = get_settings()
    return {"llm_enabled": s.llm_enabled, "llm_key_set": s.llm_key_set}


def _handle_pipeline_errors(fn):
    """A pipeline failure answers 500 with its reason and the app stays up; not-found
    errors pass through to the 404 handlers."""
    try:
        return fn()
    except (TickerNotFoundError, NoFilingsError):
        raise
    except Exception as exc:
        log.exception("pipeline request failed")
        return JSONResponse(
            status_code=500, content={"error": "pipeline_error", "detail": str(exc)}
        )


def _with_recovery_summary(ticker: str, content: dict) -> dict:
    """Attach the last simulate run's summary (the strip's RECOVER cell) to a serialized
    overview. A failed read leaves it None and never fails the overview."""
    content["recovery_summary"] = None
    try:
        with session_scope() as session:
            snap = session.get(models.Snapshot, ticker.strip().upper())
            content["recovery_summary"] = snap.recovery_summary if snap else None
    except Exception:
        log.exception("recovery summary unreadable for %s", ticker)
    return content


@app.get("/api/overview")
def overview(
    ticker: str = Query(..., min_length=1, max_length=12, pattern=r"^[A-Za-z0-9.\-]+$"),
    years: int = Query(3, ge=1, le=10),
    live: bool = Query(False),
):
    def _run():
        ov = json.loads(run_overview(ticker, years, live=live).model_dump_json())
        return JSONResponse(content=_with_recovery_summary(ticker, ov))

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


def _newest_hazard_cache(safe_t: str) -> Optional[dict]:
    """The newest readable hazard payload for the ticker (any window, any day), marked with
    "stale_as_of": the day it was computed. None when there is none."""
    for f in sorted((CACHE_DIR / "hazard").glob(f"{safe_t}_*y.json"),
                    key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            blob = json.loads(f.read_text(encoding="utf-8"))
            return {**blob["data"], "stale_as_of": blob["as_of"]}
        except Exception:
            log.exception("hazard cache unreadable: %s", f)
    return None


def _hazard_section(ticker: str, years: int, live: bool, progress: ProgressLog | None = None) -> dict:
    """Same-day disk cache around the hazard pipeline. Market data moves daily and EDGAR on
    filings, so a day-fresh payload serves page reloads instantly instead of re-running the
    ~30s pipeline; live=True bypasses. Concurrent requests for one key compute once and the
    rest reuse its result. A failed run serves the newest cached payload for the ticker,
    marked stale_as_of; the error propagates only when no cache exists. Kept in its own
    subdir so the overview-cache globs (TICKER_*y.json) never pick these up."""
    try:
        safe_t = safe_ticker(ticker)   # trust boundary: keep request-derived ticker out of the path
    except ValueError:
        # invalid ticker -> skip the cache path entirely; hazard_analyze resolves it and raises cleanly
        return jsonable(_native(hazard_analyze(ticker, years, progress=progress)))
    p = CACHE_DIR / "hazard" / f"{safe_t}_{int(years)}y.json"
    asked_at = time.time()

    def _reuse() -> Optional[dict]:
        # a live request reuses only a file written after it was asked
        if not p.exists() or (live and p.stat().st_mtime < asked_at):
            return None
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except Exception:   # an unreadable cache file means a fresh run, not a failure
            log.exception("hazard cache unreadable: %s", p)
            return None
        if blob.get("as_of") != dt.date.today().isoformat():
            return None
        if progress:
            progress.emit("Served from today's hazard cache.", step="cache", pct=100)
        return blob["data"]

    if not live and (data := _reuse()) is not None:   # a cache hit never waits on a run
        return data
    with single_flight(("hazard", safe_t, int(years)), progress):
        data = _reuse()
        if data is not None:
            return data
        try:
            data = jsonable(_native(hazard_analyze(ticker, years, progress=progress)))
        except Exception:
            stale = _newest_hazard_cache(safe_t)
            if stale is None:
                raise
            log.exception("hazard run failed for %s; serving the cache from %s",
                          ticker, stale["stale_as_of"])
            if progress:
                progress.emit(f"Live run failed; serving the cached run from "
                              f"{stale['stale_as_of']}.", step="cache", pct=100)
            return stale
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"as_of": dt.date.today().isoformat(), "data": data}),
                         encoding="utf-8")
        except Exception:   # caching is best-effort; never fail a request over it
            log.exception("hazard cache not written: %s", p)
        return data


@app.get("/api/screen")
def screen() -> JSONResponse:
    """Every analyzed company's headline metrics — filtering happens client-side. Most
    levered first: n.m. leverage (EBITDA ≤ 0, the most distressed case) leads, and rows
    whose leverage was never extracted trail."""
    from sqlalchemy import case, desc, nulls_last

    with session_scope() as session:
        rows = (session.query(models.Snapshot)
                .order_by(case((models.Snapshot.ebitda <= 0, 0), else_=1),
                          nulls_last(desc(models.Snapshot.economic_leverage))).all())
        out = []
        for r in rows:
            try:
                badge = distress_badge(session, r.ticker, r.last_price)
            except Exception:
                log.exception("distress badge failed for %s", r.ticker)
                badge = None
            out.append({
                "ticker": r.ticker, "issuer": r.issuer, "last_updated": r.last_updated,
                "reported_leverage": r.reported_leverage,
                "economic_leverage": r.economic_leverage,
                "ebitda": r.ebitda,
                "net_market_leverage": r.net_market_leverage,
                "creation_multiple_fulcrum": r.creation_multiple_fulcrum,
                "ebitda_capex_leverage": r.ebitda_capex_leverage,
                "flag_count": r.flag_count,
                "overall_risk": r.overall_risk, "trained_pd": r.trained_pd,
                "implied_rating": r.implied_rating,
                "runway_months": r.runway_months,
                "distress_badge": badge,
            })
        return JSONResponse(content=jsonable(out))


@app.get("/api/company/{ticker}/capital/ladder")
def capital_ladder(ticker: str, years: int = Query(3, ge=1, le=10),
                   recast_mezz: int = Query(0, ge=0, le=1)):
    """Creation-multiple ladder: cumulative claims through each class at face and
    at market ÷ EBITDA, plus the quotes matched per instrument (quote_by_instrument, which
    Recovery prices its IRR and blocking cost from). On-demand (not cached in the overview)
    so a marks refresh reprices without a pipeline run. recast_mezz=1 appends temporary
    equity as a preferred claim before the structure is derived (Moyer, Distressed Debt
    Analysis, ch. 6)."""
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

        # per-quote spread flags off the coarse 3-point treasury curve;
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
        except Exception:   # quotes still list, without spreads or the equity leg
            log.exception("treasury curve or equity price unavailable for %s", ticker)
        quotes_out = []
        for b in bonds:
            spr = spread_bps(b, treasuries) if treasuries else None
            quotes_out.append({**b, "spread_bps": spr,
                               "wide_spread": bool(spr is not None and spr > 1000)})
        payload["quotes"] = quotes_out
        payload["quote_feed"] = {k: v for k, v in feed.items() if k != "bonds"}
        payload["detector"] = detect_capacity_avoidance(ov, equity_price, bonds)
        return JSONResponse(content=jsonable(payload))

    return _handle_pipeline_errors(_run)


@app.get("/api/company/{ticker}/capital/refi")
def capital_refi(ticker: str, years: int = Query(3, ge=1, le=10)):
    """Refi-wall sequencing: per maturity bucket, internal repayability (sequential sweep
    funding) and the bond-mark market overlay. On-demand so a quote refresh reprices
    without a pipeline run."""
    from .capstack.refi import build_refi_wall
    from .hazard.trace import get_issuer_bonds

    def _run():
        ov = json.loads(run_overview(ticker, years).model_dump_json())
        bonds = get_issuer_bonds(ticker).get("bonds") or []
        return JSONResponse(content=jsonable(
            build_refi_wall(ov, bonds)))

    return _handle_pipeline_errors(_run)


@app.get("/api/company/{ticker}/holders")
def known_holders(ticker: str) -> JSONResponse:
    """Registered-fund holders of the issuer's debt (N-PORT data set, when ingested) in the
    latest reported quarter, largest positions first. One quarter only: a position carried
    across quarters would otherwise count once per filing."""
    from sqlalchemy import desc, func, nulls_last

    from .nport import COVERAGE_NOTE

    h = models.NportHolding
    with session_scope() as session:
        quarter = (session.query(func.max(h.report_quarter))
                   .filter(h.ticker == ticker.upper()).scalar())
        rows = (session.query(h)
                .filter(h.ticker == ticker.upper(), h.report_quarter == quarter)
                .order_by(nulls_last(desc(h.value_usd)))
                .limit(500).all())
        return JSONResponse(content=jsonable({
            "note": COVERAGE_NOTE,
            "quarter": quarter,
            "holdings": [{
                "fund_name": r.fund_name, "title": r.title, "instrument": r.instrument,
                "value_usd": r.value_usd, "pct_of_fund": r.pct_of_fund, "cusip": r.cusip,
            } for r in rows],
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


# ---- event store: the events feed ---------------------------------------------------

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
    """Event-store firehose. Ordered detected_at DESC NULLS LAST — live
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


def jsonable(obj):
    """dates and other non-JSON scalars -> strings (filing dicts carry datetime.date)."""
    return json.loads(json.dumps(obj, default=str))


class SimulateBody(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)   # reject Infinity/NaN floats at the boundary
    sim: dict = {}                      # SimConfig overrides (base_ebitda, corr, n_draws, ...)
    structure: Optional[dict] = None    # explicit {entities, tranches, admin_fees}; else derived
    petition_date: Optional[str] = None  # derives accrual_years vs debt_schedule_asof:
                                         # unsecured interest stops accruing at the petition
    attack: Optional[str] = None         # priority-attack scenario (fulcrum.attacks)
    attack_target: Optional[str] = None  # tranche name; default = all secured
    mode: Optional[str] = None           # "liquidation" forces the asset-based waterfall
    priming: Optional[dict] = None       # {face ($mm), entity?} — rank-0 secured layer
                                         # (fulcrum.proforma.prime)


def _structure_dict(structure: CapitalStructure) -> dict:
    return {
        "name": structure.name,
        "entities": [e.__dict__ for e in structure.entities],
        "tranches": [t.__dict__ for t in structure.tranches],
        "admin_fees": structure.admin_fees,
        "admin_pct": structure.admin_pct,
    }


class RecoveryCtx(NamedTuple):
    """Everything the recovery routes share: the cap table (from the request body or
    derived), EBITDA, provenance, and the petition-tolled accrual."""
    structure: CapitalStructure
    ebitda: Optional[float]
    source: str
    citations: dict
    subsidiaries: list
    ov: dict
    accrual_years: float


def _require_finite(obj, where: str) -> None:
    """ValueError (the recovery routes' 400) on a NaN or infinity anywhere in a request
    dict: the JSON parser accepts those literals, and pydantic's allow_inf_nan only covers
    float-typed fields, not the untyped sim/structure/priming dicts."""
    if isinstance(obj, float) and not math.isfinite(obj):
        raise ValueError(f"{where} must be a finite number")
    if isinstance(obj, dict):
        for k, v in obj.items():
            _require_finite(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _require_finite(v, f"{where}[{i}]")


def recovery_context(ticker: str, years: int, sim: dict,
                     structure: Optional[dict] = None,
                     petition_date: Optional[str] = None,
                     need_ov: bool = True) -> RecoveryCtx:
    """The shared preamble of every recovery route (simulate/explore/exchange/
    liquidation/structure). Cap table from the
    request body if given, else derived from the capstack overview (cache-first) with
    an XBRL total-debt seed fallback; EBITDA defaulting; petition date -> accrual toll.
    need_ov=False skips the overview load on the body-structure path (offline callers)."""
    _require_finite(sim, "sim")
    _require_finite(structure, "structure")
    citations: dict = {}
    subsidiaries: list = []
    ebitda = sim.get("base_ebitda")
    if structure is not None:
        st = _structure_from_body(ticker, structure)
        source = "request body"
        ov: dict = {}
        if need_ov:
            try:   # cache-first; only needed for petition accrual / quotes / assets
                ov = json.loads(run_overview(ticker, years).model_dump_json())
            except Exception:   # the body's cap table still runs, without those legs
                log.exception("overview unavailable for %s; running the request body alone", ticker)
                ov = {}
    else:
        ov = json.loads(run_overview(ticker, years).model_dump_json())
        st, derived_ebitda, citations = overview_to_structure(ov)
        subsidiaries = ov.get("subsidiaries") or []
        source = "filed debt schedule"
        if not st.tranches:
            # no debt schedule extracted: seed one editable tranche from the forensic
            # table's latest cited total debt
            total_debt, citations = None, {}
            for row in reversed(ov.get("forensic_table") or []):
                cv = row.get("total_debt")
                if cv and cv.get("value"):
                    total_debt = float(cv["value"]) / 1e6
                    if cv.get("citation"):
                        citations["Total debt (no debt schedule extracted)"] = cv["citation"]
                    break
            st = CapitalStructure(
                name=st.name,
                entities=[Entity("OpCo", ev_share=1.0, parent=None)],
                tranches=[Tranche("Total debt (no debt schedule extracted)", "OpCo",
                                  face=total_debt or 100.0, lien_rank=1, secured=True)],
            )
            source = "reported total debt" if total_debt else "placeholder face"
        if ebitda is None:
            ebitda = derived_ebitda
    accrual = float(sim.get("accrual_years") or 0.0)
    if petition_date and "accrual_years" not in sim:
        accrual = _accrual_from_petition(petition_date, ov)
    return RecoveryCtx(st, ebitda, source, citations, subsidiaries, ov, accrual)


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
    row: debt-like redemption obligations carried as equity (Moyer, Distressed Debt
    Analysis, ch. 6)."""
    cv = ov.get("mezzanine") or {}
    if not cv.get("value") or float(cv["value"]) <= 0:
        return None
    return {"value": round(float(cv["value"]) / 1e6, 1),
            "formula": "temporary-equity carrying amount",
            "note": "recast as a preferred claim — pays after debt, before common; "
                    "carrying ≈ liquidation preference + accrued dividends and may "
                    "include redeemable NCI"}


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
                    "dilute the unsecured pool in chapter 11"}


@app.get("/api/company/{ticker}/recovery/structure")
def recovery_structure(ticker: str, years: int = Query(3, ge=1, le=10)):
    """The editable cap table for the Recovery page — derived, never re-entered."""
    ctx = recovery_context(ticker, years, {})
    available_entities = [{"name": s.get("name"), "jurisdiction": s.get("jurisdiction")}
                          for s in ctx.subsidiaries if s.get("name")]
    return JSONResponse(content=jsonable({
        "structure": _structure_dict(ctx.structure), "base_ebitda": ctx.ebitda,
        "base_ebitda_label": ltm_ebitda(ctx.ov)[1],
        # read with the cap table so the first run already accrues to the petition date
        "chapter11": ctx.ov.get("chapter11"),
        "source": ctx.source,
        "citations": ctx.citations, "available_entities": available_entities,
        "suggested_other_claims": _suggested_other_claims(ctx.ov),
        "suggested_mezzanine": _suggested_mezzanine(ctx.ov),
        "asset_snapshot": ctx.ov.get("asset_snapshot"),
        # priming pre-seed: the covenant-dollars liens read (suggested_priming inside)
        "liens_headroom": ctx.ov.get("liens_headroom"),
    }))


def _liquidation_payload(structure: CapitalStructure, ov: dict,
                         accrual_years: float, note: str, body_rates=None,
                         body_admin=None, body_assets=None) -> dict:
    """Asset-based waterfall payload, for when positive EBITDA is unattainable and
    cash-flow metrics say nothing. Degrades with a note when no asset snapshot was
    extracted."""
    from .fulcrum.liquidation import assets_from_snapshot, liquidate

    assets = body_assets or assets_from_snapshot(ov.get("asset_snapshot"))
    if assets is None:
        return {
            "mode": "liquidation", "available": False, "accrual_years": accrual_years,
            "structure": _structure_dict(structure), "note": note,
            "detail": "no balance-sheet asset categories are available for this issuer, "
                      "so there is nothing to liquidate"}
    out = liquidate(assets, structure, rates=body_rates, admin_pct=body_admin,
                    accrual_years=accrual_years)
    out.update({"available": True, "structure": _structure_dict(structure), "note": note,
                "accrual_years": accrual_years, "asset_snapshot": ov.get("asset_snapshot")})
    return out


def _save_recovery_summary(ticker: str, payload: dict) -> Optional[dict]:
    """Persist the run's headline numbers on the snapshot row (the strip's RECOVER cell) and
    return them, so the response carries the same shape. None when nothing was saved.
    Best-effort — never fails a simulate over a summary write."""
    try:
        saved_at = dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat()
        if payload.get("mode") == "liquidation":
            if not payload.get("available"):
                return
            sc = payload.get("scenario") or {}
            summary = {
                "mode": "liquidation", "saved_at": saved_at,
                "fulcrum": sc.get("fulcrum"),
                "net_proceeds": sc.get("net_proceeds"),
                "total_face": round(sum(float(t.get("face") or 0.0)
                                        for t in sc.get("tranches") or []), 1),
                "tranches": [{"tranche": t.get("tranche"),
                              "mean_recovery_pct": t.get("recovery_pct"),
                              "is_fulcrum": bool(t.get("is_fulcrum"))}
                             for t in sc.get("tranches") or []],
            }
        else:
            ev = payload.get("ev") or {}
            summary = {
                "mode": "simulation", "saved_at": saved_at,
                "fulcrum": payload.get("fulcrum"),
                "ev_median": ev.get("median"), "ev_p10": ev.get("p10"),
                "ev_p90": ev.get("p90"),
                "total_face": payload.get("total_face"),
                "n_draws": (payload.get("sim") or {}).get("n_draws"),
                "tranches": [{"tranche": t.get("tranche"),
                              "mean_recovery_pct": t.get("mean_recovery_%"),
                              "is_fulcrum": bool(t.get("is_fulcrum"))}
                             for t in payload.get("tranches") or []],
            }
        # a NaN or inf must never reach the snapshot row: it breaks the Overview that reads it
        json.dumps(summary, allow_nan=False, default=str)
        summary = jsonable(summary)
        with session_scope() as session:
            update_snapshot_recovery(session, ticker.strip().upper(), summary)
        return summary
    except Exception:
        log.exception("recovery summary not saved for %s", ticker)
        return None


@app.post("/api/company/{ticker}/recovery/simulate")
def recovery_simulate(ticker: str, body: SimulateBody, years: int = Query(3, ge=1, le=10)):
    """Fulcrum Monte Carlo. Cap table comes from the request body if given, otherwise it is
    derived from the capstack overview (cache-first) — no manual re-entry.

    EBITDA ≤ 0 (or mode="liquidation") switches to the asset-based liquidation waterfall
    instead of failing: a going-concern EV simulation is meaningless below zero EBITDA."""
    sim_kwargs = dict(body.sim)
    try:
        _require_finite(body.priming, "priming")
        # a run that will liquidate needs the overview's asset snapshot
        need_ov = bool(body.structure is None or body.petition_date
                       or body.mode == "liquidation"
                       or not (sim_kwargs.get("base_ebitda") or 0) > 0)
        ctx = recovery_context(ticker, years, sim_kwargs, structure=body.structure,
                               petition_date=body.petition_date, need_ov=need_ov)
        structure, source, ov = ctx.structure, ctx.source, ctx.ov
        sim_kwargs.setdefault("base_ebitda", ctx.ebitda)

        if body.petition_date and "accrual_years" not in body.sim:
            sim_kwargs["accrual_years"] = ctx.accrual_years

        base_ebitda = sim_kwargs.get("base_ebitda")
        if body.mode == "liquidation" or base_ebitda is None or base_ebitda <= 0:
            note = ("forced liquidation mode" if body.mode == "liquidation" else
                    "EBITDA ≤ 0, so a going-concern EV is not meaningful: recovery is "
                    "valued on an asset liquidation instead")
            payload = _liquidation_payload(structure, ov,
                                           float(sim_kwargs.get("accrual_years") or 0.0), note)
            payload["recovery_summary"] = _save_recovery_summary(ticker, payload)
            return JSONResponse(content=jsonable(payload))

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
        if body.priming is not None:   # same EV draws as the attack block
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
    except (ValueError, TypeError) as exc:   # engine validators = the input trust boundary
        return JSONResponse(status_code=400, content={"error": str(exc)})

    ev = result.sim.ev
    order = result.structure.priority_order()
    ay = result.accrual_years
    tmap = {t.name: t for t in result.structure.tranches}
    face = {n: tmap[n].face for n in order}
    claim = {n: tmap[n].claim(ay) for n in order}   # recovery % is against the allowed claim

    # Chart payload, computed server-side so the response stays small (no raw draws).
    # No per-tranche histograms: the table's mean/P10/P90/P(zero) carries the distribution.
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

    payload = {
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
        "waterfall_at_median": waterfall_at_median,
        "headroom_506": headroom_506,
        "attack": body.attack,
        "attack_tranches": attack_rows,
        "priming_tranches": priming_rows,
        "primed_structure": primed_dict,
    }
    payload["recovery_summary"] = _save_recovery_summary(ticker, payload)
    return JSONResponse(content=jsonable(payload))


@app.post("/api/company/{ticker}/recovery/explore")
def recovery_explore(ticker: str, body: SimulateBody, years: int = Query(3, ge=1, le=10)):
    """Deterministic EV explorer: per-tranche recovery curves over an EV grid, breakpoints,
    coverage-vs-multiple, and the 'market has not repriced' flag. Works at negative EBITDA."""
    from .capstack.quotes import match_quotes
    from .fulcrum.explore import explore
    from .hazard.trace import get_issuer_bonds

    try:
        ctx = recovery_context(ticker, years, body.sim, structure=body.structure,
                               petition_date=body.petition_date)
        structure, ebitda, ov, accrual = ctx.structure, ctx.ebitda, ctx.ov, ctx.accrual_years
        matches, _ = match_quotes(ov.get("debt_schedule") or [],
                                  get_issuer_bonds(ticker).get("bonds") or [])
        prices = [q["last_price"] for q in matches.values() if q.get("last_price") is not None]
        out = explore(structure, ebitda, accrual, quotes=prices or None)
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
    """Exchange-offer analyzer (Moyer, Distressed Debt Analysis, ch. 11): a calculator over
    typed offer terms — holdout-vs-tender payoff curves per participation level, direct
    run_waterfall per scenario over the BASE structure's EV grid."""
    from .capstack.quotes import match_quotes
    from .edgar.facts import derived_value, fmt_money_millions
    from .fulcrum.explore import _cross
    from .fulcrum.proforma import exchange_scenario
    from .hazard.trace import get_issuer_bonds

    try:
        ctx = recovery_context(ticker, years, body.sim, structure=body.structure,
                               petition_date=body.petition_date)
        structure, ebitda, ov, accrual = ctx.structure, ctx.ebitda, ctx.ov, ctx.accrual_years

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

        # holdout runway: can the company carry a holdout fight?
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
                f"(cash {fmt_money_millions(cash * 1e6)} − tender cash spend "
                f"{fmt_money_millions(spend * 1e6)}) ÷ quarterly burn "
                f"{fmt_money_millions(burn / 4.0 * 1e6)} — quarters of burn the remaining "
                "cash funds, i.e. how long the company can carry a holdout fight",
                f"{rq:,.1f} qtrs").model_dump()
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


@app.get("/api/company/{ticker}/recovery/case")
def recovery_case(ticker: str, years: int = Query(3, ge=1, le=10)):
    """Chapter-11 case card inputs (Moyer, Distressed Debt Analysis, ch. 12): the cited
    petition date (the 8-K Item 1.03 filing date, a proxy for the docket petition date)
    and a weak free-fall hint (revolver drawn ≈ $0 undrawn). The statutory
    clocks/benchmark are computed frontend."""
    from .capstack.eightk import petition_citation, petition_filing

    ov = _cached_overview(ticker, years)   # cache-only: a page-mount fetch must not launch a live run

    petition = pf = None
    petition_error = False   # distinguish an EDGAR fetch failure from a genuine "no 1.03"
    cik = _ov_cik(ov)
    if cik:
        try:
            pf = petition_filing(cik)
        except Exception:
            log.exception("8-K petition lookup failed for %s", ticker)
            pf, petition_error = None, True
        if pf and pf.get("date"):
            petition = {"value": pf["date"], "display": pf["date"], "derived": False,
                        "citation": petition_citation(pf).model_dump()}

    liq = ov.get("liquidity") or {}
    undrawn = liq.get("undrawn_committed")
    note = ("petition date = the 8-K Item 1.03 filing date (proxy for the docket petition date); "
            "case type is the analyst's call. A pre-filing revolver drawdown weakly hints free-fall "
            "but isn't reliably tagged, so it is surfaced (undrawn figure) rather than auto-inferred.")
    if not ov:
        note = ("no analysis is cached for this company yet; open its Capital Structure tab "
                "to build one. " + note)
    return JSONResponse(content=jsonable({
        "petition_date": petition,
        # the case's later 1.03 ({date, accession, source_url}): no longer a live case
        "plan_confirmed": (pf or {}).get("plan_confirmed"),
        "petition_error": petition_error,   # True = EDGAR lookup failed (NOT "no bankruptcy")
        "revolver_undrawn": undrawn,        # cited undrawn-committed figure; the analyst judges free-fall
        "note": note,
    }))


class DocketBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subtype: str = Field(pattern=r"^[a-z0-9_]{1,32}$")
    occurred_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    title: str = Field(min_length=1, max_length=300)
    docket_no: Optional[str] = Field(None, max_length=32, pattern=r"^[A-Za-z0-9.\-]*$")
    source_url: Optional[str] = Field(None, max_length=500)


@app.post("/api/company/{ticker}/recovery/docket")
def add_docket_event(ticker: str, body: DocketBody):
    """Manual docket ingest of Chapter 11 milestones — a direct event-store write with
    source='manual', not a registry-detector route. Re-POST of the same (cik, date,
    subtype[, docket_no]) is idempotent (inserted=0) — that is the edit path. /api/events
    serves these rows, and a client may render source_url as a link, hence the http(s)
    guard below."""
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
        if cik is None:   # answered by the app-level 404 handler
            raise TickerNotFoundError(f"{ticker.strip().upper()!r} is in neither the "
                                      f"universe nor the snapshot index")
        ev = docket_event(cik, body)
        # ponytail: a manual row is stored as entered (source='manual'); nothing checks it
        # against the court docket
        n = insert_events(session, [ev], detected_at=dt.datetime.now(dt.UTC).replace(tzinfo=None))
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
    """Crisis-of-confidence four-factor screen (Moyer, Distressed Debt Analysis, ch. 8): a
    restatement/fraud 8-K trigger (Items 4.01/4.02/5.02) filed in the last three years,
    assessed against the four liquidity factors — cash, revolver reliance,
    acceleration/MAC language, and the immediate cash need. An older 8-K is history, not
    a live trigger. On-demand: an 8-K fetch must not gate every overview build or
    screener row."""
    from sqlalchemy import text as sql

    from .capstack.eightk import crisis_screen, crisis_triggers
    from .core.db import FTS_AVAILABLE

    ov = _cached_overview(ticker, years)   # cache-only: a page-mount fetch must not launch a live run

    triggers, trigger_error = [], False
    cik = _ov_cik(ov)
    if cik:
        since = (dt.date.today() - dt.timedelta(days=3 * 365)).isoformat()
        try:
            triggers = crisis_triggers(cik, since=since)
        except Exception:   # EDGAR lookup failed — reported as unknown, NOT "no trigger"
            log.exception("8-K crisis-trigger lookup failed for %s", ticker)
            trigger_error = True

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
        except Exception:   # the factor then reads unknown
            log.exception("acceleration-clause scan failed for %s", ticker)
            accel = {"clauses_found": 0, "sample": None, "available": False}

    screen = crisis_screen(triggers, ov.get("liquidity"), ov.get("liquidity_events"), accel)
    screen["trigger_error"] = trigger_error
    return JSONResponse(content=jsonable(screen))


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
        _require_finite({"rates": body.rates, "assets": body.assets}, "body")
        ctx = recovery_context(ticker, years, {"accrual_years": body.accrual_years},
                               structure=body.structure, need_ov=body.assets is None)
        payload = _liquidation_payload(ctx.structure, ctx.ov, body.accrual_years,
                                       "liquidation analysis", body.rates, body.admin_pct,
                                       body.assets)
        return JSONResponse(content=jsonable(payload))
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
    """Stream progress events as SSE, then a final `overview` (or `error`) event. The
    overview carries the last recovery summary, as GET /api/overview does."""
    event_q: "queue.Queue[dict]" = queue.Queue()

    def sink(evt: ProgressEvent) -> None:
        event_q.put({"event": "progress", "data": evt.to_dict()})

    def worker() -> None:
        progress = ProgressLog(sink=sink)
        try:
            ov = run_overview(ticker, years, progress=progress, live=live)
            event_q.put({"event": "overview", "data": _with_recovery_summary(
                ticker, json.loads(ov.model_dump_json()))})
        except TickerNotFoundError as exc:
            event_q.put({"event": "error", "data": {"error": "ticker_not_found", "detail": str(exc)}})
        except NoFilingsError as exc:
            event_q.put({"event": "error", "data": {"error": "no_filings", "detail": str(exc)}})
        except Exception as exc:
            log.exception("overview stream failed for %s", ticker)
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
        progress = ProgressLog(sink=sink)
        try:
            data = _hazard_section(ticker, years, live, progress=progress)
            event_q.put({"event": "hazard", "data": data})
            try:   # fill the screening index's risk columns; never fail the stream
                with session_scope() as session:
                    update_snapshot_risk(session, ticker.strip().upper(), data)
            except Exception:
                log.exception("screen risk columns not updated for %s", ticker)
        except TickerNotFoundError as exc:
            event_q.put({"event": "error", "data": {"error": "ticker_not_found", "detail": str(exc)}})
        except Exception as exc:
            log.exception("hazard stream failed for %s", ticker)
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
