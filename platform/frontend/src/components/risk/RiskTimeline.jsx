import React from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ReferenceArea, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { ACCENT, Card, INK, RISK, Section, chartTooltipStyle } from "../../ui/index.jsx";

export default function RiskTimeline({ data }) {
  const rows = (data.risk_timeline || []).filter((r) => r.risk != null);
  if (rows.length < 2) return null;

  // quarterly labels ("Q3 2025"): tick only the Q1s, as the bare year; annual labels pass through
  const yearTick = (l) => {
    const s = String(l);
    if (!s.startsWith("Q")) return s;
    return s.startsWith("Q1 ") ? s.slice(3) : "";
  };

  return (
    <Section flush title="Risk over time" subtitle="composite distress risk per filed period · 0 safe — 100 distress">
      <Card>
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={INK[700]} />
            {/* risk-band shading */}
            <ReferenceArea y1={0} y2={33} fill={RISK.ok} fillOpacity={0.06} />
            <ReferenceArea y1={33} y2={66} fill={RISK.watch} fillOpacity={0.06} />
            <ReferenceArea y1={66} y2={100} fill={RISK.high} fillOpacity={0.06} />
            <XAxis dataKey="label" stroke="#64748b" fontSize={12} interval={0} tickFormatter={yearTick} />
            <YAxis domain={[0, 100]} stroke="#64748b" fontSize={12} />
            <Tooltip
              contentStyle={chartTooltipStyle}
              labelStyle={{ color: "#e5e9f0" }}
              formatter={(v, n, p) => [
                n === "risk"
                  ? `${v.toFixed(1)}/100${p?.payload?.components?.length ? ` — ${p.payload.components.join(" + ")}` : ""}`
                  : v?.toFixed?.(2),
                n === "risk" ? "risk" : "Altman Z''",
              ]}
            />
            <Line type="monotone" dataKey="risk" stroke={ACCENT} strokeWidth={2.5} dot={{ r: 3 }} />
          </LineChart>
        </ResponsiveContainer>
      </Card>
    </Section>
  );
}
