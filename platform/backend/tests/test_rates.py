"""Key-rates module: source parsers + store round-trip. No network."""
from __future__ import annotations

from app import models
from app.core.db import init_db, session_scope
from app.rates import get_key_rates, parse_fred_csv, parse_nyfed

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
