"""get_filing_text: the whole-filing-text fallback for issuers whose Item 8/Item 1 keys don't
expose the financial-statement notes (ATUS/TSE undimensioned-item gap). No network."""
from __future__ import annotations

import datetime as dt

from app.edgar.documents import get_filing_text


class _FakeObj:
    """No Item 8 / Item 1 keys — the ATUS/TSE shape where item parsing never finds the notes."""

    items = ["Item 2", "Item 7"]

    def __getitem__(self, key):
        return {"Item 2": "properties go here.", "Item 7": "management discussion."}[key]


class _FakeFiling:
    cik = "1234567890"
    accession_no = "0000000000-26-000001"
    form = "10-K"
    filing_date = dt.date(2026, 2, 18)
    period_of_report = dt.date(2025, 12, 31)
    url = "https://example.com/filing"

    def obj(self):
        return _FakeObj()

    def text(self):
        return ("Senior secured notes due 2030 carry an aggregate principal amount of "
                 "$500 million and bear interest at 8.5% per annum, maturing in 2030.")


def test_get_filing_text_falls_back_to_whole_filing_text_when_notes_missing():
    ft = get_filing_text(_FakeFiling())
    assert ft is not None
    assert ft.notes != ""
    assert "aggregate principal" in ft.debt_window()
