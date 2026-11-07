"""Golden acceptance test for the merge: the shipped AAL hero snapshot must flow
capstack -> fulcrum adapter -> engine end-to-end in-process, with no HTTP and no
disk-path bridge. If this breaks, the module seam broke."""
import json
from pathlib import Path

from app.fulcrum import SimConfig, analyze, classify_seniority, overview_to_structure

CACHE = Path(__file__).resolve().parents[1] / "app" / "cache"


def _aal_overview() -> dict:
    return json.loads((CACHE / "AAL_3y.json").read_text(encoding="utf-8"))


def test_hero_snapshots_validate_through_merged_schema():
    from app.core.cache import load_overview

    for ticker in ("AAL", "ATUS", "TSE"):
        ov = load_overview(ticker, 3)
        assert ov is not None, f"{ticker} hero snapshot failed to load/validate"
        assert ov.header.from_cache is True


def test_hero_snapshots_seed_a_labelled_ebitda():
    # values move whenever the snapshots are rebuilt; the seam must always name its EBITDA
    from app.fulcrum.adapter import ltm_ebitda

    for ticker in ("AAL", "ATUS", "TSE", "LCID"):
        ov = json.loads((CACHE / f"{ticker}_3y.json").read_text(encoding="utf-8"))
        value, label = ltm_ebitda(ov)
        assert isinstance(value, float) and isinstance(label, str) and label, ticker
        assert overview_to_structure(ov)[1] == value, ticker


def test_classify_seniority():
    assert classify_seniority(None, None, "First Lien Term Loan") == (True, 1, False)
    assert classify_seniority("second lien", None, "Notes") == (True, 2, False)
    assert classify_seniority(None, None, "Convertible Preferred") == (False, 99, True)
    assert classify_seniority(None, None, "Senior Unsecured Notes") == (False, 99, False)
    # an explicit unsecured flag beats the facility-name patterns
    assert classify_seniority("senior unsecured", False,
                              "Senior unsecured revolving credit facility") == (False, 99, False)
    assert classify_seniority("subordinated", False, "Subordinated term loan") == (False, 99, False)
    # super-priority / DIP and receivables securitizations rank ahead of every lien (TSE)
    assert classify_seniority(None, None, "Super-Priority Revolver") == (True, 0, False)
    assert classify_seniority(None, None, "Accounts Receivable Securitization Facility") == \
        (True, 0, False)


def test_aal_snapshot_to_recovery():
    structure, ebitda, citations = overview_to_structure(_aal_overview())
    assert structure.tranches, "AAL hero snapshot should yield a cap table"
    assert all(t.face > 0 for t in structure.tranches)
    # drill-down: extracted tranches carry their filing citation, keyed by tranche name
    assert citations, "AAL debt schedule is cited — the adapter must pass citations through"
    assert set(citations) <= {t.name for t in structure.tranches}
    assert all(c.get("source_url") for c in citations.values())
    result = analyze(structure, SimConfig(base_ebitda=ebitda or 5000.0, n_draws=5000))
    table = result.table()
    assert len(table) == len(structure.tranches)
    # recoveries are percentages of face in [0, 100]
    assert (table["mean_recovery_%"] >= 0).all() and (table["mean_recovery_%"] <= 100).all()
