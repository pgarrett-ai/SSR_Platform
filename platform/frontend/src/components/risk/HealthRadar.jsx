import React from "react";
import {
  Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer, Tooltip,
} from "recharts";
import { Section, Card } from "./ui.jsx";

const clamp01 = (x) => Math.max(0, Math.min(1, x));

// Map each ratio to a 0-100 "health" score (higher = healthier).
function healthDims(f) {
  if (!f) return [];
  const dims = [
    ["Leverage", f.leverage == null ? null : 100 * (1 - clamp01(f.leverage / 1.2))],
    ["Coverage", f.interest_coverage == null ? null : 100 * clamp01(f.interest_coverage / 8)],
    ["Liquidity", f.current_ratio == null ? null : 100 * clamp01((f.current_ratio - 0.5) / 1.5)],
    ["Profitability", f.roa == null ? null : 100 * clamp01((f.roa + 0.05) / 0.15)],
    ["Size", f.size_log_assets == null ? null : 100 * clamp01((f.size_log_assets - 5) / 5)],
  ];
  return dims
    .filter(([, v]) => v != null)
    .map(([dim, v]) => ({ dim, health: Math.round(v) }));
}

export default function HealthRadar({ data }) {
  const tl = data.features_timeline || [];
  const latest = tl[tl.length - 1];
  const rows = healthDims(latest);
  if (rows.length < 3) return null;

  return (
    <Section title="Financial health" subtitle={`Latest fiscal year (FY${latest.fiscal_year}); 100 = healthiest`}>
      <Card>
        <ResponsiveContainer width="100%" height={260}>
          <RadarChart data={rows} outerRadius="72%">
            <PolarGrid stroke="#263041" />
            <PolarAngleAxis dataKey="dim" tick={{ fill: "#94a3b8", fontSize: 12 }} />
            <PolarRadiusAxis domain={[0, 100]} tick={{ fill: "#475569", fontSize: 10 }} />
            <Radar dataKey="health" stroke="#5e7bff" fill="#5e7bff" fillOpacity={0.35} />
            <Tooltip
              contentStyle={{ background: "#111827", border: "1px solid #263041", borderRadius: 8 }}
              formatter={(v) => [`${v}/100`, "health"]}
            />
          </RadarChart>
        </ResponsiveContainer>
      </Card>
    </Section>
  );
}
