import React from "react";
import { Card, Gauge, NEUTRAL, RISK, Stat, fmtPct, fmtNum, riskColor } from "../../ui/index.jsx";

const TREND = {
  worsening: { icon: "▲", color: RISK.high, label: "Worsening" },
  improving: { icon: "▼", color: RISK.ok, label: "Improving" },
  stable: { icon: "▬", color: "#94a3b8", label: "Stable" },
  "n/a": { icon: "–", color: NEUTRAL, label: "n/a" },
};

export default function ExecutiveSummary({ data }) {
  const es = data.executive_summary || {};
  const pd = es.distress_pd || {};
  const trend = TREND[es.trend?.direction] || TREND["n/a"];

  return (
    <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
      <Card className="flex flex-wrap items-center gap-3">
        <div className="shrink-0"><Gauge value={es.overall_risk} size={110} /></div>
        <div>
          <div className="text-xs uppercase tracking-wide text-slate-500">Overall risk</div>
          <div className="text-2xl font-semibold" style={{ color: riskColor(es.overall_risk) }}>
            {es.overall_risk == null ? "—" : `${Math.round(es.overall_risk)}/100`}
          </div>
          <div className="text-xs text-slate-400">
            composite ({(es.composite_of || ["Altman", "Merton"]).join(" + ")})
          </div>
        </div>
      </Card>

      <Stat
        label="Distress PD"
        value={pd["12m"] != null ? fmtPct(pd["12m"]) : "—"}
        sub={
          pd["3m"] != null
            ? `3m ${fmtPct(pd["3m"], 2)} · 6m ${fmtPct(pd["6m"], 2)} · 12m ${fmtPct(pd["12m"], 2)}`
            : "market data unavailable"
        }
        color={riskColor(pd["12m"] != null ? pd["12m"] * 100 * 5 : null)}
      />

      <Stat
        label="Distance-to-Default"
        value={es.distance_to_default != null ? `${fmtNum(es.distance_to_default)}σ` : "—"}
        sub={es.distance_to_default != null ? "Merton, 1-year" : "needs equity value + vol"}
      />

      <Stat
        label="Trend"
        value={
          <span style={{ color: trend.color }}>
            {trend.icon} {trend.label}
          </span>
        }
        sub={es.trend?.slope != null ? `${es.trend.slope > 0 ? "+" : ""}${es.trend.slope} risk/yr` : "≥2 yrs needed"}
      />
    </div>
  );
}
