"""No-network checks for the scoring core. Run: python -m tests.test_core (or pytest)."""
from __future__ import annotations

from app.hazard.merton import _self_check as merton_check
from app.hazard.score import AltmanZScore, CHSHazard
from app.hazard.trace import _breadth, _signal


def test_merton():
    merton_check()   # asserts inside; covers DD/PD sanity, monotonicity, term structure


def test_altman_value_and_contributions_sum():
    # Healthy-ish firm: contributions + baseline must reconstruct the score exactly.
    feats = {"wc_to_assets": 0.30, "re_to_assets": 0.40, "ebit_to_assets": 0.12,
             "equity_to_liabilities": 1.5}
    s = AltmanZScore()
    sc = s.score(feats)
    assert sc["available"] and sc["zone"] == "safe", sc
    contrib = s.contributions(feats)
    assert abs(sum(contrib.values()) - sc["value"]) < 0.01, (sum(contrib.values()), sc["value"])


def test_altman_zones():
    s = AltmanZScore()
    distressed = {"wc_to_assets": -0.20, "re_to_assets": -0.50,
                  "ebit_to_assets": -0.10, "equity_to_liabilities": 0.05}
    assert s.score(distressed)["zone"] == "distress"


def test_altman_missing_inputs():
    assert AltmanZScore().score({"wc_to_assets": 0.1})["available"] is False


def test_chs_logit_contributions_sum():
    # CHS contributions + baseline must reconstruct the logit exactly.
    class M:
        ok = True; market_cap = 5e9; equity_vol = 0.6; price = 8.0; excess_return_1y = -0.2
    feats = {"total_liabilities": 8e9, "net_income": -3e8, "cash": 4e8, "book_equity": 2e9}
    s = CHSHazard()
    sc = s.score(feats, M())
    assert sc["available"], sc
    contrib = s.contributions(feats, M())
    assert abs(sum(contrib.values()) - sc["logit"]) < 1e-2, (sum(contrib.values()), sc["logit"])


def test_trace_breadth_and_signal():
    assert _breadth(60, 40) == 0.6
    assert _breadth(0, 0) is None
    assert _signal(0.30) == "risk-off"
    assert _signal(0.50) == "neutral"
    assert _signal(0.70) == "risk-on"
    assert _signal(None) == "neutral"


def test_trained_hazard_framework():
    # Plumbing only: trains on a SYNTHETIC fixture (not a real metric). Verifies the training +
    # walk-forward + Scorer-interface plumbing works and that monotonic constraints hold.
    from app.hazard.train import _synthetic_panel, train_from_panel
    from app.hazard.score import TrainedHazardScorer
    df = _synthetic_panel(seed=1)
    aucs, bundle = train_from_panel(df, save=False)
    assert aucs and max(aucs.values()) > 0.6, aucs   # plumbing learns the known synthetic signal
    s = TrainedHazardScorer(bundle)
    healthy = {"leverage": 0.20, "net_debt_to_ebitda": 1.0, "interest_coverage": 12.0,
               "current_ratio": 2.2, "quick_ratio": 1.8, "cash_ratio": 0.30, "roa": 0.10,
               "fcf_margin": 0.10, "wc_to_assets": 0.30, "re_to_assets": 0.40, "size_log_assets": 9.0}
    distressed = {"leverage": 0.90, "net_debt_to_ebitda": 8.0, "interest_coverage": 0.8,
                  "current_ratio": 0.7, "quick_ratio": 0.5, "cash_ratio": 0.03, "roa": -0.05,
                  "fcf_margin": -0.10, "wc_to_assets": -0.10, "re_to_assets": -0.20, "size_log_assets": 6.0}
    ph, pdd = s.score(healthy)["value"], s.score(distressed)["value"]
    assert 0 <= ph <= 1 and 0 <= pdd <= 1
    assert pdd > ph, (ph, pdd)   # monotonic constraints => worse fundamentals never lower hazard


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("\nall core checks passed.")


if __name__ == "__main__":
    _run_all()
