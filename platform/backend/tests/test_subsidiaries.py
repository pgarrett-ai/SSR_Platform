"""Phase 4.5: Exhibit 21 subsidiary normalization (pure; no network)."""
from __future__ import annotations

from app.capstack.subsidiaries import coerce_subsidiaries
from app.schemas import Citation


def test_coerce_dedups_cleans_and_parses_percent():
    raw = [
        {"name": "  American Airlines, Inc. ", "jurisdiction": "Delaware", "percent_owned": "100"},
        {"name": "American Airlines, Inc.", "jurisdiction": "Delaware"},   # dup (case/space)
        {"name": "Envoy Aviation Group Inc.", "jurisdiction": "Delaware", "parent": "AAG"},
        {"name": "", "jurisdiction": "Texas"},                            # empty name dropped
        {"name": "Piedmont Airlines", "percent_owned": "not-a-number"},   # bad pct -> None
    ]
    subs = coerce_subsidiaries(raw)
    assert [s.name for s in subs] == ["American Airlines, Inc.", "Envoy Aviation Group Inc.",
                                      "Piedmont Airlines"]
    assert subs[0].percent_owned == 100.0
    assert subs[1].parent == "AAG"
    assert subs[2].percent_owned is None
    assert subs[0].jurisdiction == "Delaware"


def test_coerce_attaches_citation_and_caps():
    cit = Citation(accession_no="x", exhibit="EX-21", source_url="http://sec.gov/ex21")
    raw = [{"name": f"Sub {i}"} for i in range(50)]
    subs = coerce_subsidiaries(raw, citation=cit, cap=10)
    assert len(subs) == 10
    assert all(s.citation is cit for s in subs)


def test_coerce_blank_jurisdiction_becomes_none():
    subs = coerce_subsidiaries([{"name": "Co", "jurisdiction": "   ", "parent": ""}])
    assert subs[0].jurisdiction is None and subs[0].parent is None


def test_assign_roles_matches_xbrl_obligors():
    from app.capstack.subsidiaries import assign_roles
    from app.schemas import DebtInstrument, Subsidiary

    subs = [
        Subsidiary(name="American Airlines, Inc."),
        Subsidiary(name="Envoy Aviation Group Inc."),
        Subsidiary(name="AAdvantage Loyalty IP Ltd."),
    ]
    instruments = [
        DebtInstrument(instrument="5.75% Senior Notes", obligor="AmericanAirlinesIncMember"),
        DebtInstrument(instrument="2021 AAdvantage Term Loan Facility",
                       obligor="AAdvantageLoyaltyIPLtdMember"),
        DebtInstrument(instrument="PSP1 Promissory Note"),   # no obligor tagged
    ]
    assign_roles(subs, instruments, issuer_name="American Airlines Group Inc.")
    assert subs[0].role == "debt obligor"
    assert subs[0].instruments == ["5.75% Senior Notes"]
    assert subs[1].role is None
    assert subs[2].role == "debt obligor"
    assert "AAdvantage" in subs[2].instruments[0]
