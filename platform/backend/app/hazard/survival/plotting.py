"""Reporting plots for a trained hazard model.

One multi-panel figure: model discrimination/calibration comparison, example
survival curves, recovery of the true risk drivers, and a calibration check.
"""

from __future__ import annotations

import numpy as np

from .data import TRUE_BETA
from .features import FEATURES


def plot_report(results_df, fitted, X_test, y_test, times, feature_names, path=None):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    best_name = results_df.iloc[0]["model"]
    best = fitted[best_name]

    fig, axes = plt.subplots(2, 3, figsize=(16, 9))

    # A) C-index and mean AUC by model
    ax = axes[0][0]
    order = results_df["model"].tolist()
    y = np.arange(len(order))
    ax.barh(y - 0.2, results_df["c_index"], height=0.38, color="#2c3e50", label="C-index")
    ax.barh(y + 0.2, results_df["mean_auc"], height=0.38, color="#7f8c8d", label="mean AUC")
    ax.set_yticks(y)
    ax.set_yticklabels(order, fontsize=8)
    ax.invert_yaxis()
    ax.axvline(0.5, color="red", ls=":", lw=1)
    ax.set_xlim(0.4, 1.0)
    ax.set_title("Discrimination (higher = better)", fontsize=10, fontweight="bold")
    ax.legend(fontsize=7)

    # B) Integrated Brier Score by model (lower = better)
    ax = axes[0][1]
    ibs_sorted = results_df.sort_values("ibs")
    ax.barh(ibs_sorted["model"], ibs_sorted["ibs"], color="#16a085")
    ax.invert_yaxis()
    ax.set_title("Integrated Brier Score (lower = better)", fontsize=10, fontweight="bold")
    ax.tick_params(labelsize=8)

    # C) Example survival curves: low / median / high predicted risk
    ax = axes[0][2]
    risk = best.risk(X_test)
    pct = {"low risk (P10)": 10, "median (P50)": 50, "high risk (P90)": 90}
    colors = {"low risk (P10)": "#27ae60", "median (P50)": "#f39c12", "high risk (P90)": "#c0392b"}
    grid = np.linspace(times.min(), times.max(), 40)
    for label, p in pct.items():
        idx = int(np.argmin(np.abs(risk - np.percentile(risk, p))))
        surv = best.survival(X_test[idx:idx + 1], grid)[0]
        ax.plot(grid, surv, label=label, color=colors[label], lw=2)
    ax.set_ylim(0, 1)
    ax.set_xlabel("years")
    ax.set_ylabel("survival probability")
    ax.set_title(f"Survival curves - {best_name}", fontsize=10, fontweight="bold")
    ax.legend(fontsize=7)

    # D) Cox standardized coefficients vs the true betas (validation)
    ax = axes[1][0]
    if "Cox PH" in fitted:
        cox = fitted["Cox PH"]
        coefs = cox.coefficients()
        est = [coefs[n] for n in feature_names]
        true = [TRUE_BETA[f] for f in FEATURES]
        yy = np.arange(len(FEATURES))
        ax.barh(yy - 0.2, true, height=0.38, color="#bdc3c7", label="true beta")
        ax.barh(yy + 0.2, est, height=0.38, color="#2980b9", label="Cox estimate")
        ax.set_yticks(yy)
        ax.set_yticklabels(FEATURES, fontsize=7)
        ax.invert_yaxis()
        ax.axvline(0, color="black", lw=0.8)
        ax.set_title("Recovered risk drivers (Cox vs truth)", fontsize=10, fontweight="bold")
        ax.legend(fontsize=7)

    # E) Calibration of the best model at a 5y horizon
    ax = axes[1][1]
    t_cal = min(5.0, times.max())
    pd_pred = 1.0 - best.survival(X_test, [t_cal])[:, 0]
    dur = y_test["time"]
    evt = y_test["event"].astype(bool)
    defaulted_by = evt & (dur <= t_cal)
    known = defaulted_by | (dur >= t_cal)  # exclude firms censored before t_cal
    pp = pd_pred[known]
    oo = defaulted_by[known].astype(float)
    if len(pp) > 50:
        bins = np.quantile(pp, np.linspace(0, 1, 11))
        bins[-1] += 1e-9
        idx = np.clip(np.digitize(pp, bins) - 1, 0, 9)
        xs = [pp[idx == b].mean() for b in range(10) if (idx == b).any()]
        ys = [oo[idx == b].mean() for b in range(10) if (idx == b).any()]
        ax.plot([0, 1], [0, 1], ls="--", color="gray", lw=1)
        ax.plot(xs, ys, "o-", color="#8e44ad")
    ax.set_xlabel(f"predicted {t_cal:g}y PD")
    ax.set_ylabel(f"observed {t_cal:g}y default rate")
    ax.set_title("Calibration (best model)", fontsize=10, fontweight="bold")

    # F) Risk separation: percentile-ranked risk (so skewed scores stay readable)
    ax = axes[1][2]
    order = np.argsort(risk)
    ranks = np.empty(len(risk))
    ranks[order] = np.arange(len(risk))
    pctile = 100 * ranks / max(len(risk) - 1, 1)
    ax.hist(pctile[~evt], bins=30, alpha=0.6, color="#27ae60", label="censored/alive", density=True)
    ax.hist(pctile[evt], bins=30, alpha=0.6, color="#c0392b", label="defaulted", density=True)
    ax.set_xlabel("predicted risk percentile")
    ax.set_title("Risk separation (best model)", fontsize=10, fontweight="bold")
    ax.legend(fontsize=7)

    fig.suptitle("Hazard - corporate default survival model", fontsize=14, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    if path:
        fig.savefig(path, dpi=130)
    return fig
