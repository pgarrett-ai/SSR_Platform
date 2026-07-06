import React from "react";

export default function ProgressLog({ events, done }) {
  if (!events || events.length === 0) return null;
  const pct = events[events.length - 1]?.pct ?? 0;
  return (
    <div className="mb-8 rounded-xl border border-ink-700 bg-ink-800/50 p-4">
      <div className="mb-2 h-1.5 w-full overflow-hidden rounded-full bg-ink-700">
        <div
          className="h-full rounded-full bg-accent transition-all duration-300"
          style={{ width: `${done ? 100 : pct}%` }}
        />
      </div>
      <div className="max-h-40 overflow-y-auto font-mono text-[12px] leading-relaxed text-slate-400">
        {events.map((e, i) => (
          <div key={i} className={i === events.length - 1 ? "text-slate-200" : ""}>
            <span className="text-slate-600">{e.pct != null ? `${e.pct}%` : "  "}</span>{" "}
            {e.message}
          </div>
        ))}
      </div>
    </div>
  );
}
