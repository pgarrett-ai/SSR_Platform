"""Plotting: per-tranche recovery distributions.

A small grid of histograms, one per tranche (most-senior first), each showing the
recovery-as-percent-of-face distribution with median / P10 / P90 markers. The
fulcrum tranche is highlighted.
"""

from __future__ import annotations

import numpy as np

from .recovery import RecoveryResult


def plot_recovery_distributions(result: RecoveryResult, path: str | None = None):
    """Render recovery histograms. Returns the matplotlib Figure.

    Saves to `path` if given. Imports matplotlib lazily so the engine itself has
    no hard plotting dependency.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    order = result.structure.priority_order()
    face = {t.name: t.face for t in result.structure.tranches}
    entity_of = {t.name: t.entity for t in result.structure.tranches}

    n = len(order)
    ncols = min(3, n)
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.2 * ncols, 3.2 * nrows), squeeze=False)

    for i, name in enumerate(order):
        ax = axes[i // ncols][i % ncols]
        f = face[name]
        pct = 100 * (result.recoveries[name] / f if f > 0 else np.zeros_like(result.recoveries[name]))
        is_fulcrum = name == result.fulcrum
        color = "#c0392b" if is_fulcrum else "#34495e"

        ax.hist(np.clip(pct, 0, 100), bins=50, range=(0, 100), color=color, alpha=0.85)
        ax.axvline(np.median(pct), color="black", lw=1.5, label=f"median {np.median(pct):.0f}%")
        ax.axvline(np.percentile(pct, 10), color="gray", ls="--", lw=1, label="P10/P90")
        ax.axvline(np.percentile(pct, 90), color="gray", ls="--", lw=1)

        title = f"{name}  ({entity_of[name]})"
        ax.set_title(title + ("   <- FULCRUM" if is_fulcrum else ""),
                     fontsize=10, color=color, fontweight="bold" if is_fulcrum else "normal")
        ax.set_xlabel("recovery (% of face)")
        ax.set_ylabel("paths")
        ax.legend(fontsize=7)

    # hide any unused axes
    for j in range(n, nrows * ncols):
        axes[j // ncols][j % ncols].axis("off")

    fig.suptitle(
        f"{result.structure.name} - recovery distributions  (fulcrum: {result.fulcrum})",
        fontsize=13, fontweight="bold",
    )
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    if path:
        fig.savefig(path, dpi=130)
    return fig
