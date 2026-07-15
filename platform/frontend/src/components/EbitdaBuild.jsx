import React, { useEffect, useState } from "react";
import CitedNumber from "./CitedNumber.jsx";
import { fmtLev } from "../ui/index.jsx";

// The EBITDA box: net income → EBITDA walk (each line XBRL-cited), then the issuer's own
// covenant add-back categories. Each quantified add-back toggles — greyed out = removed
// from Adjusted EBITDA, and the implied economic leverage line reacts.

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

  const ebitda = build.ebitda?.value ?? null;
  const addbackSum = build.addbacks.reduce(
    (sum, a, i) => sum + (!excluded.has(i) && a.amount?.value ? a.amount.value : 0), 0);
  const adjusted = ebitda == null ? null : ebitda + addbackSum;
  const impliedLev =
    adjusted && economicDebt?.value ? economicDebt.value / adjusted : null;

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
          <div className="mt-4 mb-1 text-[10px] uppercase tracking-wide text-slate-500">
            Covenant add-backs (from the credit agreement — click to exclude)
          </div>
          <table className="w-full text-sm">
            <tbody>
              {build.addbacks.map((a, i) => {
                const off = excluded.has(i);
                return (
                  <tr
                    key={i}
                    onClick={() => toggle(i)}
                    title={off ? "click to include" : "click to exclude"}
                    className={`cursor-pointer border-b border-ink-700/40 ${
                      off ? "text-slate-600 line-through" : "text-slate-300"
                    }`}
                  >
                    <td className="py-1.5">+ {a.label}</td>
                    <td className="py-1.5 text-right">
                      {a.amount ? (
                        off ? (
                          <span className="font-mono text-slate-600">{a.amount.display}</span>
                        ) : (
                          <CitedNumber cv={a.amount} />
                        )
                      ) : (
                        <span className="text-[11px] italic text-slate-600">
                          disclosed, not XBRL-quantifiable
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
              <tr className="font-semibold text-slate-100">
                <td className="py-2">Adjusted EBITDA</td>
                <td className="py-2 text-right font-mono">{fmtM(adjusted)}</td>
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
