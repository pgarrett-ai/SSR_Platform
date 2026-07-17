"""Priority-attack transforms + claim-convention engine upgrades (Moyer ch. 7/12)."""
import numpy as np
import pytest

from app.fulcrum.attacks import apply_attack
from app.fulcrum.structure import CapitalStructure, Entity, Tranche
from app.fulcrum.waterfall import run_waterfall


def _structure(**kw):
    return CapitalStructure(
        name="T",
        entities=[Entity("OpCo", ev_share=1.0, parent="HoldCo"),
                  Entity("HoldCo", ev_share=0.0, parent=None)],
        tranches=[
            Tranche("1L", "OpCo", face=500.0, lien_rank=1, secured=True),
            Tranche("OpCo Unsec", "OpCo", face=200.0),
            Tranche("HoldCo Unsec", "HoldCo", face=150.0),
        ],
        **kw,
    )


def _rec(structure, ev):
    wf = run_waterfall(structure, np.array([float(ev)]))
    return {k: float(v[0]) for k, v in wf.items()}


def test_admin_pct_haircuts_ev():
    base = _rec(_structure(), 700.0)
    cut = _rec(_structure(admin_pct=0.07), 700.0)
    assert base["1L"] == cut["1L"] == 500.0
    assert base["OpCo Unsec"] == pytest.approx(200.0)
    # 700 × 0.93 = 651 -> 151 left after the 1L for the OpCo unsecured
    assert cut["OpCo Unsec"] == pytest.approx(151.0)
    assert cut["HoldCo Unsec"] == 0.0


def test_collateral_cap_and_deficiency():
    s = CapitalStructure(
        name="C", entities=[Entity("OpCo", 1.0)],
        tranches=[Tranche("1L", "OpCo", face=500.0, lien_rank=1, secured=True,
                          collateral_value=300.0),
                  Tranche("Unsec", "OpCo", face=200.0)])
    r = _rec(s, 1000.0)
    # secured step pays 300; the 200 deficiency shares the remaining 700 pari passu
    # with the 200 unsecured -> both claims paid in full here (700 > 400)
    assert r["1L"] == pytest.approx(500.0)
    assert r["Unsec"] == pytest.approx(200.0)
    r = _rec(s, 400.0)
    # 300 to the secured step; 100 left for 400 of pooled claims (200 deficiency + 200 unsec)
    assert r["1L"] == pytest.approx(300.0 + 100.0 * 200.0 / 400.0)
    assert r["Unsec"] == pytest.approx(100.0 * 200.0 / 400.0)


def test_subrogation_redirect():
    # Dead Co. (Moyer Table 7-1): assets 75; bank 50, trade 50, subs 100 subordinated
    # to the bank only. Pro rata 37.5% each; subs' 37.5 redirects until the bank is whole.
    s = CapitalStructure(
        name="DeadCo", entities=[Entity("OpCo", 1.0)],
        tranches=[Tranche("Bank", "OpCo", face=50.0),
                  Tranche("Trade", "OpCo", face=50.0),
                  Tranche("Subs", "OpCo", face=100.0, subordinated_to="Bank")])
    r = _rec(s, 75.0)
    assert r["Bank"] == pytest.approx(50.0)          # made whole via subrogation
    assert r["Trade"] == pytest.approx(18.75)        # unaffected (37.5%)
    assert r["Subs"] == pytest.approx(6.25)          # 37.5 - 31.25 redirected


def test_subordinated_to_validation():
    with pytest.raises(ValueError, match="unknown tranche"):
        CapitalStructure(name="X", entities=[Entity("OpCo", 1.0)],
                         tranches=[Tranche("A", "OpCo", 100.0, subordinated_to="Nope")])
    with pytest.raises(ValueError, match="different"):
        CapitalStructure(
            name="X",
            entities=[Entity("OpCo", 1.0, parent="HoldCo"), Entity("HoldCo", 0.0)],
            tranches=[Tranche("A", "OpCo", 100.0, subordinated_to="B"),
                      Tranche("B", "HoldCo", 100.0)])


def test_lien_avoidance():
    s = _structure()
    attacked = apply_attack(s, "lien_avoidance")
    base, hit = _rec(s, 400.0), _rec(attacked, 400.0)
    assert base["1L"] == 400.0                        # secured takes everything
    assert hit["1L"] < base["1L"]                     # now pari passu with unsecured
    assert hit["OpCo Unsec"] > base["OpCo Unsec"]


def test_equitable_subordination():
    s = _structure()
    attacked = apply_attack(s, "equitable_subordination", target="1L")
    r = _rec(attacked, 400.0)
    # 1L now pays after all debt AT ITS ENTITY (preferred slot): OpCo unsecured first,
    # then the demoted 1L; HoldCo paper only sees residual OpCo equity (none here).
    assert r["OpCo Unsec"] == pytest.approx(200.0)
    assert r["1L"] == pytest.approx(200.0)
    assert r["HoldCo Unsec"] == 0.0


def test_substantive_consolidation():
    s = _structure()
    base = _rec(s, 600.0)
    attacked = apply_attack(s, "substantive_consolidation")
    r = _rec(attacked, 600.0)
    # structural subordination vanishes: HoldCo + OpCo unsecured now share pro rata
    assert base["HoldCo Unsec"] < base["OpCo Unsec"]
    assert r["HoldCo Unsec"] / 150.0 == pytest.approx(r["OpCo Unsec"] / 200.0)


def test_guarantee_invalidation_descoped():
    with pytest.raises(ValueError, match="not modeled"):
        apply_attack(_structure(), "guarantee_invalidation")
