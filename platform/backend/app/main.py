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
import threading
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import datetime as dt

from . import models
from .core.cache import cached_tickers
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


@app.get("/", include_in_schema=False)
def root():
    """The API has no UI — send stray visitors (e.g. a preview tab on :8001) to the docs."""
    return RedirectResponse("/docs")


@app.get("/api/health")
def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "llm_enabled": s.llm_enabled,
        "llm_key_set": s.llm_key_set,   # lets the UI tell "toggled off" from "no key"
        "hero_tickers": sorted(s.hero_ticker_set),
        "cached": cached_tickers(),
        "sec_user_agent_set": bool(s.sec_user_agent and "example.com" not in s.sec_user_agent),
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
    ticker: str = Query(..., min_length=1, max_length=12),
    years: int = Query(3, ge=1, le=10),
    live: bool = Query(False),
):
    def _run():
        ov = run_overview(ticker, years, live=live)
        return JSONResponse(content=json.loads(ov.model_dump_json()))

    return _handle_pipeline_errors(_run)


@app.get("/api/filings")
def filings(
    ticker: str = Query(..., min_length=1, max_length=12),
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


def _hazard_section(ticker: str, years: int, live: bool) -> dict:
    """Same-day disk cache around the hazard pipeline. Market data moves daily and EDGAR on
    filings, so a day-fresh payload serves page reloads instantly instead of re-running the
    ~30s pipeline; live=True bypasses. Kept in its own subdir so the overview-cache globs
    (TICKER_*y.json) never pick these up."""
    p = CACHE_DIR / "hazard" / f"{ticker.strip().upper()}_{int(years)}y.json"
    today = dt.date.today().isoformat()
    if not live and p.exists():
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
            if blob.get("as_of") == today:
                return blob["data"]
        except Exception:
            pass
    data = jsonable(_native(hazard_analyze(ticker, years)))
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


@app.get("/api/screen")
def screen() -> JSONResponse:
    """Every analyzed company's headline metrics — filtering happens client-side."""
    from sqlalchemy import desc, nulls_last

    with session_scope() as session:
        rows = (session.query(models.Snapshot)
                .order_by(nulls_last(desc(models.Snapshot.economic_leverage))).all())
        return JSONResponse(content=jsonable([{
            "ticker": r.ticker, "issuer": r.issuer, "last_updated": r.last_updated,
            "reported_leverage": r.reported_leverage,
            "economic_leverage": r.economic_leverage,
            "flag_count": r.flag_count,
            "overall_risk": r.overall_risk, "trained_pd": r.trained_pd,
            "implied_rating": r.implied_rating,
        } for r in rows]))


@app.get("/api/rates")
def key_rates() -> JSONResponse:
    """Latest key reference rates (SOFR, EFFR, Fed Funds target, prime, T-bill, 10Y) —
    stored in the DB, refreshed when stale, served with their observation dates."""
    from .rates import LIBOR_NOTE, get_key_rates, refresh_if_stale

    with session_scope() as session:
        refresh_if_stale(session)
        return JSONResponse(content={"rates": get_key_rates(session), "note": LIBOR_NOTE})


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
           ticker: Optional[str] = Query(None, max_length=12),
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


def jsonable(obj):
    """dates and other non-JSON scalars -> strings (filing dicts carry datetime.date)."""
    return json.loads(json.dumps(obj, default=str))


class SimulateBody(BaseModel):
    sim: dict = {}                      # SimConfig overrides (base_ebitda, corr, n_draws, ...)
    structure: Optional[dict] = None    # explicit {entities, tranches, admin_fees}; else derived


def _structure_dict(structure: CapitalStructure) -> dict:
    return {
        "name": structure.name,
        "entities": [e.__dict__ for e in structure.entities],
        "tranches": [t.__dict__ for t in structure.tranches],
        "admin_fees": structure.admin_fees,
    }


def _derive_structure(ticker: str, years: int) -> tuple[CapitalStructure, Optional[float], str, dict, list]:
    """Cap table from the capstack overview (cache-first). If no debt schedule was extracted,
    seed one editable tranche from the forensic table's latest cited total debt. Also returns the
    Exhibit 21 subsidiary list so the Recovery editor can seed entities."""
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
    return structure, ebitda, source, citations, subsidiaries


def _structure_from_body(ticker: str, s: dict) -> CapitalStructure:
    return CapitalStructure(
        name=s.get("name") or ticker.upper(),
        entities=[Entity(**e) for e in s.get("entities", [])],
        tranches=[Tranche(**t) for t in s.get("tranches", [])],
        admin_fees=float(s.get("admin_fees", 0.0)),
    )


@app.get("/api/company/{ticker}/recovery/structure")
def recovery_structure(ticker: str, years: int = Query(3, ge=1, le=10)):
    """The editable cap table for the Recovery page — derived, never re-entered."""
    try:
        structure, ebitda, source, citations, subsidiaries = _derive_structure(ticker, years)
    except (TickerNotFoundError, NoFilingsError) as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    available_entities = [{"name": s.get("name"), "jurisdiction": s.get("jurisdiction")}
                          for s in subsidiaries if s.get("name")]
    return JSONResponse(content=jsonable({
        "structure": _structure_dict(structure), "base_ebitda": ebitda, "source": source,
        "citations": citations, "available_entities": available_entities,
    }))


@app.post("/api/company/{ticker}/recovery/simulate")
def recovery_simulate(ticker: str, body: SimulateBody, years: int = Query(3, ge=1, le=10)):
    """Fulcrum Monte Carlo. Cap table comes from the request body if given, otherwise it is
    derived from the capstack overview (cache-first) — no manual re-entry."""
    sim_kwargs = dict(body.sim)
    try:
        if body.structure is not None:
            structure = _structure_from_body(ticker, body.structure)
            source = "request body"
        else:
            structure, ebitda, source, _, _ = _derive_structure(ticker, years)
            sim_kwargs.setdefault("base_ebitda", ebitda)
        if not sim_kwargs.get("base_ebitda"):
            return JSONResponse(status_code=422, content={
                "error": "base_ebitda unavailable from filings — pass it in body.sim"})
        result = fulcrum_analyze(structure, SimConfig(**sim_kwargs))
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
    }))


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
    ticker: str = Query(..., min_length=1, max_length=12),
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
