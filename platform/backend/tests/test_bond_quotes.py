"""Per-issuer bond quotes drop file: reader + committed AAL seed record."""
from app.hazard.trace import get_issuer_bonds


def test_issuer_bonds_reader():
    aal = get_issuer_bonds("aal")                       # case-insensitive
    assert aal["enabled"] and len(aal["bonds"]) >= 1
    b = aal["bonds"][0]
    assert b["cusip"] == "00253XAB7"
    assert isinstance(b["last_yield"], float) and 0 < b["last_yield"] < 30
    assert b["maturity"] == "2029-04-20"

    none = get_issuer_bonds("ZZZZ")
    assert not none["enabled"] and "no scraped bonds" in none["note"]
