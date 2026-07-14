import React from "react";
import { Badge } from "../ui/index.jsx";
import CitedNumber from "./CitedNumber.jsx";

const CAT_LABELS = {
  lease_operating: "Operating lease",
  lease_finance: "Finance lease",
  pension_opeb: "Pension / OPEB",
  supplier_finance: "Supplier finance",
  guarantee: "Guarantee",
  securitization: "Securitization / factoring",
  take_or_pay: "Take-or-pay",
  vie: "Variable interest entity",
  related_party: "Related party",
  litigation_env: "Environmental / litigation",
  other: "Other OBS",
};

export default function ObsFindings({ items }) {
  if (!items || items.length === 0) {
    return (
      <p className="text-sm text-slate-400">
        Off-balance-sheet items (leases, pension, supplier finance, guarantees, VIEs,
        securitizations, take-or-pay) appear here once footnote extraction runs.
      </p>
    );
  }
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {items.map((it, i) => (
        <div key={i} className="rounded-lg border border-ink-700 bg-ink-800/70 p-4">
          <div className="mb-1 flex items-center justify-between gap-2">
            <Badge>{CAT_LABELS[it.category] || it.category}</Badge>
            {it.include_in_bridge ? (
              <Badge tone="high">in bridge</Badge>
            ) : (
              <Badge>informational</Badge>
            )}
          </div>
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-[13px] text-slate-200">{it.label}</span>
            <CitedNumber cv={it.amount} className="text-sm" placeholder="disclosed" />
          </div>
          {it.net && (
            <div className="mt-1 text-right text-[11px] text-slate-400">
              net of tax <CitedNumber cv={it.net} /> · tax effect <CitedNumber cv={it.tax_effect} />
            </div>
          )}
          {it.recourse && it.recourse !== "unknown" && (
            <div className="mt-1 text-[11px] text-slate-500">
              {it.recourse === "partial" ? "partial recourse" : it.recourse}
            </div>
          )}
          {it.notes && <p className="mt-2 text-[12px] text-slate-400">{it.notes}</p>}
        </div>
      ))}
    </div>
  );
}
