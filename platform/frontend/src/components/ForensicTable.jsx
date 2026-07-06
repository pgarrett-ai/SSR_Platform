import React from "react";
import CitedNumber from "./CitedNumber.jsx";

// The §6a "where is the cash coming from?" table. Every cell is citation- or formula-linked.
const ROWS = [
  ["total_debt", "Total reported debt"],
  ["cash", "Cash & equivalents"],
  ["revenue", "Revenue"],
  ["ebitda", "EBITDA (proxy)"],
  ["operating_cash_flow", "Operating cash flow"],
  ["free_cash_flow", "Free cash flow"],
  ["capex", "Capex"],
  ["accounts_payable", "Accounts payable"],
  ["inventory", "Inventory"],
  ["dpo", "Days payable (DPO)"],
];

export default function ForensicTable({ rows }) {
  if (!rows || rows.length === 0) {
    return <p className="text-sm text-slate-400">No annual XBRL facts available for this issuer.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-ink-600 text-slate-400">
            <th className="py-2 pr-4 text-left font-medium">Metric</th>
            {rows.map((r) => (
              <th key={r.fiscal_year} className="px-3 py-2 text-right font-medium">
                FY{r.fiscal_year}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ROWS.map(([key, label]) => {
            const anyPresent = rows.some((r) => r[key]);
            if (!anyPresent) return null;
            return (
              <tr key={key} className="border-b border-ink-700/60 hover:bg-ink-700/30">
                <td className="py-2 pr-4 text-left text-slate-300">{label}</td>
                {rows.map((r) => (
                  <td key={r.fiscal_year} className="px-3 py-2 text-right">
                    <CitedNumber cv={r[key]} />
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] text-slate-500">
        Hover any number for its source filing (or <span className="text-amber-400/80">ƒ</span> for the
        formula on derived figures). EBITDA = operating income + D&A; FCF = OCF − capex.
      </p>
    </div>
  );
}
