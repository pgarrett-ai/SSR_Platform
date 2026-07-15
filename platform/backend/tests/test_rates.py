"""Key-rates module: source parsers + store round-trip. No network."""
from __future__ import annotations

import datetime as dt

from app import models
from app.core.db import init_db, session_scope
from app.rates import SERIES, get_key_rates, parse_fred_csv, parse_nyfed, refresh_if_stale

_NYFED = '{"refRates":[{"effectiveDate":"2026-07-09","percentRate":4.31,"type":"SOFR"}]}'
_FRED = "observation_date,DGS10\n2026-07-07,4.40\n2026-07-08,.\n2026-07-09,4.42\n"


def test_parse_nyfed():
    assert parse_nyfed(_NYFED) == ("2026-07-09", 4.31)
    assert parse_nyfed('{"refRates":[]}') is None


def test_parse_fred_csv_skips_missing_markers():
    assert parse_fred_csv(_FRED) == ("2026-07-09", 4.42)   # latest non-'.' row
    assert parse_fred_csv("observation_date,DGS10\n2026-07-09,.\n") is None


def test_store_roundtrip_latest_per_series():
    init_db()
    with session_scope() as s:
        s.merge(models.Rate(series="SOFR", date="2026-07-08", value=4.29, fetched_at="x"))
        s.merge(models.Rate(series="SOFR", date="2026-07-09", value=4.31, fetched_at="x"))
    with session_scope() as s:
        rows = get_key_rates(s)
        sofr = next(r for r in rows if r["series"] == "SOFR")
        assert sofr["value"] == 4.31 and sofr["date"] == "2026-07-09"
        assert sofr["label"] == "SOFR (overnight)"
        for r in s.query(models.Rate).filter_by(series="SOFR").all():
            s.delete(r)


def test_refresh_fetches_newly_added_series_despite_fresh_store(monkeypatch):
    """A series added to SERIES must be fetched on the next refresh even when every
    stored observation is fresh — the staleness early-return only applies to a full set."""
    import app.rates as rates_mod

    init_db()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    fetched: list[str] = []
    monkeypatch.setattr(rates_mod, "_fetch_latest",
                        lambda key: fetched.append(key) or ("2026-07-14", 4.9))
    try:
        with session_scope() as s:
            for key in SERIES:
                if key != "DGS30":   # DGS30 plays the "newly added" series
                    s.merge(models.Rate(series=key, date="2026-07-14", value=1.0,
                                        fetched_at=now))
        with session_scope() as s:
            refresh_if_stale(s)
            assert "DGS30" in fetched   # missing series triggered a fetch
            rows = {r["series"] for r in get_key_rates(s)}
            assert "DGS30" in rows
        fetched.clear()
        with session_scope() as s:
            refresh_if_stale(s)         # full + fresh set → early return, no fetches
            assert fetched == []
    finally:
        with session_scope() as s:
            for r in s.query(models.Rate).all():
                s.delete(r)
