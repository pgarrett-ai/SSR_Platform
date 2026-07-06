# SSR Platform — Special Situations Research Platform

Single-issuer distressed-credit research: enter a ticker, get an integrated view of
**capital structure**, **default risk**, and **recovery** — built on primary sources
(SEC EDGAR/XBRL), with every headline number citable back to the filing it came from.

## What it does

- **Capital structure & hidden leverage** — XBRL fundamentals plus LLM extraction of
  debt-schedule, lease, pension, and off-balance-sheet footnotes; reported → economic
  leverage bridge (EBITDAR-consistent, net-of-cash lines, tax-effected OBS items);
  XBRL tie-out warnings when footnote readings disagree with tagged facts; Exhibit 21
  legal-entity trees; quarterly TTM timelines and maturity walls.
- **Default risk** — scorecards (Altman Z″, Merton distance-to-default with a real PD
  term structure, CHS hazard) alongside a gradient-boosted hazard model trained on real
  default events (2010–2026): 8-K Item 1.03 bankruptcies plus Fitch 17g-7 D/RD rating
  actions (distressed exchanges), walk-forward validated with precision/lift and
  calibration reporting, survivorship-bias-free point-in-time controls, competing-risks
  censoring, point-in-time market features (trailing vol/drawdown/excess return per
  fiscal year end), and PDs calibrated to a measured base rate and mapped to an implied
  agency rating band. Cross-module signals (hidden leverage, MD&A tone) blend into the
  composite.
- **Recovery** — editable capital-structure waterfall with Monte Carlo simulation over
  enterprise value: allowed claims (principal + accrued + make-wholes), structural
  subordination across entities, fulcrum-security identification, per-tranche recovery
  distributions, and PD × LGD expected loss.

## Stack

FastAPI + SQLite backend (`:8001`) · React/Vite/Tailwind frontend (`:5173`) · one process each.

## Quickstart

```bat
:: 1. configure (SEC_USER_AGENT is the only required setting)
copy platform\.env.example platform\.env

:: 2. backend
cd platform\backend
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
cd .. && platform_launch.bat

:: 3. frontend
cd platform\frontend
npm install && npm run dev
```

Open http://localhost:5173 and pick a company.

## Tests

```bat
cd platform\backend && .venv\Scripts\python -m pytest tests -q
```

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

## Disclaimer

Research tooling only. Nothing here is investment advice.
