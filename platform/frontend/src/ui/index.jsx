// The app's single set of presentational primitives: cards, sections, badges,
// stats, table cells, gauge, formatters. Every page composes these — no page
// defines its own card/section/badge styles.
import React, { useMemo, useState } from "react";
import { ACCENT, INK, NEUTRAL, RISK } from "./colors.js";

export { ACCENT, INK, LINE_COLORS, NEUTRAL, RISK } from "./colors.js";

/* ---------- formatters ---------- */

export const fmtPct = (x, dp = 1) =>
  x == null || Number.isNaN(x) ? "—" : `${(x * 100).toFixed(dp)}%`;
export const fmtNum = (x, dp = 2) =>
  x == null || Number.isNaN(x) ? "—" : Number(x).toFixed(dp);
export const fmtX = (x, dp = 1) =>
  x == null || Number.isNaN(x) ? "—" : `${Number(x).toFixed(Math.abs(x) < 1 ? 2 : dp)}x`;
// Leverage where negative means negative EBITDA (screener columns) — not net cash.
export const fmtLev = (x, dp = 1) =>
  x == null || Number.isNaN(x) ? "—"
  : x < 0
    ? <span className="text-slate-600" title="not meaningful — negative EBITDA">n.m.</span>
    : fmtX(x, dp);
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

export function Badge({ tone = "neutral", mono = false, className = "", children }) {
  return (
    <span
      className={`inline-block rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${mono ? "font-mono" : ""} ${BADGE_TONES[tone] || BADGE_TONES.neutral} ${className}`}
    >
      {children}
    </span>
  );
}

export function Loading({ className = "", children = "Loading…" }) {
  return <div className={`py-8 text-center text-sm text-slate-500 ${className}`}>{children}</div>;
}

export function ErrorCard({ className = "", children }) {
  return (
    <div className={`rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-200 ${className}`}>
      {children}
    </div>
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

const slugify = (s) =>
  String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");

// Section: every page block. `collapsible` renders a native <details> (keyboard/aria
// free; fragment navigation auto-opens a collapsed section in Chromium), with the open
// state persisted globally per section id — collapse "Sources" once, it stays collapsed
// for every issuer. Anchors always work: id defaults to a slug of the title and
// scroll-mt clears the sticky header.
export function Section({
  title, subtitle, badge, right, flush = false, id, className = "",
  collapsible = false, defaultOpen = true, children,
}) {
  const secId = id || slugify(title);
  const storeKey = `ui:sec:${secId}`;
  const [open, setOpen] = useState(() => {
    if (!collapsible) return true;
    try {
      const saved = localStorage.getItem(storeKey);
      return saved == null ? defaultOpen : saved === "1";
    } catch {
      return defaultOpen;
    }
  });

  // badge: legacy string (watch tone) or {label, tone, title} for state-specific tones
  const b = badge ? (typeof badge === "string" ? { label: badge, tone: "watch" } : badge) : null;
  const header = (
    <div className="mb-2 flex items-baseline justify-between gap-3">
      <div className="flex items-baseline gap-2">
        {collapsible && (
          <span aria-hidden className="text-[10px] text-slate-500">{open ? "▾" : "▸"}</span>
        )}
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">{title}</h2>
        {b && (
          <span title={b.title}>
            <Badge tone={b.tone || "watch"}>{b.label}</Badge>
          </span>
        )}
        {subtitle && <span className="text-xs text-slate-500">{subtitle}</span>}
      </div>
      {/* controls in the right slot must never toggle the disclosure */}
      {right && <span onClick={(e) => e.stopPropagation()}>{right}</span>}
    </div>
  );
  const body = flush ? children : <Card>{children}</Card>;

  if (!collapsible) {
    return (
      <section id={secId} className={`mb-6 scroll-mt-16 ${className}`}>
        {header}
        {body}
      </section>
    );
  }
  return (
    <section id={secId} className={`mb-6 scroll-mt-16 ${className}`}>
      <details
        open={open}
        onToggle={(e) => {
          if (e.target !== e.currentTarget) return;   // ignore nested <details>
          const v = e.target.open;
          setOpen(v);
          try { localStorage.setItem(storeKey, v ? "1" : "0"); } catch { /* private mode */ }
        }}
      >
        <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden">
          {header}
        </summary>
        {body}
      </details>
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

/* ---------- disclosure / state primitives ---------- */

// Muted convention: user-excluded = strikethrough; not-quantifiable/not-applicable =
// dimmed with NO pointer affordance. Bare text-slate-600 is reserved for "—"/zero
// placeholders — never for interactive or toggled state.
export const MUTED_EXCLUDED = "line-through text-slate-500";
export const MUTED_NA = "opacity-60";

// THE canonical rolled-up-list idiom: cap a list at `initial`, one accent control to
// expand/re-collapse. A hook (not a wrapper) so it composes with <tbody>, lists, grids.
export function useShowMore(items = [], initial, label = "items") {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, initial);
  const control =
    items.length > initial ? (
      <button
        onClick={() => setAll((v) => !v)}
        className="mt-2 text-[11px] text-accent hover:underline"
      >
        {all ? "show fewer" : `show all ${items.length} ${label}`}
      </button>
    ) : null;
  return { shown, control, expanded: all };
}

// Terse empty state: what's missing, optionally why/what to do about it.
export function EmptyState({ children, hint, action }) {
  return (
    <div className="py-6 text-center">
      <div className="text-sm text-slate-500">{children}</div>
      {hint && <div className="mt-1 text-xs text-slate-600">{hint}</div>}
      {action && (
        <Button onClick={action.onClick} className="mt-3">
          {action.label}
        </Button>
      )}
    </div>
  );
}

// Table-shaped loading placeholder — replaces stacked bare "Loading…" texts.
export function Skeleton({ rows = 3, className = "" }) {
  const widths = ["w-full", "w-11/12", "w-4/5", "w-2/3"];
  return (
    <div aria-hidden className={`animate-pulse motion-reduce:animate-none space-y-2 py-2 ${className}`}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className={`h-4 rounded bg-ink-700/60 ${widths[i % widths.length]}`} />
      ))}
    </div>
  );
}

