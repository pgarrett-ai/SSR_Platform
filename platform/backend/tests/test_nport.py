"""N-PORT ingest: title parsing, instrument matching, and a synthetic-zip round-trip."""
from __future__ import annotations

import zipfile

from app import models
from app.core.db import init_db, session_scope
from app.nport import ingest_zip, match_holdings_to_instruments, match_instrument, parse_title
from app.schemas import DebtInstrument


def test_parse_title():
    assert parse_title("American Airlines Group Inc 5.75% 04/20/2029") == (5.75, 2029)
    assert parse_title("AMERICAN AIRLINES 2021-1 CLASS B PASS THROUGH TRUST 3.95%") == (3.95, 2021)
    assert parse_title("American Airlines Inc Term Loan") == (None, None)


def test_match_instrument_by_coupon_and_year():
    instruments = [
        DebtInstrument(instrument="5.75% Senior Notes", coupon_pct=5.75, maturity="April 2029"),
        DebtInstrument(instrument="EETCs", coupon_pct=2.88, coupon_pct_max=7.15,
                       maturity="2026 to 2038"),
    ]
    assert match_instrument("AAL 5.75% 04/20/2029", instruments) == "5.75% Senior Notes"
    assert match_instrument("AAL PASS THROUGH 3.95% 2027", instruments) == "EETCs"  # in range
    assert match_instrument("AAL 9.99% 2031", instruments) is None
    assert match_instrument("AAL term loan (no coupon)", instruments) is None


def _synthetic_zip(path):
    holding_tsv = (
        "ACCESSION_NUMBER\tISSUER_NAME\tISSUER_LEI\tISSUER_TITLE\tISSUER_CUSIP\tBALANCE\t"
        "CURRENCY_VALUE\tPERCENTAGE\tASSET_CAT\n"
        "acc-1\tAmerican Airlines Group Inc\tLEI1\tAmerican Airlines 5.75% 04/20/2029\t02376RAE2\t"
        "1000000\t985000\t0.42\tDBT\n"
        "acc-1\tSome Other Corp\tLEI2\tOther 4.00% 2030\tXXXX\t1\t1\t0.01\tDBT\n"
        "acc-2\tAMERICAN AIRLINES GROUP INC\tLEI1\tAAL 9.99% 2031\t02376RAF9\t"
        "2000000\t1900000\t0.10\tDBT\n"
        "acc-2\tAmerican Airlines Group Inc\tLEI1\tAAL common stock\t02376R102\t"
        "500\t6000\t0.01\tEC\n"          # equity — filtered by ASSET_CAT
    )
    info_tsv = (
        "ACCESSION_NUMBER\tSERIES_NAME\tSERIES_ID\n"
        "acc-1\tBig Bond Fund\tS1\n"
        "acc-2\tHigh Yield Trust\tS2\n"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("FUND_REPORTED_HOLDING.tsv", holding_tsv)
        zf.writestr("FUND_REPORTED_INFO.tsv", info_tsv)


def test_ingest_zip_roundtrip(tmp_path):
    zp = tmp_path / "nport.zip"
    _synthetic_zip(zp)
    init_db()
    with session_scope() as s:
        s.add(models.DebtInstrumentRow(ticker="AAL", instrument="5.75% Senior Notes",
                                       coupon_pct=5.75, maturity="April 2029"))
        counts = ingest_zip(str(zp), s, {"AAL": ("AMERICAN AIRLINES",)}, "2026q1")
        assert counts == {"AAL": 2}          # two debt rows; equity + other issuer skipped
        match_holdings_to_instruments(s, "AAL")
        rows = s.query(models.NportHolding).filter_by(ticker="AAL").all()
        by_fund = {r.fund_name: r for r in rows}
        assert by_fund["Big Bond Fund"].instrument == "5.75% Senior Notes"
        assert by_fund["Big Bond Fund"].value_usd == 985000.0
        assert by_fund["High Yield Trust"].instrument is None    # 9.99% matches nothing
        # rerunning the quarter replaces, not accumulates
        counts = ingest_zip(str(zp), s, {"AAL": ("AMERICAN AIRLINES",)}, "2026q1")
        assert counts == {"AAL": 2}
        assert s.query(models.NportHolding).filter_by(ticker="AAL").count() == 2
        # cleanup
        for r in s.query(models.NportHolding).filter_by(ticker="AAL").all():
            s.delete(r)
        for r in s.query(models.DebtInstrumentRow).filter_by(ticker="AAL").all():
            s.delete(r)
