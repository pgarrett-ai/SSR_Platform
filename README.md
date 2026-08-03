# SSR Platform — Special Situations Research Platform

**A screen → dossier funnel for distressed credit.** The universe screener and events
feed are the front door: they surface names going distressed — leverage, runway, risk
score, distress fact pattern, 8-K/docket events. Clicking a name opens the single-name
dossier (Capital Structure → Default Risk → Recovery → Timeline), which answers three
questions with every number cited to a primary SEC source: **what does it really owe**,
**when does it default**, and **what does each tranche recover**. Anything that doesn't
help find a name or answer those three questions doesn't belong in the app.

## What it does

- **Screen & detect** — a cross-company screening index (reported/economic/at-market
  leverage, runway, composite risk, distress badge) plus an always-on EDGAR event
  daemon: 8-K item detectors, structural form detectors (NT 10-K, 25, 15, 13D/G),
  monthly 17g-7 rating-default events, manual docket milestones — every event stamped
  with both `occurred_at` and `detected_at`.
- **Capital structure & hidden leverage** — XBRL fundamentals plus LLM extraction of
  debt-schedule, lease, pension, and off-balance-sheet footnotes; reported → economic
  leverage bridge; XBRL tie-out warnings when footnote readings disagree with tagged
  facts; covenant packages with LME vectors and dollar builds; refi-wall sequencing;
  Exhibit 21 legal-entity trees; quarterly TTM timelines.
- **Default risk** — scorecards (Altman Z″, Merton distance-to-default with a real PD
  term structure, CHS hazard) alongside a gradient-boosted hazard model trained on real
  default events (2010–2026): 8-K Item 1.03 bankruptcies plus Fitch 17g-7 D/RD rating
  actions (distressed exchanges), walk-forward validated, survivorship-bias-free
  point-in-time controls, PDs calibrated to a measured base rate and mapped to an
  implied agency rating band. Hidden leverage blends into the composite.
- **Recovery** — editable capital-structure waterfall with Monte Carlo simulation over
  enterprise value: allowed claims (principal + accrued + make-wholes), structural
  subordination across entities, fulcrum-security identification, priority attacks and
  priming scenarios, liquidation mode. The last run's summary feeds the Overview card
  and the screener — simulate once, read everywhere.

## Stack

FastAPI + SQLite backend (`:8001`) · events worker (`python -m app.worker`) ·
React/Vite/Tailwind frontend (`:5173`).

## Quickstart

```bat
:: 1. configure (SEC_USER_AGENT is the only required setting)
copy platform\.env.example 

:: 2. install
cd platform\backend
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
cd ..\frontend && npm install

:: 3. launch API + events worker + frontend
cd .. && platform_launch.bat
```

(Or `./platform/launch.sh` from WSL/Git Bash; `./platform/stop.sh` stops all three.)
Open http://localhost:5173 and pick a company. `/api/health` reports `worker.alive`.

## Tests

```bat
cd platform\backend && .venv\Scripts\python -m pytest tests -q
```

## Database & migrations

SQLite (`DATABASE_URL`, default under `platform/backend/app/data/`) remains the default
store and the pytest fixture. The event-store tables (`universe`, `events`, `scores`,
watchlists, alerts) are Alembic-managed; legacy tables predate Alembic and are created
by the app itself on startup.

    cd platform\backend
    .venv\Scripts\python -m alembic upgrade head   # fresh DB: create the event store (run BEFORE first app start on Postgres)
    .venv\Scripts\python -m alembic stamp 0001     # optional: `upgrade head` is safe on an existing DB — it skips tables create_all already made; `stamp 0001` remains equivalent

Rule going forward: any schema change ships with an Alembic revision; the
`_ensure_columns` micro-migration is frozen.

**Postgres (optional):** install PostgreSQL 16 (winget `PostgreSQL.PostgreSQL.16`, or the
EDB installer), then `createdb -U postgres distressed`, `pip install "psycopg[binary]"`,
set `DATABASE_URL=postgresql+psycopg://postgres:<pw>@localhost:5432/distressed` in
`platform/.env`, and run `alembic upgrade head`. The snapshots screener re-seeds itself
from the JSON cache on first start; FTS5 clause search is SQLite-only and degrades to
empty results on Postgres.

## Hazard model bundle

Trained model bundles are not committed. Rebuild from primary sources — a quick pass
(~15 minutes) or the full panel (multi-hour EDGAR fetch):

```bat
python -m app.hazard.labels --defaulters 120 --controls 480 --start-year 2010
python -m app.hazard.labels --defaulters 900 --controls 3600 --start-year 2010
```

Harvested event and universe caches ship in `platform/backend/app/hazard/data/`
(`--harvest-sd` refreshes the Fitch 17g-7 D/RD events). Cloud option: open
`platform/backend/notebooks/train_hazard_molab.py` on https://molab.marimo.io
(New notebook → paste the file's GitHub URL) and run it there.

## Data sources

SEC EDGAR (XBRL company facts, full-text search, filing documents, historical CIK
lookup) · Fitch Rule 17g-7 rating histories (via ratingshistory.info CSV conversion) ·
yfinance market data · FINRA fixed-income data (optional).

## Security posture

This is a **single-user research tool**. By default (`PLATFORM_API_TOKEN` unset) the backend
is **open** — the intended posture when it is bound to `127.0.0.1` and nothing else can reach
it. Several endpoints intentionally do expensive work (LLM extraction, live EDGAR fetches,
Monte Carlo), so keep it off untrusted networks.

**The day it leaves localhost**, set `PLATFORM_API_TOKEN` in `platform/.env`. Every `/api/*`
route then requires `Authorization: Bearer <token>` (`/api/health` stays open so uptime
probes and the token-entry UI have a pre-auth liveness signal). The two SSE streams
(`/api/overview/stream`, `/api/hazard/stream`) authenticate via a same-origin `platform_token`
cookie instead, because `EventSource` cannot send headers and tokens must never go in URLs
(they land in server logs). The SPA shows a token field in the sidebar whenever
`health.auth_required` is true.

This is **shared-secret auth v1** — timing-safe (`secrets.compare_digest`) but a single
static token with no per-user identity, rotation, or rate limiting. Put a reverse proxy with
TLS in front for anything internet-facing. The API-boundary input validation (ticker
charset/length, request-body size, iteration/allocation bounds) remains defense-in-depth
*underneath* the token, not a substitute for it.

## Disclaimer

Research tooling only. Nothing here is investment advice.
