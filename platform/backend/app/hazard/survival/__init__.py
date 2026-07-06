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

from .features import FEATURES, LOG_FEATURES, build_model_matrix, split_by_firm, make_surv
from .data import PanelConfig, TRUE_BETA, generate_panel
from .models import (
    CoxModel,
    WeibullAFTModel,
    RandomForestModel,
    GradientBoostedModel,
    DiscreteTimeHazardModel,
    all_models,
)
from .evaluate import evaluate_model, compare_models, time_grid
from .score import score_firm

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
