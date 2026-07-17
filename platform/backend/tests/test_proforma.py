"""fulcrum.proforma (Moyer ch. 9/11): the rank-0 priming transform — value shifts
off the unsecureds, purity, admin forwarding, the ValueError 400 boundary — and the
exchange-offer transform with its Boxco/Steelbox worked-example payoffs."""
import numpy as np
import pytest

from app.fulcrum.proforma import exchange, exchange_scenario, prime
from app.fulcrum.structure import CapitalStructure, Entity, Tranche
from app.fulcrum.waterfall import run_waterfall


def _base(**kw):
    return CapitalStructure(
        name="Base", entities=[Entity("OpCo", ev_share=1.0, parent=None)],
        tranches=[Tranche("1L Term Loan", "OpCo", face=300.0, lien_rank=1, secured=True),
                  Tranche("Unsecured Notes", "OpCo", face=200.0, secured=False)], **kw)


def test_priming_shifts_value_from_unsecureds():
    ev = np.array([400.0])
    s = _base()
    wf = run_waterfall(s, ev)
    assert wf["1L Term Loan"][0] == 300.0             # 100%
    assert wf["Unsecured Notes"][0] == 100.0          # 50%
    p = prime(s, 100.0)
    wfp = run_waterfall(p, ev)
    assert p.priority_order()[0] == "Priming loan"    # rank 0 pays first
    assert wfp["Priming loan"][0] == 100.0            # 100%
    assert wfp["1L Term Loan"][0] == 300.0            # still 100%
    assert wfp["Unsecured Notes"][0] == 0.0           # 0% — the Moyer point


def test_prime_is_pure_and_forwards_admin():
    s = _base(admin_fees=25.0, admin_pct=0.07)
    p = prime(s, 100.0, name="New money")
    assert len(s.tranches) == 2 and s.tranches[0].lien_rank == 1   # base untouched
    assert p.admin_fees == 25.0 and p.admin_pct == 0.07
    assert p.name.endswith("primed") and p.tranches[0].name == "New money"
    p.tranches[1].face = 999.0                        # fresh copies, not references
    assert s.tranches[0].face == 300.0


def test_nonpositive_face_and_unknown_entity_raise():
    with pytest.raises(ValueError):
        prime(_base(), 0.0)
    with pytest.raises(ValueError):
        prime(_base(), -50.0)
    with pytest.raises(ValueError):                   # validate() = the 400 boundary
        prime(_base(), 100.0, entity="Nowhere")


# ---- exchange offer (Moyer ch. 11) ------------------------------------------------


def _boxco():
    return CapitalStructure(
        name="Boxco", entities=[Entity("OpCo", ev_share=1.0, parent=None)],
        tranches=[Tranche("Unsecured Notes", "OpCo", face=700.0, secured=False)])


def test_boxco_full_participation_payoff():
    # Moyer ch. 11 Boxco: 700 unsecured exchange into priming paper at 14.2857 per 100
    # plus 98.3% of the equity to tendering holders; at EV 290 and p=100 the package
    # is worth 41.0 per 100 (new paper 14.3 + equity slice 26.7)
    out = exchange_scenario(_boxco(), "Unsecured Notes", np.array([290.0]),
                            ratio_pct=14.2857, participation_pct=100.0,
                            seniority="priming", equity_pct_at_full=98.3)
    assert out["stub_pct"] is None and out["holdout"] is None    # p=1 skips the stub
    assert abs(out["tender"][0] - 41.0) < 0.1


def test_boxco_partial_participation_holdout_vs_tender():
    # p = 650/700: pro-forma face 142.9; the 50 stub rides free behind the new paper
    # (holdout 100.0) while tendering is worth 36.5 — the holdout problem
    p = 100.0 * 650.0 / 700.0
    out = exchange_scenario(_boxco(), "Unsecured Notes", np.array([290.0]),
                            ratio_pct=14.2857, participation_pct=p,
                            seniority="priming", equity_pct_at_full=98.3)
    assert abs(out["structure"].total_face() - 142.9) < 0.1
    assert abs(out["holdout"][0] - 100.0) < 1e-9
    assert abs(out["tender"][0] - 36.5) < 0.1


def test_boxco_offer_fails_base_claim():
    # offer-fails state = the base structure on the clean 700 claim: EV 215 -> 30.7%
    wf = run_waterfall(_boxco(), np.array([215.0]))
    assert abs(100.0 * wf["Unsecured Notes"][0] / 700.0 - 30.7) < 0.1


