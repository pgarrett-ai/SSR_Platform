// The app's single set of presentational primitives: cards, sections, badges,
// stats, table cells, gauge, formatters. Every page composes these — no page
// defines its own card/section/badge styles.
import React from "react";
import { ACCENT, INK, NEUTRAL, RISK } from "./colors.js";

export { ACCENT, INK, LINE_COLORS, NEUTRAL, RISK } from "./colors.js";

/* ---------- formatters ---------- */

export const fmtPct = (x, dp = 1) =>
  x == null || Number.isNaN(x) ? "—" : `${(x * 100).toFixed(dp)}%`;
export const fmtNum = (x, dp = 2) =>
  x == null || Number.isNaN(x) ? "—" : Number(x).toFixed(dp);
export const fmtX = (x, dp = 1) =>
  x == null || Number.isNaN(x) ? "—" : `${Number(x).toFixed(dp)}x`;
export const fmt = (v, d = 1) =>
  v == null || Number.isNaN(v)
    ? "—"
    : Number(v).toLocaleString("en-US", { maximumFractionDigits: d });

export function fmtMoney(x) {
  if (x == null || Number.isNaN(x)) return "—";
  const a = Math.abs(x);
  const sign = x < 0 ? "-" : "";
  if (a >= 1e12) return `${sign}$${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(0)}M`;
  return `${sign}$${a.toFixed(0)}`;
}

/* ---------- status colors ---------- */

// Risk band (0-100): green safe -> amber -> red distress. 33/66 everywhere.
export function riskColor(r) {
  if (r == null) return NEUTRAL;
  if (r >= 66) return RISK.high;
  if (r >= 33) return RISK.watch;
  return RISK.ok;
}

export const ZONE_COLOR = { safe: RISK.ok, grey: RISK.watch, distress: RISK.high };

// One shared Recharts tooltip style (replaces the per-chart inline objects).
export const chartTooltipStyle = {
  background: INK[800],
  border: `1px solid ${INK[600]}`,
  borderRadius: 8,
  fontSize: 11,
};

/* ---------- layout primitives ---------- */

export function Card({ pad = "p-4", className = "", children }) {
  return (
    <div className={`rounded-xl border border-ink-700 bg-ink-800/50 ${pad} ${className}`}>
      {children}
    </div>
  );
}

const BADGE_TONES = {
  high: "bg-rose-500/15 text-rose-300",
  watch: "bg-amber-500/15 text-amber-300",
  ok: "bg-emerald-500/15 text-emerald-300",
  info: "bg-sky-500/15 text-sky-300",
  neutral: "bg-ink-700 text-slate-400",
  accent: "bg-accent/15 text-accent",
};

export function Badge({ tone = "neutral", className = "", children }) {
  return (
    <span
      className={`inline-block rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${BADGE_TONES[tone] || BADGE_TONES.neutral} ${className}`}
    >
      {children}
    </span>
  );
}

/* ---------- form primitives ---------- */

const BUTTON_VARIANTS = {
  primary: "bg-accent font-semibold text-white hover:bg-accent/90",
  ghost: "border border-ink-600 text-slate-300 hover:border-accent hover:text-white",
};

export function Button({ variant = "ghost", className = "", ...props }) {
  return (
    <button
      className={`rounded-md px-3 py-1.5 text-sm disabled:opacity-50 ${BUTTON_VARIANTS[variant] || BUTTON_VARIANTS.ghost} ${className}`}
      {...props}
    />
  );
}

export const Input = React.forwardRef(function Input({ className = "", ...props }, ref) {
  return (
    <input
      ref={ref}
      className={`rounded-md border border-ink-600 bg-ink-800 px-3 py-1.5 text-sm text-slate-100 outline-none focus:border-accent ${className}`}
      {...props}
    />
  );
});

export function Section({ title, subtitle, badge, right, flush = false, id, className = "", children }) {
  return (
    <section id={id} className={`mb-6 ${className}`}>
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <div className="flex items-baseline gap-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">{title}</h2>
          {badge && <Badge tone="watch">{badge}</Badge>}
          {subtitle && <span className="text-xs text-slate-500">{subtitle}</span>}
        </div>
        {right}
      </div>
      {flush ? children : <Card>{children}</Card>}
    </section>
  );
}

export function Stat({ label, value, sub, color, bare = false }) {
  const body = (
    <>
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div
        className={bare ? "font-mono text-sm text-slate-200" : "mt-1 text-2xl font-semibold"}
        style={color ? { color } : undefined}
      >
        {value ?? "—"}
      </div>
      {sub && <div className="mt-1 text-xs text-slate-400">{sub}</div>}
    </>
  );
  return bare ? <div className="flex flex-col">{body}</div> : <Card>{body}</Card>;
}

/* ---------- table primitives ---------- */

export const rowClass = "border-b border-ink-700/60 hover:bg-ink-700/30";

export function Th({ right = false, className = "", children }) {
  return (
    <th
      className={`px-2 py-2 text-[11px] font-medium uppercase tracking-wide text-slate-500 ${right ? "text-right" : "text-left"} ${className}`}
    >
      {children}
    </th>
  );
}

export function Td({ right = false, mono = false, className = "", children }) {
  return (
    <td
      className={`px-2 py-1.5 text-sm ${right ? "text-right" : ""} ${mono ? "font-mono tabular-nums" : ""} ${className}`}
    >
      {children}
    </td>
  );
}

/* ---------- gauge ---------- */

// Semicircular gauge for the 0-100 overall risk score.
export function Gauge({ value, size = 150 }) {
  const v = value == null ? null : Math.max(0, Math.min(100, value));
  const r = size / 2 - 12;
  const cx = size / 2;
  const cy = size / 2 + 4;
  const polar = (deg) => {
    const a = (Math.PI * (180 - deg)) / 180;
    return [cx + r * Math.cos(a), cy - r * Math.sin(a)];
  };
  const arc = (from, to, color, w) => {
    const [x1, y1] = polar(from);
    const [x2, y2] = polar(to);
    const large = to - from > 180 ? 1 : 0;
    return (
      <path
        d={`M ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2}`}
        fill="none"
        stroke={color}
        strokeWidth={w}
        strokeLinecap="round"
      />
    );
  };
  const needleDeg = v == null ? 90 : (v / 100) * 180;
  const [nx, ny] = polar(needleDeg);
  return (
    <svg width={size} height={size * 0.62} viewBox={`0 0 ${size} ${size * 0.62}`}>
      {arc(0, 60, RISK.ok, 10)}
      {arc(60, 120, RISK.watch, 10)}
      {arc(120, 180, RISK.high, 10)}
      {v != null && (
        <>
          <line x1={cx} y1={cy} x2={nx} y2={ny} stroke="#e5e9f0" strokeWidth={2.5} />
          <circle cx={cx} cy={cy} r={4} fill="#e5e9f0" />
        </>
      )}
      <text x={cx} y={cy - 6} textAnchor="middle" className="fill-slate-100"
        style={{ fontSize: 22, fontWeight: 700 }}>
        {v == null ? "—" : Math.round(v)}
      </text>
    </svg>
  );
}
