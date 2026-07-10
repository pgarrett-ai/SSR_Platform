// Single source of truth for every color in the app. Tailwind classes (via
// tailwind.config.js) and SVG/Recharts fills import the same values so the
// two can't drift.
export const INK = {
  900: "#0b0f17",
  800: "#111827",
  700: "#1a2230",
  600: "#263041",
};

export const ACCENT = "#5e7bff";

// Semantic status colors — one red, one amber, one green.
export const RISK = {
  high: "#f43f5e",
  watch: "#f59e0b",
  ok: "#10b981",
};

export const NEUTRAL = "#64748b";

// Categorical series palette for multi-line charts (recovery paths etc.).
export const LINE_COLORS = [
  ACCENT, RISK.ok, RISK.watch, RISK.high, "#a78bfa", "#22d3ee", "#fb923c", "#e879f9",
];