def _steelbox():
    return CapitalStructure(
        name="Steelbox", entities=[Entity("OpCo", ev_share=1.0, parent=None)],
        tranches=[Tranche("Bank Term Loan", "OpCo", face=50.0, lien_rank=1,
                          secured=True),
                  Tranche("Senior Notes", "OpCo", face=150.0, secured=False)])


def test_steelbox_claim_status_second_lien_and_priming():
    # ratio 60, second lien, p=100: face 50 + 90 = 140 = 4.67x at EBITDA 30
    s2 = exchange(_steelbox(), "Senior Notes", ratio_pct=60.0,
                  participation_pct=100.0, seniority="second_lien")
    assert abs(s2.total_face() - 140.0) < 1e-9
    assert abs(s2.total_face() / 30.0 - 4.67) < 0.01
    new = next(t for t in s2.tranches if "exchange" in t.name)
    assert new.secured and new.lien_rank == 2               # behind the bank
    # priming variant recovers ahead of the bank
    sp = exchange(_steelbox(), "Senior Notes", ratio_pct=60.0,
                  participation_pct=100.0, seniority="priming")
    newp = next(t for t in sp.tranches if "exchange" in t.name)
    assert sp.priority_order()[0] == newp.name              # rank 0 pays first
    wfp = run_waterfall(sp, np.array([90.0]))
    assert wfp[newp.name][0] == 90.0 and wfp["Bank Term Loan"][0] == 0.0


def test_exchange_invariants():
    base = _boxco()
    grid = np.linspace(0.0, 1050.0, 241)
    # p=0 ≡ base on the same grid; no new tranche, tender undefined
    out0 = exchange_scenario(base, "Unsecured Notes", grid, ratio_pct=14.2857,
                             participation_pct=0.0, seniority="priming")
    wf = run_waterfall(base, grid)
    assert out0["new_pct"] is None and out0["tender"] is None
    assert np.allclose(out0["holdout"], 100.0 * wf["Unsecured Notes"] / 700.0)
    # exit consent: single-hop, same-entity subordination of the stub only
    s = exchange(_boxco(), "Unsecured Notes", ratio_pct=50.0, participation_pct=50.0,
                 seniority="unsecured", exit_consent=True)
    stub = next(t for t in s.tranches if t.name == "Unsecured Notes")
    new = next(t for t in s.tranches if "exchange" in t.name)
    assert stub.subordinated_to == new.name and new.subordinated_to is None
    assert stub.entity == new.entity
    # purity + admin forwarding + unique names
    withadmin = CapitalStructure(
        name="Boxco", entities=[Entity("OpCo", ev_share=1.0, parent=None)],
        tranches=[Tranche("Unsecured Notes", "OpCo", face=700.0, secured=False)],
        admin_fees=25.0, admin_pct=0.07)
    s2 = exchange(withadmin, "Unsecured Notes", ratio_pct=50.0,
                  participation_pct=50.0, seniority="second_lien")
    assert s2.admin_fees == 25.0 and s2.admin_pct == 0.07
    assert withadmin.tranches[0].face == 700.0              # base untouched
    names = [t.name for t in s2.tranches]
    assert len(names) == len(set(names))


def test_exchange_validation_raises():
    with pytest.raises(ValueError):
        exchange(_boxco(), "Nope", ratio_pct=50.0, participation_pct=50.0,
                 seniority="priming")
    with pytest.raises(ValueError):
        exchange(_boxco(), "Unsecured Notes", ratio_pct=50.0, participation_pct=120.0,
                 seniority="priming")
    with pytest.raises(ValueError):
        exchange(_boxco(), "Unsecured Notes", ratio_pct=-5.0, participation_pct=50.0,
                 seniority="priming")
    with pytest.raises(ValueError):
        exchange(_boxco(), "Unsecured Notes", ratio_pct=50.0, participation_pct=50.0,
                 seniority="mezzanine")


def test_coercion_tender_dominates_below_stub_entry():
    # exit consent + priming: below the EV where the (subordinated) stub first sees
    # value, tendering weakly dominates holding out — the coercion mechanic
    grid = np.linspace(0.0, 1050.0, 241)
    out = exchange_scenario(_boxco(), "Unsecured Notes", grid, ratio_pct=30.0,
                            participation_pct=90.0, seniority="priming",
                            equity_pct_at_full=50.0, exit_consent=True)
    stub, tender = out["holdout"], out["tender"]
    below = stub <= 1e-9
    assert below.any() and (~below).any()
    assert (tender[below] >= stub[below] - 1e-9).all()
    assert (tender[below] > stub[below]).any()
