// Shared presentational helpers: cards, section headers, stat blocks, a risk gauge, formatters.
import React from "react";

export const fmtPct = (x, dp = 1) =>
  x == null || Number.isNaN(x) ? "—" : `${(x * 100).toFixed(dp)}%`;
export const fmtNum = (x, dp = 2) =>
  x == null || Number.isNaN(x) ? "—" : Number(x).toFixed(dp);
export const fmtX = (x, dp = 1) =>
  x == null || Number.isNaN(x) ? "—" : `${Number(x).toFixed(dp)}x`;

export function fmtMoney(x) {
  if (x == null || Number.isNaN(x)) return "—";
  const a = Math.abs(x);
  const sign = x < 0 ? "-" : "";
  if (a >= 1e12) return `${sign}$${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(0)}M`;
  return `${sign}$${a.toFixed(0)}`;
}

// Risk band (0-100): green safe -> amber -> red distress.
export function riskColor(r) {
  if (r == null) return "#64748b";
  if (r >= 66) return "#f43f5e"; // rose
  if (r >= 33) return "#f59e0b"; // amber
  return "#10b981"; // emerald
}

export function Section({ title, subtitle, children, right }) {
  return (
    <section className="mb-6">
      <div className="flex items-baseline justify-between mb-2">
        <div>
          <h2 className="text-sm font-semibold tracking-wide text-slate-200">{title}</h2>
          {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

export function Card({ children, className = "" }) {
  return (
    <div className={`rounded-xl border border-ink-600 bg-ink-800/60 p-4 ${className}`}>
      {children}
    </div>
  );
}

export function Stat({ label, value, sub, color }) {
  return (
    <Card>
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold" style={color ? { color } : undefined}>
        {value}
      </div>
      {sub && <div className="mt-1 text-xs text-slate-400">{sub}</div>}
    </Card>
  );
}

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
      {arc(0, 60, "#10b981", 10)}
      {arc(60, 120, "#f59e0b", 10)}
      {arc(120, 180, "#f43f5e", 10)}
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

export const ZONE_COLOR = { safe: "#10b981", grey: "#f59e0b", distress: "#f43f5e" };
