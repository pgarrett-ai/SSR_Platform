import React from "react";

export default function Section({ title, subtitle, badge, children, id }) {
  return (
    <section id={id} className="mb-8">
      <div className="mb-3 flex items-baseline gap-3">
        <h2 className="text-lg font-semibold text-slate-100">{title}</h2>
        {badge && (
          <span className="rounded-full border border-amber-400/40 bg-amber-400/10 px-2 py-0.5 text-[10px] uppercase tracking-wide text-amber-300">
            {badge}
          </span>
        )}
        {subtitle && <span className="text-sm text-slate-400">{subtitle}</span>}
      </div>
      <div className="rounded-xl border border-ink-700 bg-ink-800/50 p-4">{children}</div>
    </section>
  );
}
