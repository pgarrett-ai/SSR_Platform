"""Real default labels (Phase 5): harvest 8-K Item 1.03 (Bankruptcy or Receivership) events
from EDGAR full-text search, build a real training panel, and train the hazard model on it.

This retires the synthetic plumbing fixture as the model's data source: the served bundle is
fitted on actual bankruptcy events, walk-forward validated (train.py already enforces that),
and carries its label provenance so the UI can say what the number is.

Run:  python -m app.hazard.labels --defaulters 120 --controls 120
      (harvest is cached in data/default_events.json; delete it to re-harvest)

Honesty notes baked in:
  * label = 1 when the firm filed an Item 1.03 8-K within `horizon_days` after the fiscal
    year end; fiscal years ending after the event are dropped (post-petition financials).
  * controls are sampled from a POINT-IN-TIME universe: every CIK that filed a 10-K in the
    panel window, per EDGAR's quarterly form indexes (cached in data/pit_universe.json).
    Firms that later delisted/died are in it, so the control sample is no longer today's
    survivors. ALL harvested defaulter CIKs are excluded from controls (not just the ones
    used), since a point-in-time universe contains every bankrupt firm by construction.
  * competing risks, cause-specific: a dead control's final fiscal year is censored
    (dropped) — its 365d horizon is cut short by the non-bankruptcy exit, and distressed
    delistings that never filed an Item 1.03 hide exactly there.
  * agency calibration: the case-control sample rate (~9%) is not the real-world default
    frequency; the bundle stores the MEASURED base rate (events / universe firm-years) and
    the scorer applies a King–Zeng prior correction, then maps PD to an implied agency
    rating band (see train.prior_correct / score.implied_rating).
"""
from __future__ import annotations

import datetime as dt
import json
import random
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from ..core.config import get_settings

DATA_DIR = Path(__file__).resolve().parent / "data"
EVENTS_PATH = DATA_DIR / "default_events.json"
UNIVERSE_PATH = DATA_DIR / "pit_universe.json"

_FTS = "https://efts.sec.gov/LATEST/search-index?"
_FORM_IDX = "https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/form.idx"


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": get_settings().sec_user_agent})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _fts_page(start: str, end: str, frm: int) -> dict:
    q = urllib.parse.urlencode({
        "q": '"Item 1.03"', "forms": "8-K", "startdt": start, "enddt": end, "from": frm,
    })
    return _get_json(_FTS + q)


def _has_item_103(src: dict) -> bool:
    items = src.get("items") or []
    if isinstance(items, str):
        items = items.split(",")
    items = [str(i).strip() for i in items]
    return ("1.03" in items) if items else True   # no items field -> trust the phrase match


def harvest_default_events(start_year: int, end_year: int,
                           pause_s: float = 0.15) -> list[dict]:
    """All 8-K Item 1.03 filings in [start_year, end_year] via EDGAR full-text search.
    Returns one event per CIK (the FIRST bankruptcy filing): {cik, name, filed}."""
    events: dict[str, dict] = {}
    for year in range(start_year, end_year + 1):
        for q0, q1 in (("01-01", "03-31"), ("04-01", "06-30"),
                       ("07-01", "09-30"), ("10-01", "12-31")):
            start, end = f"{year}-{q0}", f"{year}-{q1}"
            frm = 0
            while frm < 400:                       # FTS pages are 10 hits; sane cap per quarter
                try:
                    page = _fts_page(start, end, frm)
                except Exception:
                    break                           # transient EDGAR hiccup: skip rest of window
                hits = (page.get("hits") or {}).get("hits") or []
                for h in hits:
                    src = h.get("_source") or {}
                    if not _has_item_103(src):
                        continue
                    ciks = src.get("ciks") or src.get("cik") or []
                    cik = str(ciks[0] if isinstance(ciks, list) else ciks).lstrip("0")
                    filed = src.get("file_date")
                    if not cik or not filed:
                        continue
                    name = (src.get("display_names") or [""])[0].split("(CIK")[0].strip()
                    if cik not in events or filed < events[cik]["filed"]:
                        events[cik] = {"cik": cik, "name": name, "filed": filed}
                total = ((page.get("hits") or {}).get("total") or {}).get("value", 0)
                frm += 10
                if frm >= total:
                    break
                time.sleep(pause_s)
        print(f"  {year}: {len(events)} cumulative defaulter CIKs")
    return sorted(events.values(), key=lambda e: e["filed"])


