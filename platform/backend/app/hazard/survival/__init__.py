"""Hazard - a time-to-default survival model for corporate credit.

Frames corporate default as a survival problem (time until Chapter 11, with
right-censoring for firms that are still alive or exit for other reasons) and
fits a panel of survival models - from an interpretable Cox model to
gradient-boosted survival ensembles - evaluated with the metrics that actually
matter for credit: concordance (C-index), the Integrated Brier Score
(calibration), and time-dependent AUC.

The same engineering pattern (survival / hazard modeling, C-index + Brier
evaluation) that drove the customer-churn work at NinjaTrader, pointed at credit
default instead of churn.
"""

__all__ = [
    "FEATURES",
    "LOG_FEATURES",
    "build_model_matrix",
    "split_by_firm",
    "make_surv",
    "PanelConfig",
    "TRUE_BETA",
    "generate_panel",
    "CoxModel",
    "WeibullAFTModel",
    "RandomForestModel",
    "GradientBoostedModel",
    "DiscreteTimeHazardModel",
    "all_models",
    "evaluate_model",
    "compare_models",
    "time_grid",
    "score_firm",
]

__version__ = "0.1.0"

# PEP 562 lazy re-exports. features.py/models.py/evaluate.py import sksurv/lifelines at
# module top, and a package's __init__ always runs before any of its submodules — so a
# plain eager `from .features import ...` here would pull those heavy deps into every
# import of app.hazard.survival.fit / .serve, bundle or no bundle. Deferring the re-export
# to first attribute access keeps `import app.hazard.survival.serve` itself cheap; only
# actually touching the modeling API (FEATURES, all_models, ...) pays the import cost.
_SUBMODULE = {
    "FEATURES": ".features", "LOG_FEATURES": ".features", "build_model_matrix": ".features",
    "split_by_firm": ".features", "make_surv": ".features",
    "PanelConfig": ".data", "TRUE_BETA": ".data", "generate_panel": ".data",
    "CoxModel": ".models", "WeibullAFTModel": ".models", "RandomForestModel": ".models",
    "GradientBoostedModel": ".models", "DiscreteTimeHazardModel": ".models", "all_models": ".models",
    "evaluate_model": ".evaluate", "compare_models": ".evaluate", "time_grid": ".evaluate",
    "score_firm": ".score",
}


def __getattr__(name):
    mod_name = _SUBMODULE.get(name)
    if mod_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(mod_name, __name__), name)
