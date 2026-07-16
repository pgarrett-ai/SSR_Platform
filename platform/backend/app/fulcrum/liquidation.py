"""Asset-based liquidation waterfall (Moyer ch. 5/8): when the firm cannot sustain
positive EBITDA, cash-flow metrics are irrelevant — value the assets at advance/haircut
rates (orderly vs fire-sale), net estate costs, and run the SAME waterfall on the
proceeds (single draw). This is also the negative-EBITDA degradation path for the
Recovery page (C4): a going-concern EV simulation is meaningless below zero EBITDA.

All dollar figures $mm.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .structure import CapitalStructure
from .waterfall import run_waterfall

# Recovery rate on book value by asset category. Fire-sale ≈ half the orderly rate on
# non-cash assets (Moyer: the disposition basis is a key, stated assumption).
ORDERLY = {"cash": 1.00, "accounts_receivable": 0.75, "inventory": 0.50,
           "ppe": 0.40, "intangibles": 0.10, "other": 0.25}
FIRE_SALE = {"cash": 1.00, "accounts_receivable": 0.40, "inventory": 0.25,
             "ppe": 0.20, "intangibles": 0.05, "other": 0.10}
# Estate/admin cost defaults: ch11 orderly ~7%, ch7 fire-sale ~10% of gross proceeds.
ADMIN_CH11 = 0.07
ADMIN_CH7 = 0.10

_LABELS = {"cash": "Cash & equivalents", "accounts_receivable": "Accounts receivable",
           "inventory": "Inventory", "ppe": "PP&E (net)",
           "intangibles": "Goodwill & intangibles", "other": "Other assets"}


def _scenario(assets: dict[str, float], structure: CapitalStructure,
              rates: dict[str, float], admin_pct: float, accrual_years: float) -> dict:
    lines = []
    gross = 0.0
    for key, label in _LABELS.items():
        book = assets.get(key)
        if book is None:
            continue
        rate = rates.get(key, 0.0)
        proceeds = book * rate
        gross += proceeds
        lines.append({"key": key, "label": label, "book": round(book, 1),
                      "rate": rate, "proceeds": round(proceeds, 1),
                      "formula": f"{label} ${book:,.0f}M × {100 * rate:.0f}%"})
    net = gross * (1.0 - admin_pct)

    wf = run_waterfall(structure, np.array([net]), accrual_years=accrual_years)
    order = structure.priority_order()
    tmap = {t.name: t for t in structure.tranches}
    rows = []
    fulcrum = None
    for n in order:
        c = tmap[n].claim(accrual_years)
        rec = float(wf[n][0])
        pct = 100 * rec / c if c > 0 else None
        if fulcrum is None and c > 0 and rec < c * 0.999:
            fulcrum = n
        rows.append({"tranche": n, "entity": tmap[n].entity, "face": tmap[n].face,
                     "claim": round(c, 1), "recovery": round(rec, 1),
                     "recovery_pct": round(pct, 1) if pct is not None else None,
                     "is_fulcrum": n == fulcrum and fulcrum is not None})
    return {"lines": lines, "gross_proceeds": round(gross, 1), "admin_pct": admin_pct,
            "net_proceeds": round(net, 1), "tranches": rows, "fulcrum": fulcrum}


def liquidate(assets: dict[str, float], structure: CapitalStructure,
              rates: Optional[dict[str, float]] = None,
              admin_pct: Optional[float] = None,
              accrual_years: float = 0.0) -> dict:
    """Main scenario (custom rates/admin or the orderly preset) plus the ch11-orderly vs
    ch7-fire-sale comparison pair."""
    main = _scenario(assets, structure, rates or ORDERLY,
                     ADMIN_CH11 if admin_pct is None else admin_pct, accrual_years)
    return {
        "mode": "liquidation",
        "scenario": main,
        "ch11_vs_ch7": {
            "ch11_orderly": _scenario(assets, structure, ORDERLY, ADMIN_CH11, accrual_years),
            "ch7_fire_sale": _scenario(assets, structure, FIRE_SALE, ADMIN_CH7, accrual_years),
        },
        "presets": {"orderly": ORDERLY, "fire_sale": FIRE_SALE,
                    "admin_ch11": ADMIN_CH11, "admin_ch7": ADMIN_CH7},
        "note": "asset-based liquidation: book values × advance/haircut rates, net of "
                "estate costs, distributed by absolute priority (Moyer ch. 5: when positive "
                "EBITDA is unattainable, cash-flow metrics are irrelevant)",
    }


def assets_from_snapshot(snap: Optional[dict]) -> Optional[dict[str, float]]:
    """AssetSnapshot (CitedValues, $) -> {category: $mm}; None when nothing extracted."""
    if not snap:
        return None
    out = {}
    for key in _LABELS:
        cv = snap.get(key)
        if cv and cv.get("value") is not None:
            out[key] = float(cv["value"]) / 1e6
    return out or None