def load_or_harvest_events(start_year: int = 2015, end_year: Optional[int] = None) -> list[dict]:
    if EVENTS_PATH.exists():
        return json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    events = harvest_default_events(start_year, end_year or dt.date.today().year)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EVENTS_PATH.write_text(json.dumps(events, indent=1), encoding="utf-8")
    return events


# ---------------------------------------------------------------------------
# Point-in-time control universe
# ---------------------------------------------------------------------------


def ten_k_ciks(form_idx_text: str) -> set[str]:
    """CIKs on exact 10-K lines (not 10-K/A etc.) of one EDGAR quarterly form.idx. Pure.
    Line layout: FORM  COMPANY NAME  CIK  DATE  FILENAME — name has spaces, so take CIK
    as the third token from the end."""
    out: set[str] = set()
    for line in form_idx_text.splitlines():
        if line.startswith("10-K "):
            parts = line.split()
            if len(parts) >= 5 and parts[-3].isdigit():
                out.add(parts[-3].lstrip("0"))
    return out


def harvest_pit_universe(start_year: int, end_year: int,
                         pause_s: float = 0.15) -> dict[str, list[int]]:
    """CIK -> [first, last] year it filed a 10-K, from EDGAR's quarterly form indexes.
    This is the point-in-time universe: a firm that filed a 10-K in year Y existed in
    year Y, whether or not it exists today. The spans also give firm-years at risk,
    which is what turns the harvest into a measured annual default base rate."""
    headers = {"User-Agent": get_settings().sec_user_agent}
    span: dict[str, list[int]] = {}
    for year in range(start_year, end_year + 1):
        for q in (1, 2, 3, 4):
            req = urllib.request.Request(_FORM_IDX.format(y=year, q=q), headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    text = r.read().decode("latin-1")
            except Exception:
                continue                        # future quarter / transient hiccup
            for cik in ten_k_ciks(text):
                if cik in span:
                    span[cik][1] = max(span[cik][1], year)
                else:
                    span[cik] = [year, year]
            time.sleep(pause_s)
        print(f"  {year}: {len(span)} cumulative 10-K filer CIKs")
    return span


def load_or_harvest_universe(start_year: int = 2015,
                             end_year: Optional[int] = None) -> dict[str, list[int]]:
    if UNIVERSE_PATH.exists():
        uni = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
        if uni and isinstance(next(iter(uni.values())), list):
            return uni                          # stale int-valued cache falls through
    uni = harvest_pit_universe(start_year, end_year or dt.date.today().year)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UNIVERSE_PATH.write_text(json.dumps(uni), encoding="utf-8")
    return uni


def annual_default_rate(events: list[dict], universe: dict[str, list[int]]) -> float:
    """Measured base rate: harvested bankruptcies per 10-K-filer firm-year at risk."""
    firm_years = sum(last - first + 1 for first, last in universe.values())
    return len(events) / firm_years


# ---------------------------------------------------------------------------
# Panel construction
# ---------------------------------------------------------------------------


def label_for_year(period_end: dt.date, event_date: Optional[dt.date],
                   horizon_days: int = 365) -> Optional[int]:
    """1 = defaulted within the horizon after this fiscal year end; 0 = survived it;
    None = fiscal year ends on/after the event (post-petition financials — drop the row)."""
    if event_date is None:
        return 0
    if period_end >= event_date:
        return None
    return 1 if (event_date - period_end).days <= horizon_days else 0


def _firm_rows(company, event_date: Optional[dt.date], lookback_years: int,
               horizon_days: int) -> list[dict]:
    from ..edgar.facts import build_financial_series
    from .features import year_features

    series = build_financial_series(company, lookback_years)
    rows = []
    for yf in series.years:
        label = label_for_year(yf.period_end, event_date, horizon_days)
        if label is None:
            continue
        f = year_features(yf)
        f.update({"firm_id": str(company.cik), "date": yf.period_end.isoformat(),
                  "label": label})
        rows.append(f)
    return rows


def build_real_panel(events: list[dict], n_defaulters: int = 120, n_controls: int = 120,
                     lookback_years: int = 8, horizon_days: int = 365, seed: int = 0,
                     start_year: int = 2015):
    """Feature panel with real labels: defaulter fiscal years labeled by proximity to the
    Item 1.03 filing, control fiscal years labeled 0 and sampled from the point-in-time
    10-K filer universe. Skips firms without usable XBRL."""
    import pandas as pd
    from edgar import Company

    from ..edgar.client import _ensure_identity
    _ensure_identity()                              # edgartools 403s without it

    skips: list[str] = []

    def try_firm(cik: str, event_date, lookback: int) -> list[dict]:
        try:
            return _firm_rows(Company(int(cik)), event_date, lookback, horizon_days)
        except Exception as exc:
            if len(skips) < 5:
                skips.append(f"CIK {cik}: {type(exc).__name__}: {exc}")
            return []

    today = dt.date.today()
    rows: list[dict] = []
    used_defaulters = 0
    for ev in events:
        if used_defaulters >= n_defaulters:
            break
        event_date = dt.date.fromisoformat(ev["filed"])
        # build_financial_series anchors its window on *today* — widen the lookback so the
        # window still covers the years BEFORE an old default event
        lookback = (today.year - event_date.year) + lookback_years
        firm = try_firm(ev["cik"], event_date, lookback)
        if any(r["label"] == 1 for r in firm):     # must contribute at least one positive
            rows.extend(firm)
            used_defaulters += 1
            if used_defaulters % 20 == 0:
                print(f"  defaulters: {used_defaulters}/{n_defaulters}")

    # every harvested defaulter is in the point-in-time universe by construction — exclude
    # them ALL from controls (not just the n used), or pre-petition years get labeled 0
    event_ciks = {e["cik"] for e in events}
    universe = load_or_harvest_universe(start_year, today.year)
    ciks = list(universe)
    random.Random(seed).shuffle(ciks)
    control_lookback = today.year - start_year     # window reaches back to start_year-1,
    used_controls = 0                              # so dead firms' old years still surface
    dead_controls = 0
    for cik in ciks:
        if used_controls >= n_controls:
            break
        if cik in event_ciks:
            continue
        firm = try_firm(cik, None, control_lookback)
        dead = universe[cik][1] <= today.year - 2  # no recent 10-K -> delisted/dead/merged
        if dead:
            # competing-risks censoring (cause-specific): the final fiscal year's forward
            # horizon is cut short by the non-bankruptcy exit, and quiet-death years are
            # where undetected distress hides — drop the last observed year, keep the rest
            firm = firm[:-1]
        if len(firm) >= 2:
            rows.extend(firm)
            used_controls += 1
            dead_controls += dead
            if used_controls % 20 == 0:
                print(f"  controls: {used_controls}/{n_controls}")

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("panel came back empty — first skip reasons: " + "; ".join(skips))
    print(f"panel: {len(df)} firm-years, {int(df['label'].sum())} defaults, "
          f"{used_defaulters} defaulter firms, {used_controls} controls "
          f"({dead_controls} no longer filing — the survivorship-bias fix at work)")
    if skips:
        print("  sample skips:", " | ".join(skips))
    return df


if __name__ == "__main__":
    import argparse

    from .train import train_from_panel

    ap = argparse.ArgumentParser()
    ap.add_argument("--defaulters", type=int, default=120)
    ap.add_argument("--controls", type=int, default=120)
    ap.add_argument("--start-year", type=int, default=2015)
    args = ap.parse_args()

    print("1/3 harvesting 8-K Item 1.03 events…")
    events = load_or_harvest_events(args.start_year)
    print(f"   {len(events)} defaulter CIKs ({events[0]['filed']} … {events[-1]['filed']})")

    print("2/3 building real panel from XBRL…")
    df = build_real_panel(events, args.defaulters, args.controls,
                          start_year=args.start_year)

    print("3/3 walk-forward training…")
    universe = load_or_harvest_universe(args.start_year)
    true_rate = annual_default_rate(events, universe)
    sample_rate = float(df["label"].mean())
    print(f"   base rate: {true_rate:.4%}/yr measured vs {sample_rate:.2%} in-sample "
          f"-> prior correction stored in bundle")
    label_source = (f"8-K Item 1.03 harvest {args.start_year}–{events[-1]['filed'][:4]} "
                    f"({int(df['label'].sum())} default firm-years / {len(df)} rows; "
                    f"controls from point-in-time 10-K filer universe "
                    f"{args.start_year}–{dt.date.today().year}, non-bankruptcy exits "
                    f"censored; PD prior-corrected to {true_rate:.2%}/yr base rate)")
    aucs, bundle = train_from_panel(df, save=True, meta={
        "label_source": label_source,
        "prior": {"sample_rate": sample_rate, "true_rate": true_rate}})
    print("walk-forward AUC by test year:")
    for y, a in sorted(aucs.items()):
        print(f"  {y}: {a:.3f}")
    print(f"label source: {label_source}")
