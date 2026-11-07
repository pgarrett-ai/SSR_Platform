import React, { useMemo } from "react";
import CitedNumber from "./CitedNumber.jsx";
import { Badge, Td, Th, fmtMoney, rowClass, useSort } from "../ui/index.jsx";

// Coupon cell: tagged XBRL rates first (coupon_pct / effective_rate_pct — the same
// fields the recovery adapter accrues on), regex compaction of the filing prose only
// as the untagged fallback; the exact filing wording is always in the title tooltip.
function compactCoupon(s) {
  if (!s || s.length <= 14) return s;
  const pcts = [...s.matchAll(/(\d+(?:\.\d+)?)\s*%/g)].map((m) => m[1]);
  if (!pcts.length) return null;
  const varMark = /variable|floating/i.test(s) ? " var." : "";
  if (pcts.length >= 3 && /rang/i.test(s)) return `${pcts[0]}–${pcts[1]}% avg ${pcts[2]}%`;
  if (pcts.length === 2 && /rang|\bto\b/i.test(s)) return `${pcts[0]}–${pcts[1]}%`;
  return `${pcts[0]}%${varMark}`;
}

function couponCell(d) {
  if (d.coupon_pct != null)
    return d.coupon_pct_max != null ? `${d.coupon_pct}–${d.coupon_pct_max}%` : `${d.coupon_pct}%`;
  if (d.effective_rate_pct != null) return `${d.effective_rate_pct}% eff.`;
  return compactCoupon(d.coupon) || d.coupon || "—";
}

// The schedule's total reconciles against reported debt at the schedule's own as-of
// (`reported`, from the forensic row for that period) — never silently against a
// different period's balance sheet. Pipeline warnings explain any gap.
export default function DebtScheduleTable({ instruments, asOf, reported, reportedLabel, warnings }) {
  // Sortable projections; default sort null = server order (assumed seniority).
  const projected = useMemo(() => (instruments || []).map((d) => ({
    ...d,
    _out: d.outstanding?.value ?? d.principal?.value ?? null,
    _mat: d.maturity || null,
  })), [instruments]);
  const { sorted, thProps } = useSort(projected, null);

  // the subtitle and total row already state these two; keep one statement of each
  const shown = reported
    ? (warnings || []).filter((w) => !/^(Maturity unknown|Debt schedule sums to)/.test(w))
    : warnings || [];
  const notes = shown.map((w, i) => (
    <p key={i} className="mt-1 text-[11px] text-amber-300/80">{w}</p>
  ));
  if (!instruments || instruments.length === 0) {
    return (
      <>
        <p className="text-sm text-slate-400">No instrument-level detail available for this issuer.</p>
        {notes}
      </>
    );
  }

  const hasObligor = instruments.some((d) => d.obligor);
  const hasCapacity = instruments.some((d) => d.commitment || d.undrawn);
  const amounts = projected.filter((d) => d._out != null);
  const total = amounts.reduce((s, d) => s + d._out, 0);
  const totalCv = {
    value: total, display: fmtMoney(total), derived: true,
    formula: `Σ outstanding of the ${amounts.length} instruments with a tagged amount`,
  };
  const gap = reported?.value != null ? total - reported.value : null;
  const colSpanRest = 3 + (hasCapacity ? 1 : 0) + (hasObligor ? 1 : 0);

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink-600">
              <Th {...thProps("instrument")}>Instrument</Th>
              <Th right {...thProps("_out")}>Outstanding</Th>
              {hasCapacity && <Th right>Undrawn / commitment</Th>}
              <Th>Coupon</Th>
              <Th {...thProps("_mat")}>Maturity</Th>
              <Th>Lien / seniority</Th>
              {hasObligor && <Th>Obligor</Th>}
            </tr>
          </thead>
          <tbody>
            {sorted.map((d, i) => (
              <tr key={i} className={rowClass}>
                <Td className="text-slate-200">
                  {d.instrument}
                  {d.facility_type && d.facility_type !== "notes" && (
                    <span className="ml-2 text-[10px] uppercase tracking-wide text-slate-500">
                      {d.facility_type}
                    </span>
                  )}
                </Td>
                <Td right>
                  <CitedNumber cv={d.outstanding || d.principal} />
                </Td>
                {hasCapacity && (
                  <Td right mono className="text-[12px] text-slate-400">
                    {d.undrawn || d.commitment ? (
                      <>
                        {d.undrawn ? <CitedNumber cv={d.undrawn} /> : "—"}
                        {d.commitment && (
                          <span className="text-slate-500">
                            {" / "}
                            <CitedNumber cv={d.commitment} />
                          </span>
                        )}
                      </>
                    ) : (
                      "—"
                    )}
                  </Td>
                )}
                <Td mono className="text-[12px] text-slate-300">
                  <span title={d.coupon || undefined}>{couponCell(d)}</span>
                </Td>
                <Td mono className={`text-[12px] ${d.maturity ? "text-slate-300" : "text-slate-500"}`}
                  title={d.maturity ? undefined : "maturity not disclosed in the filing"}>
                  {d.maturity || "n/d"}
                </Td>
                <Td className="text-[12px]">
                  {d.secured != null && (
                    <Badge tone={d.secured ? "ok" : "neutral"} className="mr-2">
                      {d.secured ? "secured" : "unsecured"}
                    </Badge>
                  )}
                  <span className="text-slate-400">{d.seniority || ""}</span>
                </Td>
                {hasObligor && (
                  <Td className="text-[12px] text-slate-400">{d.obligor || "—"}</Td>
                )}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-ink-600 font-semibold text-slate-100">
              <Td>Schedule total{asOf ? `, as of ${asOf}` : ""}</Td>
              <Td right><CitedNumber cv={totalCv} /></Td>
              <Td colSpan={colSpanRest} />
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-slate-400">
        {reported ? (
          <>
            Reported debt, {reportedLabel} balance sheet: <CitedNumber cv={reported} />
            {gap != null && Math.abs(gap) >= 0.5e6 && (
              <> · schedule is {fmtMoney(Math.abs(gap))} {gap > 0 ? "above" : "below"} it</>
            )}
          </>
        ) : (
          `No reported debt figure tagged for ${asOf || "the schedule's period"} to reconcile against.`
        )}
      </p>
      {notes}
    </div>
  );
}
