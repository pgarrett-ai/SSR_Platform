import React from "react";
import { fetchHolders } from "../api.js";
import { useAsync } from "../cache.js";
import { Section, Td, Th, rowClass, useShowMore } from "../ui/index.jsx";

// Known holders (registered funds) per instrument, from the ingested N-PORT data set.
// Renders its own section, and nothing at all until an ingest has run — no empty promises.

const fmtM = (v) => (v == null ? "—" : `$${(v / 1e6).toFixed(1)}M`);

// One instrument group — its own component so each group owns a useShowMore.
function HolderGroup({ instrument, rows }) {
  const { shown, control } = useShowMore(rows, 8, "funds");
  return (
    <div className="mb-4">
      <div className="mb-1 text-[12px] font-semibold text-slate-200">{instrument}</div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-ink-600">
            <Th>Fund</Th>
            <Th>Issue</Th>
            <Th right>Position</Th>
            <Th right>% of fund</Th>
          </tr>
        </thead>
        <tbody>
          {shown.map((h, i) => (
            <tr key={i} className={rowClass}>
              <Td className="text-slate-300">{h.fund_name || "—"}</Td>
              <Td className="text-[12px] text-slate-500">{h.title || "—"}</Td>
              <Td right mono className="text-slate-300">{fmtM(h.value_usd)}</Td>
              <Td right mono className="text-slate-400">
                {h.pct_of_fund == null ? "—" : `${h.pct_of_fund.toFixed(2)}%`}
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
      {control}
    </div>
  );
}

export default function HoldersPanel({ ticker }) {
  const { data } = useAsync(`holders:${ticker}`, () => fetchHolders(ticker), [ticker]);
  if (!data?.holdings?.length) return null;

  // group by matched instrument; issuer-level (unmatched) paper last
  const groups = new Map();
  for (const h of data.holdings) {
    const key = h.instrument || "Unmatched issuer paper";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(h);
  }

  return (
    <Section
      collapsible
      title="Known holders"
      subtitle={`registered funds via N-PORT · ${data.holdings.length} positions`}
    >
      {[...groups.entries()].map(([instrument, rows]) => (
        <HolderGroup key={instrument} instrument={instrument} rows={rows} />
      ))}
      <p className="text-[11px] text-slate-500">
        {data.quarter ? `As of ${data.quarter}. ` : ""}{data.note}
      </p>
    </Section>
  );
}
