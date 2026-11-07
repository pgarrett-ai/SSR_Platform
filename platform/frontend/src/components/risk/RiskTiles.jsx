import React from "react";
import { Stat, ZONE_COLOR, fmtNum, fmtPct } from "../../ui/index.jsx";

// Below a basis point fmtPct(_, 2) would print a misleading 0.00%.
const pd = (x) => (x == null ? "—" : x < 1e-4 ? "<0.01%" : fmtPct(x, 2));

// Classic Z'' zones; the same cutoffs the history chart draws.
const zoneOf = (z) => (z == null ? null : z < 1.1 ? "distress" : z < 2.6 ? "grey" : "safe");

// Model breakdown: Merton's short end and its asset-vol input, Altman Z'' at the latest
// period the history chart plots, and the trained model (only when fit on real labels).
export default function RiskTiles({ data }) {
  const m = data.merton || {};
  const alt = data.scores?.["Altman Z''"] || {};
  const tr = data.scores?.["Trained hazard"];
  const lastZ = (data.risk_timeline || []).filter((p) => p.altman_z != null).at(-1);
  const z = lastZ ? lastZ.altman_z : alt.available ? alt.value : null;
  const zone = zoneOf(z);
  const zPeriod = lastZ ? lastZ.label || `FY${lastZ.fiscal_year}` : "latest FY";

  return (
    <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
      <Stat
        label="Merton PD · 3m / 6m"
        value={m.available ? `${pd(m.pd?.["3m"])} / ${pd(m.pd?.["6m"])}` : "—"}
        sub={m.note || "market-implied (risk-neutral)"}
      />
      <Stat
        label="Asset vol"
        value={m.available ? fmtPct(m.asset_vol) : "—"}
        sub={m.available && (m.converged ? "Merton model, 1-year horizon" : "assets approximated as equity + debt (Merton did not converge)")}
      />
      <Stat
        label="Altman Z''"
        value={z != null ? fmtNum(z) : "—"}
        sub={z != null ? `${zone} zone · ${zPeriod} · cutoffs 1.1 / 2.6` : alt.note}
        color={ZONE_COLOR[zone]}
      />
      {tr?.available && tr.real_labels && (
        <div title={tr.note}>
          <Stat
            label="Hazard model 12m PD"
            value={`${pd(tr.value)}${tr.implied_rating ? ` ≈ ${tr.implied_rating}` : ""}`}
            sub="calibrated to the observed default rate"
          />
        </div>
      )}
    </div>
  );
}
