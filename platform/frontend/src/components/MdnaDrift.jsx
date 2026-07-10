import React from "react";
import { ACCENT, RISK } from "../ui/colors.js";

// Dependency-free SVG dual-line chart: semantic drift (left axis) + liquidity-stress tone
// (right axis, 0-100). Marked experimental — we read the trend, not the absolute level.
export default function MdnaDrift({ points }) {
  if (!points || points.length < 2) {
    return (
      <p className="text-sm text-slate-400">
        MD&A drift needs at least two periods of 10-K/10-Q filings in the window.
      </p>
    );
  }

  const W = 760;
  const H = 260;
  const padL = 44;
  const padR = 44;
  const padT = 20;
  const padB = 48;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const n = points.length;
  const x = (i) => padL + (n === 1 ? plotW / 2 : (plotW * i) / (n - 1));

  const drifts = points.map((p) => p.drift_from_prior).filter((v) => v != null);
  const maxDrift = Math.max(0.5, ...drifts, 0.01);
  const yD = (v) => padT + plotH - (v / maxDrift) * plotH;
  const yT = (v) => padT + plotH - (v / 100) * plotH;

  const driftPts = points
    .map((p, i) => (p.drift_from_prior == null ? null : [x(i), yD(p.drift_from_prior)]))
    .filter(Boolean);
  const tonePts = points
    .map((p, i) => (p.liquidity_tone_score == null ? null : [x(i), yT(p.liquidity_tone_score), i]))
    .filter(Boolean);

  const path = (pts) => pts.map(([px, py], i) => `${i ? "L" : "M"}${px},${py}`).join(" ");

  const label = (p) => {
    const d = p.period_end || "";
    return d.slice(2, 7); // YY-MM
  };

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 280 }}>
        {/* gridlines */}
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <line
            key={f}
            x1={padL}
            x2={W - padR}
            y1={padT + plotH * f}
            y2={padT + plotH * f}
            stroke="#1a2230"
            strokeWidth="1"
          />
        ))}
        {/* axis labels */}
        <text x={padL - 6} y={padT + 4} textAnchor="end" fill="#64748b" fontSize="9">
          {maxDrift.toFixed(2)}
        </text>
        <text x={padL - 6} y={padT + plotH} textAnchor="end" fill="#64748b" fontSize="9">
          0
        </text>
        <text x={W - padR + 6} y={padT + 4} textAnchor="start" fill={RISK.high} fontSize="9">
          100
        </text>
        <text x={W - padR + 6} y={padT + plotH} textAnchor="start" fill={RISK.high} fontSize="9">
          0
        </text>

        {/* tone (stress) line — right axis */}
        {tonePts.length > 1 && (
          <path d={path(tonePts.map(([a, b]) => [a, b]))} fill="none" stroke={RISK.high} strokeWidth="2" />
        )}
        {tonePts.map(([px, py, i]) => (
          <g key={`t${i}`}>
            <circle cx={px} cy={py} r="3.5" fill={RISK.high} />
            <text x={px} y={py - 7} textAnchor="middle" fill="#fda4af" fontSize="9" fontFamily="monospace">
              {Math.round(points[i].liquidity_tone_score)}
            </text>
          </g>
        ))}

        {/* drift line — left axis */}
        {driftPts.length > 1 && (
          <path d={path(driftPts)} fill="none" stroke={ACCENT} strokeWidth="2" strokeDasharray="1 0" />
        )}
        {points.map((p, i) =>
          p.drift_from_prior == null ? null : (
            <circle key={`d${i}`} cx={x(i)} cy={yD(p.drift_from_prior)} r="3" fill={ACCENT} />
          )
        )}

        {/* x labels */}
        {points.map((p, i) => (
          <text key={`x${i}`} x={x(i)} y={H - 26} textAnchor="middle" fill="#94a3b8" fontSize="9">
            {label(p)}
          </text>
        ))}
        {points.map((p, i) => (
          <text key={`f${i}`} x={x(i)} y={H - 14} textAnchor="middle" fill="#475569" fontSize="8">
            {p.form_type}
          </text>
        ))}
      </svg>
      <div className="mt-2 flex flex-wrap items-center gap-4 text-[11px] text-slate-400">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-4 rounded bg-accent" /> semantic drift vs prior period
          (TF-IDF cosine)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-4 rounded" style={{ background: RISK.high }} /> liquidity / going-concern
          stress (LLM, 0–100)
        </span>
      </div>
      <p className="mt-1 text-[11px] text-slate-500">
        Directional only — read the trend, not the level.
      </p>
    </div>
  );
}
