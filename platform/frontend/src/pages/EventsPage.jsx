import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchEvents } from "../api.js";
import { Badge, Button, ErrorCard, Input, Loading, Td, Th, rowClass } from "../ui/index.jsx";
import { DetectedStamp, SevBadge } from "../components/EventBits.jsx";

// Keep in sync with the PR-3 detector registry (ITEM_SPECS event_types + form/ratings).
const TYPE_OPTIONS = [
  ["bankruptcy", "1.03 bankruptcy"],
  ["acceleration", "2.04 acceleration"],
  ["non_reliance", "4.02 restatement"],
  ["auditor_change", "4.01 auditor"],
  ["delisting_notice", "3.01 delisting"],
  ["officer_change", "5.02 officers"],
  ["new_debt_obligation", "2.03 new debt"],
  ["impairment", "2.06 impairment"],
  ["late_filing", "NT late"],
  ["delisting", "Form 25"],
  ["deregistration", "Form 15"],
  ["insider_filing", "Form 4"],
  ["stake_13d", "13D"],
  ["stake_13g", "13G"],
  ["rating_default", "rating D"],
];

const PAGE = 100;

export default function EventsPage() {
  const [types, setTypes] = useState([]);
  const [minSev, setMinSev] = useState(0);
  const [tickerDraft, setTickerDraft] = useState("");
  const [ticker, setTicker] = useState("");     // committed on submit only
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);

  async function load(offset = 0) {
    setLoading(true);
    setError(null);
    try {
      const d = await fetchEvents({
        ticker: ticker || undefined,
        event_type: types,
        min_severity: minSev || undefined,
        limit: PAGE,
        offset,
      });
      setRows((prev) => (offset === 0 ? d.events : [...prev, ...d.events]));
      setDone(d.events.length < PAGE);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, types, minSev]);

  const toggleType = (slug) =>
    setTypes((prev) => (prev.includes(slug) ? prev.filter((t) => t !== slug) : [...prev, slug]));

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold text-slate-100">Events feed</h1>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {TYPE_OPTIONS.map(([slug, label]) => (
          <button
            key={slug}
            onClick={() => toggleType(slug)}
            className={`rounded-full border px-2.5 py-0.5 text-[11px] ${
              types.includes(slug)
                ? "border-accent text-white"
                : "border-ink-600 text-slate-400 hover:border-accent"
            }`}
          >
            {label}
          </button>
        ))}
        <select
          value={minSev}
          onChange={(e) => setMinSev(Number(e.target.value))}
          className="rounded-md border border-ink-600 bg-ink-800 px-2 py-1 text-xs text-slate-300"
        >
          <option value={0}>any severity</option>
          {[2, 3, 4, 5].map((s) => (
            <option key={s} value={s}>≥ S{s}</option>
          ))}
        </select>
        <form
          onSubmit={(e) => { e.preventDefault(); setTicker(tickerDraft.trim().toUpperCase()); }}
          className="ml-auto"
        >
          <Input
            value={tickerDraft}
            onChange={(e) => setTickerDraft(e.target.value)}
            placeholder="ticker filter ⏎"
            className="w-36 font-mono"
          />
        </form>
      </div>

      {error && <ErrorCard className="mb-4">{error}</ErrorCard>}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink-600">
              <Th>Occurred</Th>
              <Th>Ticker</Th>
              <Th>Type</Th>
              <Th>Sev</Th>
              <Th>Event</Th>
              <Th right title="when the daemon saw it — backfilled rows were never detected live">
                Detected
              </Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className={rowClass}>
                <Td mono className="text-slate-400">{(r.occurred_at || "").slice(0, 10) || "—"}</Td>
                <Td mono>
                  <Link
                    to={`/company/${r.ticker || r.cik}/timeline`}
                    className="text-slate-200 hover:text-accent"
                  >
                    {r.ticker || r.cik}
                  </Link>
                </Td>
                <Td>
                  <Badge tone="accent" mono>
                    {r.source_form || r.event_type}{r.subtype ? ` ${r.subtype}` : ""}
                  </Badge>
                </Td>
                <Td><SevBadge severity={r.severity} /></Td>
                <Td className="max-w-md">
                  {r.source_url ? (
                    <a href={r.source_url} target="_blank" rel="noreferrer"
                       className="block truncate text-slate-300 hover:text-accent">
                      {r.title || r.event_type}
                    </a>
                  ) : (
                    <span className="block truncate text-slate-400">{r.title || r.event_type}</span>
                  )}
                </Td>
                <Td right>
                  <DetectedStamp occurredAt={r.occurred_at} detectedAt={r.detected_at} />
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {loading && <Loading />}
      {!loading && rows.length === 0 && !error && (
        <Loading>no events match — the daemon fills this as it ingests</Loading>
      )}
      {!loading && !done && rows.length > 0 && (
        <div className="mt-3 text-center">
          <Button onClick={() => load(rows.length)}>load more</Button>
        </div>
      )}
    </div>
  );
}
