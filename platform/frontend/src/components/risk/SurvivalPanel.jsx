import React from "react";
import { Card, Section, fmtPct, fmtNum } from "../../ui/index.jsx";

export default function SurvivalPanel({ data }) {
  const rows = data.models || [];
  if (rows.length === 0) return null;
  return (
    <Section flush title="Survival panel" subtitle={data.note}>
      <Card>
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-ink-600 text-[10px] font-medium uppercase tracking-wide text-slate-500">
              <th className="text-left py-1.5 pr-3">Model</th>
              <th className="text-right py-1.5 px-2">1y PD</th>
              <th className="text-right py-1.5 px-2">C-index</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name} className="border-b border-ink-700/50">
                <td className="py-1.5 pr-3 text-slate-300">{r.name}</td>
                <td className="text-right py-1.5 px-2 font-mono text-slate-100">{fmtPct(r.pd_1y, 1)}</td>
                <td className="text-right py-1.5 px-2 font-mono text-slate-400">{fmtNum(r.c_index, 3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </Section>
  );
}
