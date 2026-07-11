"""N-PORT fund holdings — who (among registered funds) holds the issuer's bonds.

Data source: the SEC's quarterly "Form N-PORT data sets" ZIP (structured TSVs, ~1GB), the
drop-file pattern: download the quarter's ZIP from
https://www.sec.gov/about/dera_form-n-port-data-sets and run scripts/ingest_nport.py — the
ZIP streams row-by-row (never extracted) and only rows matching tracked issuers land in the
nport_holdings table.

Honesty: N-PORT covers registered funds (mutual funds/ETFs) only — banks, CLOs, insurers,
and separate accounts are invisible, and reports lag the quarter end. This is a partial
holder view, labeled as such in the UI.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from typing import Optional

from sqlalchemy import select

from . import models

COVERAGE_NOTE = ("Registered funds only (N-PORT): banks, CLOs, insurers and separate "
                 "accounts are not visible in free data; holdings lag the report quarter.")

# ponytail: column names per the N-PORT data-set dictionary, with fallbacks — verify against
# the quarter's meta files on first real ingest if a column comes up empty.
_COLS = {
    "issuer_name": ("ISSUER_NAME",),
    "title": ("ISSUER_TITLE", "TITLE"),
    "cusip": ("ISSUER_CUSIP", "CUSIP"),
    "balance": ("BALANCE",),
    "value_usd": ("CURRENCY_VALUE", "VALUE_USD", "VALUE"),
    "pct_of_fund": ("PERCENTAGE",),
    "asset_cat": ("ASSET_CAT",),
}


def _pick(header: list[str], candidates: tuple[str, ...]) -> Optional[int]:
    up = [h.strip().upper() for h in header]
    for c in candidates:
        if c in up:
            return up.index(c)
    return None


def parse_title(title: str) -> tuple[Optional[float], Optional[int]]:
    """'American Airlines Group Inc 5.75% 04/20/2029' -> (5.75, 2029)."""
    coupon = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", title or "")
    if m:
        coupon = float(m.group(1))
    year = None
    ym = re.findall(r"\b((?:19|20)\d{2})\b", title or "")
    if ym:
        year = int(ym[-1])
    else:
        dm = re.search(r"\d{1,2}/\d{1,2}/(\d{2,4})", title or "")
        if dm:
            y = int(dm.group(1))
            year = y + 2000 if y < 100 else y
    return coupon, year


def match_instrument(title: str, instruments: list) -> Optional[str]:
    """Match a fund-holding title to a debt-schedule row by coupon + maturity-year tokens.
    None = issuer-level only (shown under 'unmatched paper')."""
    coupon, year = parse_title(title)
    if coupon is None:
        return None
    for inst in instruments:
        cp = inst.coupon_pct
        hi = inst.coupon_pct_max
        in_range = (cp is not None and abs(cp - coupon) < 0.01) or (
            cp is not None and hi is not None and cp - 0.01 <= coupon <= hi + 0.01)
        if not in_range:
            continue
        if year is None or inst.maturity is None:
            return inst.instrument
        hay = f"{inst.instrument} {inst.maturity}"
        hay_years = [int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", hay)]
        if str(year) in hay or (len(hay_years) >= 2 and hay_years[0] <= year <= hay_years[-1]):
            return inst.instrument
    return None


def _stream_tsv(zf: zipfile.ZipFile, name_fragment: str):
    """Yield rows from the first TSV in the zip whose name contains the fragment."""
    member = next((n for n in zf.namelist() if name_fragment.lower() in n.lower()), None)
    if member is None:
        return None
    stream = io.TextIOWrapper(zf.open(member), encoding="utf-8", errors="replace")
    return csv.reader(stream, delimiter="\t")


def ingest_zip(zip_path: str, session, issuers: dict[str, tuple[str, ...]],
               report_quarter: str) -> dict[str, int]:
    """Stream FUND_REPORTED_HOLDING.tsv, keep debt rows whose ISSUER_NAME matches a tracked
    issuer pattern, join fund names, replace the ticker's rows. Returns {ticker: n_rows}."""
    counts = {t: 0 for t in issuers}
    with zipfile.ZipFile(zip_path) as zf:
        # fund names by accession (SERIES_NAME from FUND_REPORTED_INFO; small enough to hold)
        fund_names: dict[str, str] = {}
        info = _stream_tsv(zf, "FUND_REPORTED_INFO")
        if info is not None:
            header = next(info, None) or []
            up = [h.strip().upper() for h in header]
            try:
                acc_i = up.index("ACCESSION_NUMBER")
                name_i = up.index("SERIES_NAME")
            except ValueError:
                acc_i = name_i = None
            if acc_i is not None:
                for row in info:
                    if len(row) > max(acc_i, name_i):
                        fund_names.setdefault(row[acc_i], row[name_i])

        holdings = _stream_tsv(zf, "FUND_REPORTED_HOLDING")
        if holdings is None:
            raise ValueError("FUND_REPORTED_HOLDING.tsv not found in the zip")
        header = next(holdings, None) or []
        idx = {k: _pick(header, cands) for k, cands in _COLS.items()}
        up = [h.strip().upper() for h in header]
        acc_i = up.index("ACCESSION_NUMBER") if "ACCESSION_NUMBER" in up else None
        if idx["issuer_name"] is None:
            raise ValueError(f"ISSUER_NAME column not found (header: {header[:12]}…)")

        # wipe the quarter's rows for tracked tickers, then insert matches
        for t in issuers:
            for row in session.scalars(
                select(models.NportHolding).where(
                    models.NportHolding.ticker == t,
                    models.NportHolding.report_quarter == report_quarter)
            ).all():
                session.delete(row)

        def col(row, key):
            i = idx.get(key)
            return row[i] if i is not None and len(row) > i else None

        n_flushed = 0
        for row in holdings:
            name = (col(row, "issuer_name") or "").upper()
            if not name:
                continue
            cat = (col(row, "asset_cat") or "").upper()
            if cat and not cat.startswith("DBT"):
                continue
            for ticker, patterns in issuers.items():
                if any(p in name for p in patterns):
                    try:
                        value = float(col(row, "value_usd") or 0) or None
                    except ValueError:
                        value = None
                    try:
                        pct = float(col(row, "pct_of_fund") or 0) or None
                    except ValueError:
                        pct = None
                    acc = row[acc_i] if acc_i is not None and len(row) > acc_i else None
                    session.add(models.NportHolding(
                        ticker=ticker,
                        issuer_name=col(row, "issuer_name"),
                        title=col(row, "title"),
                        cusip=col(row, "cusip"),
                        fund_name=fund_names.get(acc or "", None),
                        value_usd=value,
                        pct_of_fund=pct,
                        report_quarter=report_quarter,
                    ))
                    counts[ticker] += 1
                    n_flushed += 1
                    if n_flushed % 500 == 0:
                        session.flush()
                    break
    session.flush()
    return counts


def match_holdings_to_instruments(session, ticker: str) -> None:
    """Fill each stored holding's instrument by title-matching against the ticker's latest
    debt schedule rows."""
    inst_rows = session.scalars(
        select(models.DebtInstrumentRow).where(models.DebtInstrumentRow.ticker == ticker)
    ).all()
    if not inst_rows:
        return
    for h in session.scalars(
        select(models.NportHolding).where(models.NportHolding.ticker == ticker)
    ).all():
        h.instrument = match_instrument(h.title or "", inst_rows)
