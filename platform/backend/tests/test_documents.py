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


def test_debt_window_reaches_the_debt_note_past_statement_captions():
    # ATUS 10-Q: one-hit statement captions fill the first 28k chars; the instrument table
    # sits further in and must still be read, alongside the early text
    from app.edgar.documents import FilingText

    captions = "".join(f"Long-term debt, net of current maturities {n},000\n" + "x" * 6000
                       for n in range(8))
    note = ("NOTE 9. DEBT\nCSC Holdings Senior Notes: April 1, 2028 7.500% 1,045,882. "
            "Term loan B-5 matures April 15, 2027. The revolving credit facility matures "
            "July 13, 2027. Senior secured notes due 2031, aggregate principal $1,527,000.")
    ft = FilingText(accession_no="a", form_type="10-Q", filing_date="2026-11-06",
                    period_of_report="2026-09-30", source_url="u", mdna="",
                    notes=captions + note)
    window = ft.debt_window()
    assert "Term loan B-5 matures April 15, 2027" in window
    assert "net of current maturities 0,000" in window


def test_quote_in_text_folds_formatting_and_follows_splices():
    from app.edgar.documents import quote_in_text

    text = ("The Company’s pension plans were underfunded by  $1.2 billion\nat year-end. "
            "Plan assets totaled $4.0 billion.")
    assert quote_in_text("the company's pension plans were underfunded by $1.2 billion", text)
    assert quote_in_text('"underfunded by $1.2 billion ... Plan assets totaled $4.0 billion."',
                         text)
    # fragments out of order, a planted figure, and an empty quote are all rejected
    assert not quote_in_text("Plan assets totaled ... underfunded by", text)
    assert not quote_in_text("underfunded by $2.4 billion", text)
    assert not quote_in_text("", text) and not quote_in_text(None, text)
