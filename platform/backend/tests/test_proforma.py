"""fulcrum.proforma.prime (Moyer ch. 9): the rank-0 priming transform — value shifts
off the unsecureds, purity, admin forwarding, and the ValueError 400 boundary."""
import numpy as np
import pytest

from app.fulcrum.proforma import prime
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
