import React from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ReferenceArea, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { ACCENT, Card, INK, RISK, Section, chartTooltipStyle, fmtNum } from "../../ui/index.jsx";

export default function RiskTimeline({ data }) {
  const rows = (data.risk_timeline || []).filter((r) => r.altman_z != null);
  if (rows.length < 2) return null;

  // the axis always spans both zone cutoffs, so the bands read the same for every issuer
  const zs = rows.map((r) => r.altman_z);
  const lo = Math.floor(Math.min(0, ...zs));
  const hi = Math.ceil(Math.max(3.5, ...zs));

  // quarterly labels ("Q3 2025"): tick only the Q1s, as the bare year; annual labels pass through
  const yearTick = (l) => {
    const s = String(l);
    if (!s.startsWith("Q")) return s;
    return s.startsWith("Q1 ") ? s.slice(3) : "";
  };

  return (
    <Section flush title="Altman Z'' history"
      subtitle="per filed period · distress < 1.1 · grey 1.1–2.6 · safe > 2.6">
      <Card>
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={INK[700]} />
            <ReferenceArea y1={lo} y2={1.1} fill={RISK.high} fillOpacity={0.08} />
            <ReferenceArea y1={1.1} y2={2.6} fill={RISK.watch} fillOpacity={0.08} />
            <ReferenceArea y1={2.6} y2={hi} fill={RISK.ok} fillOpacity={0.08} />
            <XAxis dataKey="label" stroke="#64748b" fontSize={12} interval={0} tickFormatter={yearTick} />
            <YAxis domain={[lo, hi]} allowDecimals={false} stroke="#64748b" fontSize={12} />
            <Tooltip
              contentStyle={chartTooltipStyle}
              labelStyle={{ color: "#e5e9f0" }}
              formatter={(v) => [fmtNum(v), "Altman Z''"]}
            />
            <Line type="monotone" dataKey="altman_z" stroke={ACCENT} strokeWidth={2.5} dot={{ r: 3 }} />
          </LineChart>
        </ResponsiveContainer>
      </Card>
    </Section>
  );
}
