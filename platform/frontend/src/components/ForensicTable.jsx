import React from "react";
import CitedNumber from "./CitedNumber.jsx";
import { Td, Th, rowClass } from "../ui/index.jsx";

// The §6a cash-vs-debt table. Every cell is citation- or formula-linked.
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
  // Newest period leftmost. Spread before reversing — the payload array is shared.
  const cols = [...rows].reverse();
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-ink-600">
            <Th>Metric</Th>
            {cols.map((r) => (
              <Th key={r.label ?? r.fiscal_year} right>{r.label || `FY${r.fiscal_year}`}</Th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ROWS.map(([key, label]) => {
            const anyPresent = cols.some((r) => r[key]);
            if (!anyPresent) return null;
            return (
              <tr key={key} className={rowClass}>
                <Td className="text-slate-300">{label}</Td>
                {cols.map((r) => (
                  <Td key={r.label ?? r.fiscal_year} right>
                    <CitedNumber cv={r[key]} />
                  </Td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] text-slate-500">
        EBITDA and FCF are XBRL proxies (op. income + D&A; OCF − capex). A quarter column
        shows 10-Q balance-sheet snapshots with trailing-12-month flows.
      </p>
    </div>
  );
}