/* ---------- table primitives ---------- */

export const rowClass = "border-b border-ink-700/60 hover:bg-ink-700/30";

export function Th({ right = false, className = "", title, onClick, sorted, children }) {
  // title carries load-bearing methodology in this app — make it visibly hoverable
  // with the same dotted-underline convention cited numbers use (.cite-link).
  const label = title ? <span className="cite-link">{children}</span> : children;
  const arrow = sorted ? (
    <span className="ml-0.5 text-accent">{sorted === "desc" ? "▼" : "▲"}</span>
  ) : null;
  return (
    <th
      title={title}
      aria-sort={sorted ? (sorted === "desc" ? "descending" : "ascending") : undefined}
      className={`px-2 py-2 text-[11px] font-medium uppercase tracking-wide text-slate-500 ${right ? "text-right" : "text-left"} ${className}`}
    >
      {onClick ? (
        <button
          onClick={onClick}
          className="text-[11px] font-medium uppercase tracking-wide hover:text-slate-300"
        >
          {label}
          {arrow}
        </button>
      ) : (
        <>
          {label}
          {arrow}
        </>
      )}
    </th>
  );
}

// Shared column-sort state (extracted from ScreenTable): nulls last both directions,
// numbers numerically, strings lexicographically. defaultKey null = server order
// until the first click. Spread thProps(key) onto a sortable <Th>.
export function useSort(rows = [], defaultKey = null, defaultDir = "desc") {
  const [sort, setSort] = useState({ key: defaultKey, dir: defaultDir });
  const sorted = useMemo(() => {
    if (!sort.key) return rows;
    return [...rows].sort((a, b) => {
      const av = a[sort.key], bv = b[sort.key];
      if (av == null) return 1;
      if (bv == null) return -1;
      const d = typeof av === "string" || typeof bv === "string"
        ? String(av).localeCompare(String(bv))
        : av - bv;
      return sort.dir === "desc" ? -d : d;
    });
  }, [rows, sort]);
  const thProps = (key) => ({
    onClick: () =>
      setSort((s) => ({ key, dir: s.key === key && s.dir === "desc" ? "asc" : "desc" })),
    sorted: sort.key === key ? sort.dir : null,
  });
  return { sorted, thProps, sort };
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
// ---- dense form primitives shared by the Recovery tool cards ----------------------
// (one definition — these were copy-pasted per component before the consolidation)

export const NUM_CLS =
  "w-28 rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 font-mono text-xs text-slate-100 outline-none focus:border-accent";

export function Field({ label, title, children }) {
  return (
    <label className="flex flex-col gap-1" title={title}>
      <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  );
}

export function NumField({ label, title, value, onChange, step }) {
  return (
    <Field label={label} title={title}>
      <input type="number" step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))} className={NUM_CLS} />
    </Field>
  );
}

export function NumCell({ value, onChange, step = 1, className = "" }) {
  return (
    <input
      type="number"
      step={step}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
      className={`w-24 rounded border border-ink-600 bg-ink-800 px-2 py-1 font-mono text-xs text-slate-100 outline-none focus:border-accent ${className}`}
    />
  );
}

export function TextCell({ value, onChange, className = "" }) {
  return (
    <input
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      className={`w-full rounded border border-ink-600 bg-ink-800 px-2 py-1 text-xs text-slate-100 outline-none focus:border-accent ${className}`}
    />
  );
}

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
