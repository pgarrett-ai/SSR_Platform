import React, { useState } from "react";
import {
  Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import CitedNumber from "./CitedNumber.jsx";
import { Badge, Section, LINE_COLORS, chartTooltipStyle, fmt } from "../ui/index.jsx";
import { capPenalty, effectiveFloat, impliedEquityCap, overhangPct } from "../lib/postReorgMath.js";

// Post-reorg equity technicals (Moyer ch. 13, F5): a mostly-manual card. Reorg EV /
// post-reorg debt come from the shared plan assumptions (set in the Plan Recovery card
// above); this card only READS them and layers analyst-supplied float, forced-seller
// mix and lockups on top — the market data layer only has the pre-distress share count.

const numCls =
  "w-20 rounded-md border border-ink-600 bg-ink-800 px-2 py-1 font-mono text-xs text-slate-100 outline-none focus:border-accent";
const noteCls =
  "w-56 rounded-md border border-ink-600 bg-ink-800 px-2 py-1 text-xs text-slate-100 outline-none focus:border-accent";

function TermField({ label, title, children }) {
  return (
    <label className="flex flex-col gap-1" title={title}>
      <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  );
}

// J.P. Morgan study of 117 post-reorg equities (1986–2003): historical benchmark, not a
// prediction. Month-1 +17.1%, first-year +85.1% average excess return.
const BENCHMARK = [
  { period: "Month 1", ret: 17.1 },
  { period: "First year", ret: 85.1 },
];

const TIER_TONE = { institutional: "ok", "small-cap": "watch", "micro-cap": "high" };

export default function PostReorgTechnicals({ reorgEv, reorgDebt }) {
  const [controlPct, setControlPct] = useState(0);
  const [bankPct, setBankPct] = useState(0);
  const [cdoPct, setCdoPct] = useState(0);
  const [lockup, setLockup] = useState("");

  const evNum = reorgEv === "" || reorgEv == null ? null : Number(reorgEv);
  const debtNum = reorgDebt === "" || reorgDebt == null ? null : Number(reorgDebt);
  const havePlan = evNum != null && debtNum != null;

  const cap = havePlan ? impliedEquityCap(evNum, debtNum) : null;
  const control = Number(controlPct) || 0;
  const float = cap != null ? effectiveFloat(cap, control) : null;
  const tier = cap != null ? capPenalty(cap) : null;
  const overhang = overhangPct(Number(bankPct) || 0, Number(cdoPct) || 0);

  return (
    <Section
      title="Post-reorg equity technicals"
      subtitle="float, cap-tier and forced-seller overhang on the reorg equity (Moyer ch. 13)"
    >
      <div className="mb-4 flex flex-wrap items-end gap-x-5 gap-y-3">
        <TermField label="Control %" title="stake held by a control block or plan sponsor — carved out of the investable float">
          <input type="number" step={1} value={controlPct} onChange={(e) => setControlPct(e.target.value)} className={numCls} />
        </TermField>
        <TermField label="Forced bank %" title="pre-petition bank debt swapped to equity — banks must typically dispose within ~2 years (forced sellers)">
          <input type="number" step={1} value={bankPct} onChange={(e) => setBankPct(e.target.value)} className={numCls} />
        </TermField>
        <TermField label="Forced CDO %" title="CLO/CDO holders — equity breaches their eligibility limits, so they are capped and forced to sell">
          <input type="number" step={1} value={cdoPct} onChange={(e) => setCdoPct(e.target.value)} className={numCls} />
        </TermField>
        <TermField label="Voluntary lockup" title="analyst note on any voluntary lockup / standstill on new equity (optional)">
          <input type="text" value={lockup} onChange={(e) => setLockup(e.target.value)} className={noteCls}
            placeholder="e.g. 180d sponsor lockup" />
        </TermField>
      </div>

      {!havePlan ? (
        <div className="text-xs text-slate-500">
          set plan EV / post-reorg debt in the Plan Recovery card above
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-x-8 gap-y-3 text-xs text-slate-400">
          <span>
            implied equity cap{" "}
            <CitedNumber
              className="text-slate-100"
              cv={{
                value: cap,
                display: `${fmt(cap, 0)} $mm`,
                derived: true,
                formula: `plan EV ${fmt(evNum, 0)} − post-reorg debt ${fmt(debtNum, 0)}`,
              }}
            />
          </span>
          <span>
            effective float{" "}
            <CitedNumber
              className="text-slate-100"
              cv={{
                value: float,
                display: `${fmt(float, 0)} $mm`,
                derived: true,
                formula: `cap ${fmt(cap, 0)} × (1 − control ${control}%)`,
                note: "float carved for the control block — the tradable equity",
              }}
            />
          </span>
          <span className="flex items-center gap-1.5">
            cap tier <Badge tone={TIER_TONE[tier]}>{tier}</Badge>
          </span>
          <span title="banks must dispose ≤2yr; CDOs are capped on equity — this % overhangs the aftermarket as forced supply">
            forced-seller overhang{" "}
            <CitedNumber
              className="text-slate-100"
              cv={{
                value: overhang,
                display: `${fmt(overhang, 0)}%`,
                derived: true,
                formula: `bank ${Number(bankPct) || 0}% + CDO ${Number(cdoPct) || 0}%`,
                note: "banks dispose within ~2yr; CDOs capped on equity holdings",
              }}
            />
          </span>
        </div>
      )}

      <div className="mt-5">
        <div className="mb-1 text-xs text-slate-500">
          back-end equity — average excess return (J.P. Morgan, 117 post-reorg equities 1986–2003; historical benchmark, not a prediction)
        </div>
        <ResponsiveContainer width="100%" height={120}>
          <BarChart data={BENCHMARK} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
            <XAxis dataKey="period" tick={{ fill: "#94a3b8", fontSize: 10 }} />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 10 }} unit="%" />
            <Tooltip contentStyle={chartTooltipStyle} formatter={(v) => [`+${v}%`, "avg excess return"]} />
            <Bar dataKey="ret" fill={LINE_COLORS[1]} radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-3 text-[11px] text-slate-500">
        §382 caps trading by 5%+ holders — plans and post-emergence charters routinely bar any
        holder from crossing 5% to protect the NOLs (see the NOL / §382 tax-asset card), which
        thins natural demand and leaves the new equity demand-limited.
      </div>
      <div className="mt-2 text-[11px] text-slate-600">
        float, forced-seller mix and lockups are analyst inputs — the market layer only has the
        pre-distress share count.
      </div>
    </Section>
  );
}
