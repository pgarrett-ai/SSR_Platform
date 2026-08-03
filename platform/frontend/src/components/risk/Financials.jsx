import React from "react";
import { Card, Section, fmtX, fmtPct, fmtMoney } from "../../ui/index.jsx";
import CitedNumber from "../CitedNumber.jsx";

const RATIO_COLS = [
  ["leverage", "Debt/Assets", fmtPct],
  ["net_debt_to_ebitda", "NetDebt/EBITDA", fmtX],
  ["interest_coverage", "EBIT/Int", fmtX],
  ["current_ratio", "Current", fmtX],
  ["quick_ratio", "Quick", fmtX],
  ["cash_ratio", "Cash/CL", fmtX],
  ["roa", "ROA", fmtPct],
  ["fcf_margin", "FCF margin", fmtPct],
];

const RAW_COLS = [
  ["revenue", "Revenue", fmtMoney],
  ["ebitda", "EBITDA", fmtMoney],
  ["total_debt", "Total debt", fmtMoney],
  ["cash", "Cash", fmtMoney],
  ["fcf", "FCF", fmtMoney],
  ["net_income", "Net income", fmtMoney],
];

// ponytail: raw th/td, not kit Th/Td — these two tables are denser (text-xs, py-1.5)
// than the kit cells, and header/body padding must match for the sticky FY column.
function Table({ rows, cols }) {
  // Newest FY on top. Spread before reversing — `rows` is the shared features_timeline
  // array that other consumers read with last-element-is-latest.
  const ordered = [...rows].reverse();
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-ink-600 text-[10px] font-medium uppercase tracking-wide text-slate-500">
            <th className="text-left py-1.5 pr-3 sticky left-0 bg-ink-800/60">FY</th>
            {cols.map(([, label]) => (
              <th key={label} className="text-right py-1.5 px-2">{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ordered.map((r) => (
            <tr key={r.fiscal_year} className="border-b border-ink-700/50">
              <td className="py-1.5 pr-3 font-mono text-slate-300 sticky left-0 bg-ink-800/60">{r.fiscal_year}</td>
              {cols.map(([key, , fmt]) => (
                <td key={key} className="text-right py-1.5 px-2 font-mono text-slate-200">
                  {r.cited?.[key] ? <CitedNumber cv={r.cited[key]} /> : fmt(r[key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Financials({ data }) {
  const rows = data.features_timeline || [];
  if (rows.length === 0) return null;
  return (
    <Section flush title="Financials" subtitle="history from EDGAR XBRL (10-K)">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <Card><div className="text-xs text-slate-500 mb-1">Ratios</div><Table rows={rows} cols={RATIO_COLS} /></Card>
        <Card><div className="text-xs text-slate-500 mb-1">Raw figures</div><Table rows={rows} cols={RAW_COLS} /></Card>
      </div>
    </Section>
  );
}
