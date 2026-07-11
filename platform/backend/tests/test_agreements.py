"""Agreement families: deterministic preamble parsing, grouping, instrument mapping."""
from __future__ import annotations

from app.capstack.agreements import group_families, map_instruments, parse_doc_head
from app.capstack.covenants import CreditDoc
from app.schemas import DebtInstrument


def _doc(text, doc_class="credit_agreement", accession="0001-26-000001",
         filing_date="2026-01-15", exhibit="EX-10.1"):
    return CreditDoc(accession=accession, form_type="8-K", filing_date=filing_date,
                     exhibit_type=exhibit, url=None, doc_class=doc_class,
                     title=" ".join(text[:120].split()), text=text)


BASE_CA = ("AMENDED AND RESTATED CREDIT AGREEMENT dated as of June 27, 2013, among "
           "AMERICAN AIRLINES, INC., as borrower, the lenders party hereto, and "
           "CITIBANK, N.A., as Administrative Agent" + " x" * 3000)

AMENDMENT = ("ELEVENTH AMENDMENT TO AMENDED AND RESTATED CREDIT AGREEMENT, dated as of "
             "March 10, 2026, to the Credit Agreement dated as of June 27, 2013, among "
             "AMERICAN AIRLINES, INC. and CITIBANK, N.A., as Administrative Agent" + " x" * 3000)

INDENTURE = ("INDENTURE, dated as of April 1, 2021, governing the 5.75% Senior Secured Notes "
             "due 2029, among the issuer and WILMINGTON TRUST, NATIONAL ASSOCIATION, as "
             "Trustee and Collateral Trustee" + " x" * 3000)


def test_parse_doc_head():
    base = parse_doc_head(_doc(BASE_CA))
    assert base.amendment_no is None and base.amended_restated
    assert base.dated == "June 27, 2013"
    assert "CITIBANK" in base.roles["admin_agent"]

    amd = parse_doc_head(_doc(AMENDMENT, filing_date="2026-03-11"))
    assert amd.amendment_no == 11
    assert amd.base_date == "June 27, 2013"

    ind = parse_doc_head(_doc(INDENTURE, doc_class="indenture", exhibit="EX-4.1"))
    assert ind.note_coupon == 5.75 and ind.note_due == 2029
    assert "WILMINGTON TRUST" in ind.roles["trustee"]


def test_group_families_base_plus_amendment():
    fams = group_families([
        _doc(BASE_CA, accession="0001-19-000001", filing_date="2019-06-27"),
        _doc(AMENDMENT, accession="0001-26-000002", filing_date="2026-03-11"),
        _doc(INDENTURE, doc_class="indenture", accession="0001-21-000003",
             filing_date="2021-04-01", exhibit="EX-4.1"),
    ])
    assert len(fams) == 2
    ca = next(f for f in fams if f.doc_class == "credit_agreement")
    assert ca.operative.amended_restated and not ca.base_missing
    assert len(ca.amendments) == 1 and ca.amendments[0].amendment_no == 11
    assert "June 27, 2013" in ca.label

    notes = next(f for f in fams if f.doc_class == "indenture")
    assert notes.label == "5.75% notes due 2029"


def test_amendment_only_family_flags_base_missing():
    fams = group_families([_doc(AMENDMENT)])   # short text: doesn't embed the restatement
    assert len(fams) == 1
    assert fams[0].base_missing is True


def test_map_instruments():
    fams = group_families([
        _doc(BASE_CA, accession="a1", filing_date="2019-06-27"),
        _doc(INDENTURE, doc_class="indenture", accession="a2", filing_date="2021-04-01"),
    ])
    instruments = [
        DebtInstrument(instrument="2013 Term Loan Facility"),
        DebtInstrument(instrument="5.75% Senior Notes", coupon_pct=5.75, maturity="April 2029"),
        DebtInstrument(instrument="Payroll Support Program Promissory Note One"),
        DebtInstrument(instrument="Enhanced Equipment Trust Certificates (EETC)"),
        DebtInstrument(instrument="8.50% Senior Notes", coupon_pct=8.5),
    ]
    mapping = map_instruments(fams, instruments)
    assert "June 27, 2013" in mapping["2013 Term Loan Facility"]
    assert mapping["5.75% Senior Notes"] == "5.75% notes due 2029"
    assert instruments[1].governed_by == "5.75% notes due 2029"
    assert "Treasury" in mapping["Payroll Support Program Promissory Note One"]
    assert mapping["Enhanced Equipment Trust Certificates (EETC)"] is None
    assert mapping["8.50% Senior Notes"] is None      # no matching indenture on file
    fam = next(f for f in fams if f.doc_class == "indenture")
    assert fam.governs_instruments == ["5.75% Senior Notes"]
