"""Annotation matching (exact / alias / token containment) + gap-fill application.
Pure logic, no network, no LLM."""
from __future__ import annotations

from types import SimpleNamespace

from app.capstack.debt_schedule import _apply_annotations, match_annotation
from app.schemas import DebtInstrument


def _inst(name, member=None, **kw):
    return DebtInstrument(instrument=name, xbrl_member=member, **kw)


def _by_name(*anns):
    return {a["instrument"].strip().lower(): a for a in anns}


def test_match_exact_beats_everything():
    by = _by_name({"instrument": "2030 Notes", "maturity": "2030"})
    assert match_annotation(_inst("2030 Notes"), by)["maturity"] == "2030"


def test_match_via_learned_alias():
    by = _by_name({"instrument": "5.00% Convertible Senior Notes due 2030", "maturity": "2030"})
    aliases = {"lcid:A2030NotesMember": ["5.00% Convertible Senior Notes due 2030"]}
    got = match_annotation(_inst("2030 Notes", "lcid:A2030NotesMember"), by, aliases)
    assert got is not None and got["maturity"] == "2030"


def test_match_token_containment_unambiguous_only():
    by = _by_name(
        {"instrument": "5.00% Convertible Senior Notes due 2030", "maturity": "April 1, 2030"},
        {"instrument": "7.00% Convertible Senior Notes due 2031", "maturity": "2031"},
    )
    got = match_annotation(_inst("2030 Notes"), by)
    assert got is not None and got["maturity"] == "April 1, 2030"
    # 'Notes' alone is contained in BOTH prose names -> ambiguous -> no match
    assert match_annotation(_inst("Notes"), by) is None


def test_apply_annotations_learns_fuzzy_aliases_and_respects_asof():
    ft = SimpleNamespace(accession_no="a", form_type="10-Q", filing_date="2026-05-05",
                         period_of_report="2026-03-31", source_url="u")
    insts = [
        _inst("2030 Notes", "lcid:A2030NotesMember"),
        _inst("Old Notes", "lcid:OldMember"),
    ]
    anns = [
        {"instrument": "5.00% Convertible Senior Notes due 2030",
         "maturity": "April 1, 2030", "quote": "will mature on April 1, 2030"},
        {"instrument": "Old Notes", "maturity": "2019", "quote": "matured 2019"},
    ]
    learned = _apply_annotations(insts, anns, ft, asof="2026-03-31")
    assert insts[0].maturity == "April 1, 2030"
    assert insts[0].citation.quote == "will mature on April 1, 2030"
    assert insts[1].maturity is None            # pre-as-of year rejected (carrying > 0)
    assert ("lcid:A2030NotesMember", "5.00% Convertible Senior Notes due 2030") in learned
    assert all(m != "lcid:OldMember" for m, _ in learned)   # exact match learns nothing
