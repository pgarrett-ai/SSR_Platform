"""No-network checks for the scoring core. Run: python -m tests.test_core (or pytest)."""
from __future__ import annotations

from app.hazard.merton import _self_check as merton_check
from app.hazard.score import AltmanZScore


def test_merton():
    merton_check()   # asserts inside; covers DD/PD sanity, monotonicity, term structure


def test_altman_value():
    # Healthy-ish firm: 6.56·0.30 + 3.26·0.40 + 6.72·0.12 + 1.05·1.5 = 5.65
    feats = {"wc_to_assets": 0.30, "re_to_assets": 0.40, "ebit_to_assets": 0.12,
             "equity_to_liabilities": 1.5}
    sc = AltmanZScore().score(feats)
    assert sc["available"] and sc["zone"] == "safe", sc
    assert sc["value"] == 5.65, sc


def test_altman_zones():
    s = AltmanZScore()
    distressed = {"wc_to_assets": -0.20, "re_to_assets": -0.50,
                  "ebit_to_assets": -0.10, "equity_to_liabilities": 0.05}
    assert s.score(distressed)["zone"] == "distress"


def test_altman_mid_distress_reads_distress():
    # Z'' = 0 − 0.326 + 0.202 + 0.315 = 0.19: distress under the classic cutoffs. The +3.25
    # EM constant would print 3.44 and call it "safe".
    mid = {"wc_to_assets": 0.0, "re_to_assets": -0.10, "ebit_to_assets": 0.03,
           "equity_to_liabilities": 0.30}
    sc = AltmanZScore().score(mid)
    assert sc["value"] == 0.19 and sc["zone"] == "distress", sc


def test_altman_missing_inputs():
    assert AltmanZScore().score({"wc_to_assets": 0.1})["available"] is False


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
