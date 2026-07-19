import React from "react";
import { Badge } from "../ui/index.jsx";

// severity 1–5 -> Badge tone (Badge is the app's chip primitive — ui/index.jsx;
// the plan's "FlagChip" never existed, FlagCard composes Badge too)
const SEV_TONE = { 1: "neutral", 2: "info", 3: "watch", 4: "watch", 5: "high" };

export function SevBadge({ severity }) {
  if (severity == null) return null;
  return <Badge tone={SEV_TONE[severity] || "neutral"} mono>S{severity}</Badge>;
}

// detected-vs-occurred honesty: live rows show detection date (+lag), backfilled
// rows (detected_at null) say so instead of pretending they were seen live.
export function DetectedStamp({ occurredAt, detectedAt }) {
  if (!detectedAt) return <Badge tone="neutral">backfilled</Badge>;
  const lagDays = occurredAt
    ? Math.round((new Date(detectedAt) - new Date(occurredAt)) / 86400000)
    : null;
  return (
    <span className="font-mono text-[10px] text-slate-500" title={`detected ${detectedAt}`}>
      det {String(detectedAt).slice(0, 10)}{lagDays > 0 ? ` (+${lagDays}d)` : ""}
    </span>
  );
}
