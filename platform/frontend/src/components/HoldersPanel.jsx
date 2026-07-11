import React from "react";
import { fetchHolders } from "../api.js";
import { useAsync } from "../cache.js";
import { Section, Td, Th, rowClass } from "../ui/index.jsx";

// Known holders (registered funds) per instrument, from the ingested N-PORT data set.
// Renders its own section, and nothing at all until an ingest has run — no empty promises.

const fmtM = (v) => (v == null ? "—" : `$${(v / 1e6).toFixed(1)}M`);

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
      title="Known holders"
      subtitle={`registered funds via N-PORT · ${data.holdings.length} positions`}
    >
      {[...groups.entries()].map(([instrument, rows]) => (
        <div key={instrument} className="mb-4">
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
              {rows.slice(0, 8).map((h, i) => (
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
          {rows.length > 8 && (
            <div className="mt-1 text-[11px] text-slate-600">+ {rows.length - 8} more funds</div>
          )}
        </div>
      ))}
      <p className="text-[11px] text-slate-500">
        {data.quarter ? `As of ${data.quarter}. ` : ""}{data.note}
      </p>
    </Section>
  );
}
