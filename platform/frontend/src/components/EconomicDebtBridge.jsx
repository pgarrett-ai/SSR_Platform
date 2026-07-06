import React from "react";
import CitedNumber from "./CitedNumber.jsx";

const B = (v) => (v == null ? null : `${v < 0 ? "−" : ""}$${Math.abs(v / 1e9).toFixed(1)}B`);

const TOTAL_SUBLABEL = { reported_debt: "reported", economic_debt: "economic", net_economic_debt: "net" };

function LeverageCallout({ bridge }) {
  const r = bridge.reported_leverage;
  const e = bridge.economic_leverage;
  if (!r && !e) return null;
  return (
    <div className="mb-5 flex flex-wrap items-center gap-4 rounded-lg border border-ink-700 bg-ink-900/60 p-4">
      <div className="flex items-baseline gap-2">
        <span className="text-[11px] uppercase tracking-wide text-slate-500">Reported lev</span>
        <CitedNumber cv={r} className="text-2xl font-bold text-slate-200" />
      </div>
      <span className="text-2xl text-slate-600">→</span>
      <div className="flex items-baseline gap-2">
        <span className="text-[11px] uppercase tracking-wide text-slate-500">Economic lev</span>
        <CitedNumber cv={e} className="text-3xl font-bold text-rose-300" />
      </div>
      {r?.value != null && e?.value != null && (
        <div className={`ml-auto rounded-md px-3 py-1.5 text-sm ${e.value >= r.value ? "bg-rose-500/10 text-rose-200" : "bg-emerald-500/10 text-emerald-200"}`}>
          {e.value >= r.value ? "Hidden leverage: " : "EBITDAR-adjusted: "}
          <span className="font-mono font-semibold">
            {e.value >= r.value ? "+" : ""}{(e.value - r.value).toFixed(1)}x
          </span>{" "}
          turns vs reported
        </div>
      )}
    </div>
  );
}

// Dependency-free SVG waterfall — deterministic, no ResizeObserver/animation, screenshots cleanly.
function WaterfallSvg({ lines }) {
  const W = 760;
  const H = 300;
  const padL = 8;
  const padR = 8;
  const top = 24;
  const baseline = 232; // y of $0
  const labelY = baseline + 14;
  const areaW = W - padL - padR;
  const n = lines.length;
  const slot = areaW / n;
  const barW = Math.min(72, slot * 0.62);

  const maxV = Math.max(...lines.map((l) => l.amount?.value ?? 0), 1);
  const scale = (baseline - top) / maxV;

  let running = 0;
  const bars = lines.map((ln, i) => {
    const v = ln.amount?.value ?? 0;
    const cx = padL + slot * i + slot / 2;
    let yTop, yBot, fill;
    if (ln.is_total) {
      yBot = baseline;
      yTop = baseline - v * scale;
      running = v;
      fill = i === 0 ? "#64748b" : "#fb7185";
    } else {
      const start = running;
      running += v;
      yBot = baseline - start * scale;
      yTop = baseline - running * scale;
      fill = v < 0 ? "#34d399" : "#f59e0b";
    }
    return { ln, v, cx, x: cx - barW / 2, yTop, yBot, fill, isTotal: ln.is_total };
  });

  const shortLabel = (s) =>
    s
      .replace(" liabilities", "")
      .replace(" (underfunded)", "")
      .replace("Pension / OPEB deficit", "Pension/OPEB")
      .replace("Supplier / supply-chain finance", "Supplier fin.")
      .replace("Securitized / factored receivables (recourse)", "Securitization")
      .replace("Guarantees of external / JV / SPE debt", "Guarantees")
      .replace("Less: cash & equivalents", "Cash")
      .replace("Less: restricted cash", "Restricted cash")
      .replace("Net economic (adjusted) debt", "Net econ. debt");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 320 }}>
      <line x1={padL} y1={baseline} x2={W - padR} y2={baseline} stroke="#263041" strokeWidth="1" />
      {bars.map((b, i) => (
        <g key={i}>
          {/* connector from previous running level */}
          {i > 0 && (
            <line
              x1={bars[i - 1].cx + barW / 2}
              y1={b.isTotal ? baseline : b.yBot}
              x2={b.x}
              y2={b.isTotal ? baseline : b.yBot}
              stroke="#3b475a"
              strokeDasharray="3 3"
              strokeWidth="1"
            />
          )}
          <rect
            x={b.x}
            y={Math.min(b.yTop, b.yBot)}
            width={barW}
            height={Math.max(2, Math.abs(b.yBot - b.yTop))}
            fill={b.fill}
            rx="2"
          />
          <text x={b.cx} y={Math.min(b.yTop, b.yBot) - 6} textAnchor="middle" fill="#cbd5e1" fontSize="11" fontFamily="monospace">
            {B(b.v)}
          </text>
          <text x={b.cx} y={labelY} textAnchor="middle" fill="#94a3b8" fontSize="10">
            {shortLabel(b.ln.label).length > 16
              ? shortLabel(b.ln.label).slice(0, 15) + "…"
              : shortLabel(b.ln.label)}
          </text>
          {b.isTotal && (
            <text x={b.cx} y={labelY + 13} textAnchor="middle" fill="#64748b" fontSize="9">
              {TOTAL_SUBLABEL[b.ln.key] || ""}
            </text>
          )}
        </g>
      ))}
    </svg>
  );
}

export default function EconomicDebtBridge({ bridge }) {
  if (!bridge || !bridge.lines || bridge.lines.length === 0) {
    return (
      <p className="text-sm text-slate-400">
        The economic-debt bridge populates from footnote/OBS extraction. Run with an Anthropic API
        key set to add leases, pension/OPEB deficit, supplier finance, guarantees, securitizations
        and take-or-pay to reported debt — each line citation-linked.
      </p>
    );
  }

  return (
    <div>
      <LeverageCallout bridge={bridge} />
      <WaterfallSvg lines={bridge.lines} />

      <table className="mt-4 w-full text-sm">
        <tbody>
          {bridge.lines.map((ln, i) => (
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
                <CitedNumber cv={ln.amount} className={ln.is_total ? "text-base" : ""} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {bridge.ebitda && (
        <p className="mt-2 text-[11px] text-slate-500">
          Reported leverage vs EBITDA <CitedNumber cv={bridge.ebitda} /> (proxy).{" "}
          {bridge.ebitdar ? (
            <>Economic leverage vs EBITDAR <CitedNumber cv={bridge.ebitdar} /> — lease cost added
            back since lease liabilities sit in economic debt. </>
          ) : null}
          Lease amounts are from XBRL; pension, supplier finance and other lines are from the
          footnotes (hover for the quote).
        </p>
      )}
    </div>
  );
}
