"""API hardening (PR-A): the ticker path-traversal guard and the resource-exhaustion input
bounds. Pure/unit level — no network, no TestClient (endpoint-level 400/422 checks live in
test_recovery_api.py)."""
import pytest

from app.capstack.tax382 import tax_asset_pv
from app.core.cache import cache_path, load_overview, safe_ticker
from app.core.config import CACHE_DIR
from app.fulcrum.simulate import SimConfig
from app.fulcrum.structure import CapitalStructure, Entity, Tranche


def test_safe_ticker_accepts_real_symbols():
    assert safe_ticker(" aal ") == "AAL"        # normalizes strip/upper (cache key unchanged)
    assert safe_ticker("BRK.A") == "BRK.A"       # dot for class shares
    assert safe_ticker("brk-a") == "BRK-A"       # hyphen
    assert safe_ticker("CIK0001811210") == "CIK0001811210"   # resolve_company accepts raw/CIK forms


@pytest.mark.parametrize("bad", [
    "../etc/passwd", "..\\..\\x", "a/b", "a\\b", "..", "...", "", "   ", "a b",
    "toolongtickername",  # 17 chars > 16
])
def test_safe_ticker_rejects_traversal_and_junk(bad):
    with pytest.raises(ValueError):
        safe_ticker(bad)


def test_cache_path_rejects_traversal_and_stays_in_dir():
    with pytest.raises(ValueError):
        cache_path("../../../x", 3)
    # a valid ticker resolves to a path that never escapes CACHE_DIR
    assert cache_path("AAL", 3).resolve().parent == CACHE_DIR.resolve()


def test_load_overview_invalid_ticker_degrades_to_none():
    # graceful: an invalid ticker is treated as "no cache" (no raise, no traversal); the
    # live path then resolves it and 404s cleanly.
    assert load_overview("../../etc/x", 3) is None


def test_simconfig_n_draws_bounded():
    with pytest.raises(ValueError):
        SimConfig(base_ebitda=100.0, n_draws=10**9)
    SimConfig(base_ebitda=100.0, n_draws=50_000)   # normal request is fine


def _struct(n_tranches=1, n_entities=1):
    ents = [Entity(f"E{i}", ev_share=(1.0 if i == 0 else 0.0),
                   parent=None if i == 0 else "E0") for i in range(n_entities)]
    trs = [Tranche(f"T{i}", "E0", face=1.0) for i in range(n_tranches)]
    return CapitalStructure(name="X", entities=ents, tranches=trs)


def test_structure_caps_entity_and_tranche_counts():
    with pytest.raises(ValueError):
        _struct(n_tranches=501).validate()
    with pytest.raises(ValueError):
        _struct(n_entities=201).validate()
    _struct(n_tranches=10, n_entities=3).validate()   # a real cap table is well under the caps


def test_tax_pv_horizon_clamped_no_hang():
    # a huge horizon with a tiny-but-positive limit must stay bounded (loop clamp), not hang
    pv = tax_asset_pv(1e18, 1.0, 0.21, 10**9, 0.10)
    assert 0.0 <= pv < 1e18


def test_simconfig_rejects_nonfinite_base_ebitda():
    # NaN evades a bare `<= 0` (all NaN comparisons are False) — must be caught by isfinite
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            SimConfig(base_ebitda=bad, n_draws=1_000)


def test_body_models_reject_inf_nan():
    from pydantic import ValidationError

    from app.main import Tax382Body
    for bad in (float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            Tax382Body(equity_fmv=bad)
