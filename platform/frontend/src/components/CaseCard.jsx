import React, { useEffect, useRef, useState } from "react";
import { fetchCh11Case } from "../api.js";
import CitedNumber from "./CitedNumber.jsx";
import { Badge, Section } from "../ui/index.jsx";

// Ch 11 case card (Moyer ch. 12): statutory clocks off the petition date (pure date math,
// IrrMatrix-style frontend calc). Petition date is seeded + cited from the 8-K Item 1.03
// filing via /recovery/case, editable. Case type is an analyst toggle — only free-fall has
// a data proxy (a pre-filing revolver drawdown), so it is suggested, never auto-classified.

const DAY = 86400000;
const EXCLUSIVITY_DAYS = 120;   // §1121 plan exclusivity (routinely extended; statutory cap 18 mo)
const SOLICITATION_DAYS = 180;  // acceptance/solicitation period
const BENCHMARK_MONTHS = 14;    // Moyer: average time in ch.11 ≈ 14 mo (range 11.5–19.1)

const dateCls =
  "rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 font-mono text-xs text-slate-100 outline-none focus:border-accent";

function addDays(iso, n) {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export default function CaseCard({ ticker, years, petitionDate, setPetitionDate }) {
  const [caseInfo, setCaseInfo] = useState(null);
  const [caseType, setCaseType] = useState("unclassified");
  const seededFor = useRef(null);   // ticker we've already seeded, so a new issuer reseeds

  useEffect(() => {
    let alive = true;
    fetchCh11Case(ticker, years)
      .then((d) => {
        if (!alive) return;
        setCaseInfo(d);
        // seed the shared petition date from the 8-K once per issuer (don't clobber edits)
        if (seededFor.current !== ticker && d?.petition_date?.value) {
          setPetitionDate(d.petition_date.value);
          seededFor.current = ticker;
        }
      })
      .catch(() => alive && setCaseInfo(null));
    return () => { alive = false; };
  }, [ticker, years]);   // eslint-disable-line react-hooks/exhaustive-deps

  const valid = /^\d{4}-\d{2}-\d{2}$/.test(petitionDate || "");
  const daysElapsed = valid ? Math.floor((Date.now() - new Date(petitionDate + "T00:00:00Z")) / DAY) : null;
  const monthsElapsed = daysElapsed != null ? daysElapsed / 30.44 : null;
  const exclusivityEnd = valid ? addDays(petitionDate, EXCLUSIVITY_DAYS) : null;
  const solicitationEnd = valid ? addDays(petitionDate, SOLICITATION_DAYS) : null;
  const barPct = monthsElapsed != null ? Math.min((monthsElapsed / BENCHMARK_MONTHS) * 100, 130) : 0;
  const overBench = monthsElapsed != null && monthsElapsed > BENCHMARK_MONTHS;

  const clock = (label, endIso, elapsedDays, windowDays) => {
    const remaining = windowDays - (elapsedDays ?? 0);
    return (
      <div className="flex flex-col gap-0.5">
        <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
        <span className="font-mono text-sm text-slate-100">{endIso || "—"}</span>
        <span className={`text-[11px] ${remaining < 0 ? "text-rose-300" : "text-slate-400"}`}>
          {endIso == null ? "" : remaining >= 0 ? `${remaining}d remaining` : `${-remaining}d past (extended)`}
        </span>
      </div>
    );
  };

  return (
    <Section
      title="Chapter 11 case"
      subtitle="statutory clocks off the petition date · case type is your call (Moyer ch. 12)"
    >
      <div className="flex flex-wrap items-end gap-x-8 gap-y-4">
        <div className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wide text-slate-500">
            Petition date
            {caseInfo?.petition_date?.citation && (
              <CitedNumber cv={{ display: " §", citation: caseInfo.petition_date.citation }} className="text-accent" />
            )}
          </span>
          <input type="date" value={petitionDate || ""} onChange={(e) => setPetitionDate(e.target.value)} className={dateCls} />
          <span className="text-[11px] text-slate-500">
            {caseInfo?.petition_error
              ? "petition lookup unavailable (EDGAR) — enter manually"
              : caseInfo?.petition_date?.value
                ? "seeded from 8-K Item 1.03"
                : "no 8-K 1.03 found — enter manually"}
          </span>
        </div>

        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wide text-slate-500">Time in case</span>
          <span className="font-mono text-sm text-slate-100">
            {monthsElapsed != null ? `${monthsElapsed.toFixed(1)} mo` : "—"}
          </span>
          <span className="text-[11px] text-slate-400">{daysElapsed != null ? `${daysElapsed} days` : ""}</span>
        </div>

        {clock("Plan exclusivity (120d)", exclusivityEnd, daysElapsed, EXCLUSIVITY_DAYS)}
        {clock("Solicitation (180d)", solicitationEnd, daysElapsed, SOLICITATION_DAYS)}

        <div className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wide text-slate-500">Case type</span>
          <select value={caseType} onChange={(e) => setCaseType(e.target.value)}
            className="rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-accent">
            <option value="unclassified">unclassified</option>
            <option value="prepack">prepackaged</option>
            <option value="prenegotiated">prenegotiated</option>
            <option value="freefall">free-fall</option>
          </select>
        </div>
      </div>

      {monthsElapsed != null && (
        <div className="mt-5">
          <div className="mb-1 flex justify-between text-[10px] uppercase tracking-wide text-slate-500">
            <span>vs ~14-month benchmark</span>
            <span>{overBench ? "over benchmark" : "within benchmark"}</span>
          </div>
          <div className="relative h-2.5 w-full max-w-md rounded-full bg-ink-800">
            {/* fill and marker share one 0–130% scale (barPct caps at 130); 14 mo sits at 100/130 */}
            <div className={`h-2.5 rounded-full ${overBench ? "bg-rose-400/70" : "bg-accent/70"}`}
              style={{ width: `${Math.min(barPct * (100 / 130), 100)}%` }} />
            <div className="absolute top-[-2px] h-[14px] w-px bg-slate-400"
              style={{ left: `${100 / 130 * 100}%` }}
              title="14-month average (range 11.5–19.1)" />
          </div>
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <Badge tone="info" title="prepacks confirm in under ~45 days; free-fall cases run 1–3 years">
          prepack &lt;45d · free-fall 1–3y
        </Badge>
        {caseInfo?.note && <span className="text-[11px] text-slate-500">{caseInfo.note}</span>}
      </div>
    </Section>
  );
}
