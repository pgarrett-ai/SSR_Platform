import React from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ReferenceArea, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { Section, Card } from "./ui.jsx";

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
    <Section title="Risk over time" subtitle="Composite distress risk per filed period (0 = safe, 100 = distress) — hover a point for the signals blended into it">
      <Card>
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid stroke="#1a2230" />
            {/* risk-band shading */}
            <ReferenceArea y1={0} y2={33} fill="#10b981" fillOpacity={0.06} />
            <ReferenceArea y1={33} y2={66} fill="#f59e0b" fillOpacity={0.06} />
            <ReferenceArea y1={66} y2={100} fill="#f43f5e" fillOpacity={0.06} />
            <XAxis dataKey="label" stroke="#64748b" fontSize={12} interval={0} tickFormatter={yearTick} />
            <YAxis domain={[0, 100]} stroke="#64748b" fontSize={12} />
            <Tooltip
              contentStyle={{ background: "#111827", border: "1px solid #263041", borderRadius: 8 }}
              labelStyle={{ color: "#e5e9f0" }}
              formatter={(v, n, p) => [
                n === "risk"
                  ? `${v.toFixed(1)}/100${p?.payload?.components?.length ? ` — ${p.payload.components.join(" + ")}` : ""}`
                  : v?.toFixed?.(2),
                n === "risk" ? "risk" : "Altman Z''",
              ]}
            />
            <Line type="monotone" dataKey="risk" stroke="#5e7bff" strokeWidth={2.5} dot={{ r: 3 }} />
          </LineChart>
        </ResponsiveContainer>
      </Card>
    </Section>
  );
}
