"""Deterministic waterfall tests.

These feed a *constant* enterprise value (no simulation noise) so every recovery
is an exact hand-checkable number. Run with `pytest` or `python -m tests.test_waterfall`.
"""

from __future__ import annotations

import numpy as np

from app.fulcrum import CapitalStructure, Entity, Tranche
from app.fulcrum.waterfall import allocate_entity, run_waterfall


def _const(x: float, n: int = 5) -> np.ndarray:
    return np.full(n, float(x))


def test_single_entity_full_coverage():
    # EV 1000 covers 1L 500 + 2L 250 + unsec 120 fully; 130 of equity remains.
    tranches = [
        Tranche("1L", "Co", 500, lien_rank=1, secured=True),
        Tranche("2L", "Co", 250, lien_rank=2, secured=True),
        Tranche("Unsec", "Co", 120, secured=False),
    ]
    recs, equity = allocate_entity(_const(1000), tranches)
    assert np.allclose(recs["1L"], 500)
    assert np.allclose(recs["2L"], 250)
    assert np.allclose(recs["Unsec"], 120)
    assert np.allclose(equity, 130)


def test_secured_priority_breaks_at_2L():
    # EV 600: 1L full (500), 2L gets the residual 100, unsecured gets 0.
    tranches = [
        Tranche("1L", "Co", 500, lien_rank=1, secured=True),
        Tranche("2L", "Co", 250, lien_rank=2, secured=True),
        Tranche("Unsec", "Co", 120, secured=False),
    ]
    recs, equity = allocate_entity(_const(600), tranches)
    assert np.allclose(recs["1L"], 500)
    assert np.allclose(recs["2L"], 100)
    assert np.allclose(recs["Unsec"], 0)
    assert np.allclose(equity, 0)


def test_unsecured_pro_rata():
    # EV 560, fees 0. 1L is made whole (500). The remaining 60 funds the
    # unsecured pool: UnsecA 100 + UnsecB 300 = 400 of claims, so each recovers
    # 60/400 = 15% of face. Nothing left for equity.
    tranches = [
        Tranche("1L", "Co", 500, lien_rank=1, secured=True),
        Tranche("UnsecA", "Co", 100, secured=False),
        Tranche("UnsecB", "Co", 300, secured=False),
    ]
    recs, equity = allocate_entity(_const(560), tranches)
    assert np.allclose(recs["1L"], 500)
    assert np.allclose(recs["UnsecA"], 15)  # 15% of 100
    assert np.allclose(recs["UnsecB"], 45)  # 15% of 300
    assert np.allclose(equity, 0)


def test_junior_secured_paid_before_unsecured():
    # EV 560, fees 0. 1L full (500); the residual 60 goes to the 2L (secured,
    # lien 2) before any unsecured claim, so 2L = 60 and unsecured gets nothing.
    tranches = [
        Tranche("1L", "Co", 500, lien_rank=1, secured=True),
        Tranche("2L", "Co", 200, lien_rank=2, secured=True),
        Tranche("Unsec", "Co", 300, secured=False),
    ]
    recs, equity = allocate_entity(_const(560), tranches)
    assert np.allclose(recs["1L"], 500)
    assert np.allclose(recs["2L"], 60)
    assert np.allclose(recs["Unsec"], 0)
    assert np.allclose(equity, 0)


def test_admin_fees_are_senior():
    # Fees 50 come out first; 1L then gets 450 of its 500 face.
    tranches = [Tranche("1L", "Co", 500, lien_rank=1, secured=True)]
    recs, equity = allocate_entity(_const(500), tranches, fees=50.0)
    assert np.allclose(recs["1L"], 450)
    assert np.allclose(equity, 0)


def test_structural_subordination():
    # OpCo holds 90% of EV with a 600 first lien; HoldCo holds 10% with 150 unsec.
    # EV 1000 -> opco value 900 (net of fees 0). 1L paid 600, opco residual 300
    # upstreams to holdco. HoldCo value = 100 (own) + 300 = 400; its 150 unsec is
    # paid in full, leaving 250 of equity. Structural priority means opco's lien
    # is satisfied before holdco's residual even exists.
    entities = [
        Entity("OpCo", ev_share=0.90, parent="HoldCo"),
        Entity("HoldCo", ev_share=0.10, parent=None),
    ]
    tranches = [
        Tranche("OpCo 1L", "OpCo", 600, lien_rank=1, secured=True),
        Tranche("HoldCo Unsec", "HoldCo", 150, secured=False),
    ]
    cs = CapitalStructure("T", entities, tranches, admin_fees=0.0)
    recs = run_waterfall(cs, _const(1000))
    assert np.allclose(recs["OpCo 1L"], 600)
    assert np.allclose(recs["HoldCo Unsec"], 150)


def test_structural_subordination_traps_value():
    # Same structure but EV only 500. OpCo value 450 -> 1L recovers 450 (short of
    # 600). Nothing upstreams. HoldCo has only its own 50 of value for its 150
    # unsec -> 50 recovery. The holdco note is structurally subordinated even
    # though opco's 1L is itself impaired.
    entities = [
        Entity("OpCo", ev_share=0.90, parent="HoldCo"),
        Entity("HoldCo", ev_share=0.10, parent=None),
    ]
    tranches = [
        Tranche("OpCo 1L", "OpCo", 600, lien_rank=1, secured=True),
        Tranche("HoldCo Unsec", "HoldCo", 150, secured=False),
    ]
    cs = CapitalStructure("T", entities, tranches, admin_fees=0.0)
    recs = run_waterfall(cs, _const(500))
    assert np.allclose(recs["OpCo 1L"], 450)
    assert np.allclose(recs["HoldCo Unsec"], 50)


def test_pari_passu_secured_share_pro_rata():
    # Two 1L tranches (300 + 100 = 400 of claims) against only 200 of value:
    # pari passu, so each recovers 50% of face - NOT first-in-list-order.
    tranches = [
        Tranche("1L-A", "Co", 300, lien_rank=1, secured=True),
        Tranche("1L-B", "Co", 100, lien_rank=1, secured=True),
    ]
    recs, equity = allocate_entity(_const(200), tranches)
    assert np.allclose(recs["1L-A"], 150)
    assert np.allclose(recs["1L-B"], 50)
    assert np.allclose(equity, 0)


def test_preferred_after_debt_before_equity():
    # EV 700: 1L 500 full, unsec 100 full, preferred 80 full, 20 of equity left.
    tranches = [
        Tranche("1L", "Co", 500, lien_rank=1, secured=True),
        Tranche("Unsec", "Co", 100, secured=False),
        Tranche("Pref", "Co", 80, preferred=True),
    ]
    recs, equity = allocate_entity(_const(700), tranches)
    assert np.allclose(recs["Pref"], 80)
    assert np.allclose(equity, 20)

    # EV 550: unsec pool only gets 50; preferred and equity get zero.
    recs, equity = allocate_entity(_const(550), tranches)
    assert np.allclose(recs["Unsec"], 50)
    assert np.allclose(recs["Pref"], 0)
    assert np.allclose(equity, 0)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
