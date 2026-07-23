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
  * label enrichment (data/sd_events.json, via --harvest-sd): 17g-7 rating default
    actions (Fitch issuer D/RD, Moody's organization C) — distressed exchanges and
    missed payments that never file an Item 1.03 8-K; merged with the earliest event
    winning per CIK.
  * market features: each panel row also carries trailing-window equity vol / drawdown /
    excess return AS OF that fiscal year end (market.pit_market_features — no lookahead);
    delisted firms without price history just leave them NaN.
  * panel checkpoint store (data/panel.db, gitignored): per-firm feature rows persist the
    moment they're fetched — killed runs resume for free, bigger samples only fetch new
    firms, and the accumulated store is the training base going forward.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import random
import re
import time
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Optional

from ..edgar.http import paced_get

DATA_DIR = Path(__file__).resolve().parent / "data"
EVENTS_PATH = DATA_DIR / "default_events.json"
UNIVERSE_PATH = DATA_DIR / "pit_universe.json"
SD_PATH = DATA_DIR / "sd_events.json"
BRD_CSV = DATA_DIR / "lopucki_brd_cases.csv"  # Florida-UCLA-LoPucki BRD Cases table (operator-supplied; license: free commercial/academic use w/ attribution)

_FTS = "https://efts.sec.gov/LATEST/search-index?"
_FORM_IDX = "https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/form.idx"


def _get_json(url: str) -> dict:
    return json.loads(paced_get(url).decode("utf-8"))


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


def harvest_default_events(start_year: int, end_year: int) -> list[dict]:
    """All 8-K Item 1.03 filings in [start_year, end_year] via EDGAR full-text search.
    Returns one event per CIK (the FIRST bankruptcy filing): {cik, name, filed}."""
    events: dict[str, dict] = {}
    for year in range(start_year, end_year + 1):
        for q0, q1 in (("01-01", "03-31"), ("04-01", "06-30"),
                       ("07-01", "09-30"), ("10-01", "12-31")):
            start, end = f"{year}-{q0}", f"{year}-{q1}"
            frm = 0
            while frm < 400:                       # FTS pages are 10 hits; sane cap per quarter
                # FTS throws intermittent 500s; a silent skip here once cost two whole years
                # of defaulters (2012–2013), so retry with backoff and WARN before giving up.
                # 429/403 skip the outer retries entirely — paced_get already spent the
                # sanctioned throttle backoff (same guard as capstack.eightk._cached).
                page = None
                for attempt in range(4):
                    try:
                        page = _fts_page(start, end, frm)
                        break
                    except urllib.error.HTTPError as exc:
                        if exc.code in (429, 403):
                            break              # straight to WARN-and-truncate
                        time.sleep(2.0 * (attempt + 1))
                    except Exception:
                        time.sleep(2.0 * (attempt + 1))
                if page is None:
                    print(f"  WARN: FTS unreachable for {start}..{end} from page {frm // 10} "
                          f"— window truncated, events may be missing")
                    break
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
        print(f"  {year}: {len(events)} cumulative defaulter CIKs")
    return sorted(events.values(), key=lambda e: e["filed"])


def load_or_harvest_events(start_year: int = 2015, end_year: Optional[int] = None) -> list[dict]:
    if EVENTS_PATH.exists():
        events = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    else:
        events = harvest_default_events(start_year, end_year or dt.date.today().year)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        EVENTS_PATH.write_text(json.dumps(events, indent=1), encoding="utf-8")
    if SD_PATH.exists():   # enrich with 17g-7 rating default events; earliest event per CIK
        by_cik = {e["cik"]: e for e in events}
        for ev in json.loads(SD_PATH.read_text(encoding="utf-8")):
            cur = by_cik.get(ev["cik"])
            if cur is None or ev["filed"] < cur["filed"]:
                by_cik[ev["cik"]] = ev
        events = sorted(by_cik.values(), key=lambda e: e["filed"])
    return events


# ---------------------------------------------------------------------------
# Rating-based default events (Rule 17g-7 histories) — distressed exchanges and
# missed payments get a rating default action without ever filing an 8-K Item 1.03
# ---------------------------------------------------------------------------

# ratingshistory.info republishes the NRSROs' regulatory 17g-7 XBRL as CSV, monthly.
# S&P's own portal is bot-walled; Fitch D/RD covers the same event class (RD = restricted
# default, Fitch's marker for distressed exchanges).
_FITCH_CSV = "https://ratingshistory.info/api/public/20260501%20Fitch%20Ratings%20Corporate.csv"
_MOODYS_CSV = ("https://ratingshistory.info/api/public/"
               "20240715%20Moody's%20Investors%20Service%20Corporate.csv")
_CIK_LOOKUP = "https://www.sec.gov/Archives/edgar/cik-lookup-data.txt"

_NAME_SUFFIXES = {"INC", "CORP", "CO", "LLC", "LP", "LTD", "PLC",
                  "CORPORATION", "COMPANY", "INCORPORATED"}


def norm_name(s: str) -> str:
    """Normalize a company name for matching: uppercase, alphanumeric, legal suffixes off."""
    parts = re.sub(r"[^A-Z0-9 ]", " ", s.upper()).split()
    while parts and parts[-1] in _NAME_SUFFIXES:
        parts.pop()
    return " ".join(parts)


def _cik_by_name() -> dict[str, str]:
    """EDGAR's full historical company list (dead firms included): normalized name -> CIK.
    Names that map to more than one CIK are dropped — unique matches only."""
    text = paced_get(_CIK_LOOKUP, timeout=120).decode("latin-1")
    out: dict[str, str] = {}
    dupes: set[str] = set()
    for line in text.splitlines():                 # "COMPANY NAME:CIK:" (name may hold colons)
        name, sep, cik = line.rstrip(":").rpartition(":")
        cik = cik.strip()
        if not sep or not cik.isdigit():
            continue
        key = norm_name(name)
        if not key:
            continue
        if key in out and out[key] != cik.lstrip("0"):
            dupes.add(key)
        out[key] = cik.lstrip("0")
    for key in dupes:
        del out[key]
    return out


def sd_events_from_frame(df, lookup: dict[str, str], ratings: set[str] = frozenset({"D", "RD"}),
                         type_pattern: str = "Issuer Default",
                         source: str = "fitch_rd") -> tuple[list[dict], int]:
    """Pure part of the SD harvest: 17g-7 frame + name->CIK lookup -> (events, unmatched).
    First issuer-level default-rating action per obligor, where "default rating" =
    `ratings` on rows whose rating_type matches `type_pattern` (regex). CIK from the file
    where present, else a unique normalized-name match; unmatched obligors (mostly
    non-SEC filers) are skipped."""
    import pandas as pd

    m = df["rating"].isin(ratings) & df["rating_type"].str.contains(type_pattern, na=False)
    d = df.loc[m, ["obligor_name", "central_index_key", "rating_action_date"]].copy()
    d["obligor_name"] = d["obligor_name"].fillna("").str.strip('"')
    d = d.sort_values("rating_action_date").drop_duplicates("obligor_name")
    events, unmatched = [], 0
    for _, row in d.iterrows():
        cik = str(row["central_index_key"]).lstrip("0") if pd.notna(row["central_index_key"]) else ""
        if not cik:
            cik = lookup.get(norm_name(row["obligor_name"]), "")
        if not cik:
            unmatched += 1
            continue
        events.append({"cik": cik, "name": row["obligor_name"],
                       "filed": row["rating_action_date"], "source": source})
    return sorted(events, key=lambda e: e["filed"]), unmatched


# (url, default-rating set, rating_type regex, source tag). Moody's 17g-7 has no D/SD
# rating at all — organization-level C ("typically in default") is its default marker;
# Ca ("likely in or very near default") is deliberately excluded as too soft.
_SD_SOURCES = [
    (_FITCH_CSV, {"D", "RD"}, "Issuer Default", "fitch_rd"),
    (_MOODYS_CSV, {"C"}, "^Organization$", "moodys_c"),
]


def harvest_sd_events() -> list[dict]:
    """Download the NRSROs' Rule 17g-7 corporate rating histories and extract default
    events, earliest event per CIK across sources."""
    import pandas as pd

    lookup = _cik_by_name()
    by_cik: dict[str, dict] = {}
    for url, ratings, type_pattern, source in _SD_SOURCES:
        # ratingshistory.info isn't EDGAR but riding the paced client is harmless for two
        # one-shot downloads and keeps one outbound seam
        df = pd.read_csv(io.BytesIO(paced_get(url, timeout=300)), dtype=str)
        events, unmatched = sd_events_from_frame(df, lookup, ratings, type_pattern, source)
        print(f"  {source}: {len(events)} issuers matched to a CIK, "
              f"{unmatched} unmatched (mostly non-SEC filers)")
        for ev in events:
            cur = by_cik.get(ev["cik"])
            if cur is None or ev["filed"] < cur["filed"]:
                by_cik[ev["cik"]] = ev
    if BRD_CSV.exists():
        df = pd.read_csv(BRD_CSV, dtype=str, encoding="latin-1")  # legacy export, non-UTF8 bytes
        # Florida-UCLA-LoPucki Bankruptcy Research Database (attribution required by its license).
        df = df.rename(columns={"NameCorp": "obligor_name", "CikBefore": "central_index_key"})
        df["central_index_key"] = df["central_index_key"].replace("", pd.NA)  # blank -> name-lookup fallback
        df["rating_action_date"] = pd.to_datetime(df["DateFiled"]).dt.strftime("%Y-%m-%d")
        df["rating"] = "D"
        df["rating_type"] = "Issuer Default"
        events, unmatched = sd_events_from_frame(df, lookup, {"D"}, "Issuer Default", "lopucki_brd")
        print(f"  lopucki_brd: {len(events)} matched, {unmatched} unmatched")
        for ev in events:
            cur = by_cik.get(ev["cik"])
            if cur is None or ev["filed"] < cur["filed"]:
                by_cik[ev["cik"]] = ev
    return sorted(by_cik.values(), key=lambda e: e["filed"])


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


def harvest_pit_universe(start_year: int, end_year: int) -> dict[str, list[int]]:
    """CIK -> [first, last] year it filed a 10-K, from EDGAR's quarterly form indexes.
    This is the point-in-time universe: a firm that filed a 10-K in year Y existed in
    year Y, whether or not it exists today. The spans also give firm-years at risk,
    which is what turns the harvest into a measured annual default base rate."""
    span: dict[str, list[int]] = {}
    for year in range(start_year, end_year + 1):
        for q in (1, 2, 3, 4):
            try:
                text = paced_get(_FORM_IDX.format(y=year, q=q), timeout=60).decode("latin-1")
            except Exception:
                # no outer retry here by design — paced_get owns 429/403 backoff, and a
                # missing/future quarter or transient hiccup just skips one idx file
                continue
            for cik in ten_k_ciks(text):
                if cik in span:
                    span[cik][1] = max(span[cik][1], year)
                else:
                    span[cik] = [year, year]
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
    from ..edgar import current_ticker
    from ..edgar.facts import build_financial_series
    from .features import year_features
    from .market import benchmark_history, pit_market_features, price_history

    series = build_financial_series(company, lookback_years)
    ticker = current_ticker(company)                # dead firms keep their last ticker on EDGAR
    close = price_history(ticker) if ticker else None
    bench = benchmark_history() if close is not None else None
    rows = []
    for yf in series.years:
        label = label_for_year(yf.period_end, event_date, horizon_days)
        if label is None:
            continue
        f = year_features(yf)
        f.update(pit_market_features(close, yf.period_end, bench))
        f.update({"firm_id": str(company.cik), "date": yf.period_end.isoformat(),
                  "label": label})
        rows.append(f)
    return rows


def _fetch_firm_rows(cik: str, event_date: Optional[dt.date], lookback: int,
                     horizon_days: int) -> list[dict]:
    """Network seam: EDGAR company lookup + feature rows (monkeypatched in tests)."""
    from edgar import Company
    return _firm_rows(Company(int(cik)), event_date, lookback, horizon_days)


# Per-firm checkpoint store: every successfully fetched firm's rows land here the moment
# they're built, so a killed run resumes where it stopped and future runs only fetch NEW
# firms. Historical firm-years never change — rows are reused forever. To force a refetch
# (e.g. an annual top-up so cached firms gain their newest fiscal year):
#   DELETE FROM firm_rows WHERE fetched_at < '<date>';
PANEL_DB = DATA_DIR / "panel.db"


def _panel_db():
    import sqlite3

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(PANEL_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS firm_rows (
        cik TEXT NOT NULL,
        event_date TEXT NOT NULL DEFAULT '',
        horizon_days INTEGER NOT NULL,
        fetched_at TEXT NOT NULL,
        rows_json TEXT NOT NULL,
        PRIMARY KEY (cik, event_date, horizon_days))""")
    return con


def sign_safe_panel(df):
    """Recompute the C3-sign-safe feature columns from the raw levels every panel row
    carries (ebitda/total_debt/cash/fcf): net_debt_to_ebitda only reads as leverage when
    EBITDA > 0, and runway_years = cash / burn exists only while FCF < 0. Applied to the
    assembled panel so rows checkpointed in panel.db BEFORE the fix retrain correctly
    without refetching EDGAR. Pure; idempotent on fresh rows."""
    if {"ebitda", "total_debt", "cash"} <= set(df.columns):
        td, cash = df["total_debt"], df["cash"]
        nd = td.fillna(0.0) - cash.fillna(0.0)          # mirrors _sum(td, -(cash or 0))
        df["net_debt_to_ebitda"] = (nd / df["ebitda"]).where(df["ebitda"] > 0)
    if {"cash", "fcf"} <= set(df.columns):
        df["runway_years"] = (df["cash"] / -df["fcf"]).where(df["fcf"] < 0)
    return df


def build_real_panel(events: list[dict], n_defaulters: int = 120, n_controls: int = 120,
                     lookback_years: int = 8, horizon_days: int = 365, seed: int = 0,
                     start_year: int = 2015):
    """Feature panel with real labels: defaulter fiscal years labeled by proximity to the
    Item 1.03 filing, control fiscal years labeled 0 and sampled from the point-in-time
    10-K filer universe. Skips firms without usable XBRL.

    Per-firm rows are checkpointed to data/panel.db as they're fetched (see PANEL_DB) —
    a killed run resumes for free, and growing the sample only fetches unseen firms."""
    import pandas as pd

    from ..edgar.client import _ensure_identity
    _ensure_identity()                              # edgartools 403s without it

    skips: list[str] = []
    con = _panel_db()

    def try_firm(cik: str, event_date, lookback: int) -> list[dict]:
        ev_key = event_date.isoformat() if event_date else ""
        hit = con.execute("SELECT rows_json FROM firm_rows WHERE cik=? AND event_date=? "
                          "AND horizon_days=?", (cik, ev_key, horizon_days)).fetchone()
        if hit is not None:
            return json.loads(hit[0])
        try:
            rows = _fetch_firm_rows(cik, event_date, lookback, horizon_days)
        except Exception as exc:
            if len(skips) < 5:
                skips.append(f"CIK {cik}: {type(exc).__name__}: {exc}")
            return []           # failures are NOT cached — retried on the next run
        con.execute("INSERT OR REPLACE INTO firm_rows VALUES (?,?,?,?,?)",
                    (cik, ev_key, horizon_days, dt.date.today().isoformat(),
                     json.dumps(rows)))
        con.commit()            # per-firm transaction — the crash checkpoint
        return rows

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
    df = sign_safe_panel(df)    # repair stale checkpoint rows from before the C3 fix
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
    ap.add_argument("--harvest-sd", action="store_true",
                    help="(re)harvest 17g-7 rating default events into data/sd_events.json")
    args = ap.parse_args()

    if args.harvest_sd:
        print("0/3 harvesting 17g-7 rating default events (Fitch D/RD, Moody's C)…")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SD_PATH.write_text(json.dumps(harvest_sd_events(), indent=1), encoding="utf-8")

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
    label_kind = ("8-K Item 1.03 + 17g-7 rating defaults (Fitch D/RD, Moody's C)"
                  if SD_PATH.exists() else "8-K Item 1.03 harvest")
    label_source = (f"{label_kind} {args.start_year}–{events[-1]['filed'][:4]} "
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
    ev = bundle.get("eval") or {}
    acct = ev.get("auc_by_year_accounting_only")
    if acct:
        import statistics
        both = [(aucs[y], acct[y]) for y in aucs if y in acct]
        if both:
            print(f"ablation: mean AUC {statistics.mean(a for a, _ in both):.3f} with market "
                  f"features vs {statistics.mean(b for _, b in both):.3f} accounting-only")
    cov = ev.get("market_coverage")
    if cov:
        print(f"market-feature coverage: {cov['defaulter_rows']:.0%} of defaulter rows, "
              f"{cov['control_rows']:.0%} of control rows")
    for name, op in (ev.get("operating_points") or {}).items():
        print(f"{name}: precision {op['precision']:.1%}, lift {op['lift']}x, "
              f"recall {op['recall']:.1%} ({op['n_flagged']} flagged)")
    if ev.get("calibration"):
        print("calibration (pooled out-of-sample, case-control space): "
              "decile mean-predicted vs realized")
        for c in ev["calibration"]:
            print(f"  d{c['decile']:>2}: pred {c['mean_pred']:.4f}  "
                  f"realized {c['realized']:.4f}  (n={c['n']})")
    print(f"label source: {label_source}")
