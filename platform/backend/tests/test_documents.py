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


def test_quote_in_text_folds_how_exhibits_render_but_not_their_wording():
    from app.edgar.documents import quote_in_text

    # (covenant quote the model returned, the AAL/ATUS/TSE/LCID exhibit text it was checked on)
    rendered = [
        ('Incremental Revolving Commitments (the "New Revolving Commitments") to be made available',
         "Incremental Revolving Commitments (the \x93New Revolving Commitments\x94) to be made\n"
         "available"),
        ("2024 Incremental Revolving Commitments shall mean the commitment of each applicable",
         "\x932024 Incremental Revolving Commitments\x94 shall mean the commitment of each\n"
         "applicable"),
        ('(the "Commitment Increase") to an aggregate principal amount',
         "(the “ Commitment Increase ”) to an aggregate\nprincipal amount"),
        ("identified as 'Potential Defaults' on Exhibit A hereto",
         "identified as\n“Potential Defaults” on Exhibit Ahereto"),
        ('"Amortization Period" shall mean each period', '" Amortization Period"shall mean each period'),
        ("Incremental Lender desire to establish Incremental Term Loan Commitments",
         "Incremental Lender desire to\nestablishIncremental Term Loan Commitments"),
        ("Incremental Term Loans; provided, that if the All-In Yield",
         "Incremental Term Loans; provided , that    \n  if the All-In Yield"),
        ("any transfer or exchange (or de-registration) of this Note",
         "any transfer or exchange (or de-\nregistration) of this Note"),
        ("Section 6.08 | Liquidity | 157161",
         "  Section 6.08                      Liquidity                          157161  \n"),
        ("the Lien or priority of any Security Document",
         "the Lien or priority of any Security     │\n│  Document or any other security"),
        ("(ii) [reserved];", "as follows:\n\n“(ii) [reserved]”\n\n(f)Sections"),
    ]
    for quote, text in rendered:
        assert quote_in_text(quote, text), quote
    # a moved apostrophe, a renamed defined term and an edited ending are wording, not rendering
    assert not quote_in_text("an Officers' Certificate certifying",
                             "an\nOfficer\x92s Certificate certifying")
    assert not quote_in_text("Collateral Coverage Deficiency means, as of any date",
                             "\x93Collateral Coverage Ratio Failure\x94 means, as of any date")
    assert not quote_in_text("as modified hereby, if any Default o[ccurred]",
                             "as modified hereby, if any Default or Event of Default had occurred")


def test_quote_in_text_reads_through_a_clause_label_set_in_the_hanging_indent_column():
    from app.edgar.documents import quote_in_text

    text = ("       to be made under Section\xa06.05, any transaction specifically excluded    \n"
            "  (h)  from the definition                                                     \n"
            "       of Restricted Payment and the making of any Permitted Payment")
    assert quote_in_text("to be made under Section 6.05, any transaction specifically excluded "
                         "from the definition of Restricted Payment", text)
    assert quote_in_text("consenting to this Amendment in accordance with Section 9(b)",
                         "         the written consent of Alter Domus (US) LLC consenting to this  \n"
                         "  ii.    Amendment in accordance with Section 9(b) of the 2023 Incremental\n")
    # a label inside running text is the agreement's own wording
    assert not quote_in_text("specifically excluded from the definition",
                             "specifically excluded (h) from the definition")
