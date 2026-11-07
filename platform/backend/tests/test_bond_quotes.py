"""Per-issuer bond marks file: reader against the committed example, and absence."""
from pathlib import Path

from app.hazard import trace
from app.hazard.trace import get_issuer_bonds

EXAMPLE = Path(trace.__file__).resolve().parent / "data" / "bond_quotes.example.json"


def test_issuer_bonds_reader(monkeypatch):
    monkeypatch.setattr(trace, "_QUOTES_PATH", EXAMPLE)
    t = get_issuer_bonds("ticker")                      # case-insensitive
    assert t["enabled"] and t["as_of"] == "2026-10-31"
    b = t["bonds"][0]
    assert isinstance(b["last_yield"], float) and "rating" not in b
    none = get_issuer_bonds("ZZZZ")
    assert not none["enabled"] and "no marks" in none["note"]


def test_missing_marks_file_degrades(monkeypatch, tmp_path):
    monkeypatch.setattr(trace, "_QUOTES_PATH", tmp_path / "absent.json")
    out = get_issuer_bonds("AAL")
    assert out["enabled"] is False and "no marks file" in out["note"]
