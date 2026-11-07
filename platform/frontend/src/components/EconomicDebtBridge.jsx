import React from "react";
import CitedNumber from "./CitedNumber.jsx";

// ponytail: pre-rework cached snapshots still carry cash/net lines as data; drop them at
// render. Remove this filter once the hero caches are regenerated.
const DROPPED_KEYS = new Set(["cash_offset", "restricted_cash_offset", "net_economic_debt"]);

function Chip({ label, cv }) {
  return (
    <span className="rounded bg-ink-700/60 px-2 py-1 text-[11px] text-slate-400">
      {label} <CitedNumber cv={cv} className="text-slate-200" />
    </span>
  );
}

// Reported debt → each debt-like obligation → economic debt, every line cited. The footer
// names the EBITDA every leverage figure divides by, and the coverage it implies.
export default function EconomicDebtBridge({ bridge, coverage }) {
  const lines = (bridge?.lines || []).filter((ln) => !DROPPED_KEYS.has(ln.key));
  if (lines.length === 0)
    return <p className="text-sm text-slate-400">No reported debt figure tagged in XBRL.</p>;

  const e = bridge.ebitda;
  const positive = e?.value > 0;
  const cc = coverage || {};
  const chips = positive ? [
    ["EBITDA / interest", cc.ebitda_interest],
    ["Debt / (EBITDA − capex)", cc.debt_ebitda_capex],
    ["(EBITDA − capex) / interest", cc.ebitda_capex_interest],
    ["Capex % of EBITDA", cc.capex_pct_ebitda != null && {
      value: cc.capex_pct_ebitda, display: `${cc.capex_pct_ebitda}%`, derived: true,
      formula: "capex ÷ EBITDA",
    }],
  ].filter(([, cv]) => cv?.display) : [];

  return (
    <div>
      <table className="w-full text-sm">
        <tbody>
          {lines.map((ln, i) => (
            <tr
              key={i}
              className={`border-b border-ink-700/60 ${
                ln.is_total ? "font-semibold text-slate-100" : "text-slate-300"
              }`}
            >
              <td className="py-2">
                {!ln.is_total && (
                  <span className={`mr-2 ${ln.amount?.value < 0 ? "text-emerald-400" : "text-amber-400"}`}>
                    {ln.amount?.value < 0 ? "−" : "+"}
                  </span>
                )}
                {ln.label}
              </td>
              <td className="py-2 text-right">
                <CitedNumber cv={ln.amount} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {e && (
        <p className="mt-3 text-[12px] text-slate-400">
          Leverage divides by EBITDA <CitedNumber cv={e} className="text-slate-200" />
          {e.formula && <span className="text-slate-500"> = {e.formula}</span>}
          {!positive && <span className="text-slate-300"> — not positive, so leverage is n.m.</span>}
        </p>
      )}
      {e?.note && <p className="mt-1 text-[11px] text-amber-300/80">{e.note}</p>}
      {chips.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {chips.map(([label, cv]) => <Chip key={label} label={label} cv={cv} />)}
        </div>
      )}
    </div>
  );
}
