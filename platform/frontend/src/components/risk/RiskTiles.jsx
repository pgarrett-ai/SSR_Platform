import React from "react";
import { Stat, ZONE_COLOR, fmtNum, fmtPct } from "../../ui/index.jsx";

// Below a basis point fmtPct(_, 2) would print a misleading 0.00%.
const pd = (x) => (x == null ? "—" : x < 1e-4 ? "<0.01%" : fmtPct(x, 2));

// Model breakdown: Merton's short end and its asset-vol input, Altman Z'', and the trained
// model beside the agency rating (only when it was trained on real default labels).
export default function RiskTiles({ data }) {
  const m = data.merton || {};
  const alt = data.scores?.["Altman Z''"] || {};
  const tr = data.scores?.["Trained hazard"];
  const agency = (data.issuer_bonds?.bonds || []).map((b) => b.rating).find((r) => r && r !== "NR");

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
        sub={m.available && (m.converged ? "Merton solve, 1-year" : "naive assets: solve did not converge")}
      />
      <Stat
        label="Altman Z''"
        value={alt.available ? fmtNum(alt.value) : "—"}
        sub={alt.available ? `${alt.zone} zone · cutoffs 1.1 / 2.6` : alt.note}
        color={ZONE_COLOR[alt.zone]}
      />
      {tr?.available && tr.real_labels && (
        <div title={tr.note}>
          <Stat
            label="Trained 12m PD"
            value={`${pd(tr.value)}${tr.implied_rating ? ` ≈ ${tr.implied_rating}` : ""}`}
            sub={agency ? `agency: ${agency}` : "no agency rating on file"}
          />
        </div>
      )}
    </div>
  );
}
