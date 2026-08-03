import React, { useEffect, useState } from "react";
import CitedNumber from "./CitedNumber.jsx";
import { MUTED_EXCLUDED, MUTED_NA, fmtLev } from "../ui/index.jsx";

// The EBITDA box: net income → EBITDA walk (each line XBRL-cited), then the issuer's own
// covenant add-back categories. Quantified add-backs toggle (checkbox or row click) and
// the Adjusted EBITDA + implied-leverage lines react; unquantified add-backs are inert —
// dimmed, no toggle affordance, because excluding them can't change any number.

const fmtM = (v) =>
  v == null ? "—" : `${v < 0 ? "−" : ""}$${Math.round(Math.abs(v) / 1e6).toLocaleString()}M`;

export default function EbitdaBuild({ build, economicDebt }) {
  const [excluded, setExcluded] = useState(() => new Set());

  useEffect(() => { setExcluded(new Set()); }, [build]);

  if (!build || !build.lines?.length) return null;

  const toggle = (i) => {
    setExcluded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  };

  const quantified = (a) => a.amount?.value != null;
  const nQuant = build.addbacks.filter(quantified).length;
  const ebitda = build.ebitda?.value ?? null;
  const addbackSum = build.addbacks.reduce(
    (sum, a, i) => sum + (!excluded.has(i) && a.amount?.value ? a.amount.value : 0), 0);
  const nIncluded = build.addbacks.filter((a, i) => quantified(a) && !excluded.has(i)).length;
  const adjusted = ebitda == null ? null : ebitda + addbackSum;
  const impliedLev =
    adjusted && economicDebt?.value ? economicDebt.value / adjusted : null;

  // The one number the user tuned gets the same provenance treatment as everything else.
  const adjustedCv = adjusted == null ? null : {
    display: fmtM(adjusted), value: adjusted, derived: true,
    formula: `EBITDA ${fmtM(ebitda)} + ${nIncluded} enabled add-back${nIncluded === 1 ? "" : "s"} ${fmtM(addbackSum)}`,
    note: excluded.size ? `${excluded.size} add-back${excluded.size === 1 ? "" : "s"} excluded by you` : undefined,
  };

  return (
    <div>
      <table className="w-full text-sm">
        <tbody>
          {build.lines.map((ln, i) => (
            <tr
              key={i}
              className={`border-b border-ink-700/60 ${
                ln.is_total ? "font-semibold text-slate-100" : "text-slate-300"
              }`}
            >
              <td className="py-2">{ln.label}</td>
              <td className="py-2 text-right">
                <CitedNumber cv={ln.amount} className={ln.is_total ? "text-base" : ""} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {build.addbacks.length > 0 && (
        <>
          <div className="mt-4 mb-1 flex items-baseline justify-between gap-3">
            <span className="text-[10px] uppercase tracking-wide text-slate-500">
              Covenant add-backs (from the credit agreement)
            </span>
            <span className="text-[10px] text-slate-500">
              {nQuant} of {build.addbacks.length} quantifiable · {fmtM(addbackSum)} included
            </span>
          </div>
          <table className="w-full text-sm">
            <tbody>
              {build.addbacks.map((a, i) => {
                const off = excluded.has(i);
                if (!quantified(a)) {
                  // Inert: excluding an unquantified add-back can't change any number,
                  // so it gets no toggle affordance at all.
                  return (
                    <tr key={i} className={`border-b border-ink-700/40 text-slate-400 ${MUTED_NA}`}>
                      <td className="py-1.5 pl-6">+ {a.label}</td>
                      <td className="py-1.5 text-right">
                        {a.amount ? (
                          <CitedNumber cv={a.amount} />
                        ) : (
                          <span className="text-[11px] italic text-slate-500">
                            not quantified — disclosed only
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                }
                return (
                  <tr
                    key={i}
                    onClick={() => toggle(i)}
                    title={off ? "click to include" : "click to exclude"}
                    className={`cursor-pointer border-b border-ink-700/40 ${
                      off ? MUTED_EXCLUDED : "text-slate-300"
                    }`}
                  >
                    <td className="py-1.5">
                      <span className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={!off}
                          onChange={() => toggle(i)}
                          onClick={(e) => e.stopPropagation()}
                          className="accent-accent"
                          aria-label={`include ${a.label} in Adjusted EBITDA`}
                        />
                        + {a.label}
                      </span>
                    </td>
                    <td className="py-1.5 text-right">
                      {/* provenance survives exclusion — the citation matters most
                          exactly when the user is deciding whether to trust the number */}
                      <CitedNumber cv={a.amount} />
                    </td>
                  </tr>
                );
              })}
              <tr className="font-semibold text-slate-100">
                <td className="py-2 pl-6">Adjusted EBITDA</td>
                <td className="py-2 text-right">
                  {adjustedCv ? <CitedNumber cv={adjustedCv} /> : <span className="font-mono">—</span>}
                </td>
              </tr>
            </tbody>
          </table>
        </>
      )}

      {impliedLev != null && (
        <p className="mt-2 text-[11px] text-slate-500">
          Implied economic leverage vs adjusted EBITDA:{" "}
          <span className="font-mono text-slate-300">{fmtLev(impliedLev)}</span>
          {" "}(economic debt {fmtM(economicDebt.value)} / adjusted EBITDA {fmtM(adjusted)})
        </p>
      )}
    </div>
  );
}
