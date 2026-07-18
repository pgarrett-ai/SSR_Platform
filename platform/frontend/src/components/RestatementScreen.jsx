import React, { useEffect, useState } from "react";
import { fetchCrisisScreen } from "../api.js";
import CitedNumber from "./CitedNumber.jsx";
import { Badge, ErrorCard, Loading, Section } from "../ui/index.jsx";

// Crisis of confidence (Moyer ch. 8): a restatement/fraud 8-K (Item 4.01/4.02/5.02) is only
// the trigger — it becomes a liquidity crisis when it coincides with an immediate cash need
// cash on hand can't cover. Self-fetches on mount; cash/revolver/immediate-need are cited or
// derived, acceleration/MAC is a best-effort FTS scan (never the full indenture).

function headline(d) {
  if (d.trigger_error) return ["watch", "8-K lookup unavailable"];
  if (d.crisis) return ["high", "crisis of confidence"];
  if (d.triggered) return ["watch", "restatement/fraud flag — liquidity covers it"];
  return ["ok", "no restatement/fraud 8-K"];
}

function Factor({ label, note, children }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
      <span className="font-mono text-sm text-slate-100">{children}</span>
      {note && <span className="text-[11px] text-slate-500">{note}</span>}
    </div>
  );
}

export default function RestatementScreen({ ticker, years }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(false);
    fetchCrisisScreen(ticker, years)
      .then((d) => alive && setData(d))
      .catch(() => alive && setError(true));
    return () => { alive = false; };
  }, [ticker, years]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (error)
    return (
      <Section title="Crisis of confidence"
        subtitle="restatement/fraud trigger vs the four liquidity factors (Moyer ch. 8)">
        <ErrorCard>Crisis screen unavailable for this issuer.</ErrorCard>
      </Section>
    );

  const hl = data ? headline(data) : null;
  const f = data?.factors || {};
  const rev = f.revolver || {};
  const acc = f.acceleration || {};
  const need = f.immediate_need || {};
  const events = need.events || [];

  return (
    <Section
      title="Crisis of confidence"
      subtitle="restatement/fraud trigger vs the four liquidity factors (Moyer ch. 8)"
      right={hl && <Badge tone={hl[0]}>{hl[1]}</Badge>}
    >
      {!data ? (
        <Loading />
      ) : (
        <>
          {data.trigger_error ? (
            <div className="mb-4 text-xs text-amber-300">
              8-K lookup unavailable (EDGAR) — trigger status unknown, not cleared.
            </div>
          ) : data.triggered ? (
            <div className="mb-4 space-y-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-[10px] uppercase tracking-wide text-slate-500">trigger items</span>
                {(data.trigger_items || []).map((it) => (
                  <Badge key={it} tone="high" mono>{it}</Badge>
                ))}
              </div>
              {(data.trigger_filings || []).slice(0, 6).map((fl) => (
                <div key={fl.accession} className="text-xs text-slate-400">
                  <a href={fl.source_url} target="_blank" rel="noreferrer"
                    className="font-mono text-accent hover:underline">
                    ↗ {fl.filing_date}
                  </a>
                  {" — "}{Object.values(fl.triggers || {}).join(" · ")}
                </div>
              ))}
              {(data.trigger_filings || []).length > 6 && (
                <div className="text-[11px] text-slate-500">
                  +{data.trigger_filings.length - 6} earlier trigger filing(s) — most 8-K Item 5.02
                  entries are routine appointments; the 4.01/4.02 flags are the accounting-confidence signal
                </div>
              )}
            </div>
          ) : (
            <div className="mb-4 text-xs text-slate-500">
              No restatement/fraud 8-K (Item 4.01/4.02/5.02) found in the last {years}y.
            </div>
          )}

          <div className="grid gap-x-8 gap-y-4 sm:grid-cols-3">
            <Factor label="1 · Cash on hand">
              <CitedNumber cv={f.cash} />
            </Factor>

            <Factor label="2 · Revolver reliance" note={rev.note}>
              <CitedNumber cv={rev.undrawn} />
              {rev.reliance_pct != null && (
                <span className="ml-2 font-sans text-slate-400">{rev.reliance_pct}% reliance</span>
              )}
            </Factor>

            <Factor label="3 · Acceleration / MAC" note={acc.note || "best-effort"}>
              {acc.available
                ? `${acc.clauses_found} found`
                : <span className="text-slate-500">not indexed</span>}
              {acc.available && acc.sample && (
                <span className="mt-0.5 block max-w-xs truncate font-sans text-[11px] text-slate-500"
                  title={acc.sample}>
                  {acc.sample}
                </span>
              )}
            </Factor>
          </div>

          <div className="mt-4">
            <div className="mb-1 flex items-center gap-2">
              <span className="text-[10px] uppercase tracking-wide text-slate-500">4 · Immediate need</span>
              {need.covered_by_cash === true && <Badge tone="ok">cash covers</Badge>}
              {need.covered_by_cash === false && <Badge tone="high">NOT covered by cash</Badge>}
              {need.covered_by_cash == null && (
                <span className="text-[11px] text-slate-500">
                  {events.length > 0 ? "coverage unknown — cash not tagged" : "no near-term at-risk event"}
                </span>
              )}
            </div>
            {events.length > 0 && (
              <div className="space-y-1">
                {events.map((ev, i) => (
                  <div key={i} className="flex flex-wrap items-center gap-2 text-xs text-slate-300">
                    <span className="font-mono text-slate-400">{ev.date}</span>
                    <span className="text-slate-600">·</span>
                    <span>{ev.kind}</span>
                    <span className="text-slate-600">·</span>
                    <span className="text-slate-400">{ev.instrument}</span>
                    <span className="text-slate-600">·</span>
                    <CitedNumber cv={ev.amount} />
                    {(ev.flags || []).map((fg) => (
                      <Badge key={fg} tone="high">{fg.replace(/_/g, " ")}</Badge>
                    ))}
                  </div>
                ))}
              </div>
            )}
            {need.note && <div className="mt-1 text-[11px] text-slate-500">{need.note}</div>}
          </div>

          {data.note && <div className="mt-4 text-[11px] text-slate-500">{data.note}</div>}
        </>
      )}
    </Section>
  );
}
