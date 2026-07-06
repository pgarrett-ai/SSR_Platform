"""Fulcrum - distressed-debt recovery & waterfall simulator.

A Monte Carlo engine that simulates enterprise value (correlated EBITDA and
exit-multiple compression), allocates proceeds down a capital structure under
strict absolute priority - including structural subordination across legal
entities - and reports a recovery distribution per tranche plus the implied
fulcrum security.
"""

from .structure import Entity, Tranche, CapitalStructure, UNSECURED
from .simulate import SimConfig, simulate_enterprise_value
from .waterfall import allocate_entity, run_waterfall
from .recovery import RecoveryResult, analyze
from .adapter import classify_seniority, overview_to_structure

__all__ = [
    "Entity",
    "Tranche",
    "CapitalStructure",
    "UNSECURED",
    "SimConfig",
    "simulate_enterprise_value",
    "allocate_entity",
    "run_waterfall",
    "RecoveryResult",
    "analyze",
    "classify_seniority",
    "overview_to_structure",
]

__version__ = "0.1.0"
