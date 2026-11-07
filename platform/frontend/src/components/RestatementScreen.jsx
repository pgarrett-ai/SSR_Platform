import React from "react";
import { fetchCrisisScreen } from "../api.js";
import { useAsync } from "../cache.js";
import { Badge, Section } from "../ui/index.jsx";

// An auditor change (8-K Item 4.01) or non-reliance on prior financials (4.02) only turns
// into a liquidity crisis when an at-risk obligation is not covered by cash on hand. Item
// 5.02 rides the same screen but is mostly routine appointments, so it is left out here.
// Renders nothing unless a 4.01/4.02 filing exists. The screen reads the cached overview,
// so it waits for `ready`: an answer from before the overview exists is empty, and the
// session cache would keep it.
const ACCOUNTING = ["4.01", "4.02"];

export default function RestatementScreen({ ticker, years, ready }) {
  const { data } = useAsync(ready ? `crisis:${ticker}:${years}` : null,
    () => fetchCrisisScreen(ticker, years), [ticker, years, ready]);
  const filings = (data?.trigger_filings || [])
    .filter((fl) => ACCOUNTING.some((it) => fl.triggers?.[it]));
  if (!filings.length) return null;
  const covered = data.factors?.immediate_need?.covered_by_cash;

  return (
    <Section title="Accounting triggers" subtitle="8-K Item 4.01 auditor change · 4.02 non-reliance"
      right={<Badge tone={data.crisis ? "high" : "watch"}>{data.crisis ? "crisis of confidence" : "trigger on file"}</Badge>}>
      <div className="mb-2 text-sm text-slate-300">
        {data.crisis
          ? "An at-risk obligation is not covered by cash on hand."
          : covered == null
            ? "Cash coverage of near-term obligations could not be determined."
            : "Near-term at-risk obligations are covered by cash on hand."}
      </div>
      {filings.map((fl) => (
        <div key={fl.accession} className="text-xs text-slate-400">
          <a href={fl.source_url} target="_blank" rel="noreferrer"
            className="font-mono text-accent hover:underline">
            ↗ {fl.filing_date}
          </a>
          {" — "}{ACCOUNTING.filter((it) => fl.triggers[it]).map((it) => `${it} ${fl.triggers[it]}`).join(" · ")}
        </div>
      ))}
    </Section>
  );
}
