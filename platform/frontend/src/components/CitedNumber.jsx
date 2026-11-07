import React, { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Badge } from "../ui/index.jsx";

// Renders a single value with its provenance. Hovering, clicking or focusing it shows the
// citation (verbatim quote + link to the EDGAR source) or, for a derived figure, the formula.
// Nothing is shown uncited.
//
// The card renders through a portal to <body> with fixed positioning so it can never be
// clipped by the overflow-x-auto wrappers every data table sits in.

const CARD_W = 320; // matches w-80

function CiteCard({ id, anchorRef, cardRef, cv, onEnter, onLeave }) {
  const [pos, setPos] = useState(null);

  useLayoutEffect(() => {
    const a = anchorRef.current?.getBoundingClientRect();
    const h = cardRef.current?.offsetHeight || 0;
    if (!a) return;
    const left = Math.min(
      Math.max(a.left + a.width / 2 - CARD_W / 2, 8),
      window.innerWidth - CARD_W - 8,
    );
    let top = a.bottom + 4;
    if (top + h > window.innerHeight - 8) top = Math.max(a.top - h - 4, 8);
    setPos({ top, left });
  }, [anchorRef]);

  return createPortal(
    <div
      id={id}
      role="tooltip"
      ref={cardRef}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      // a portal still bubbles through the React tree: keep clicks in the card (the SEC.gov
      // link, selecting the quote) from toggling a clickable row around the number
      onClick={(e) => e.stopPropagation()}
      style={pos ? { top: pos.top, left: pos.left } : { top: -9999, left: -9999 }}
      className="fixed z-30 w-80 rounded-md border border-ink-600 bg-ink-900 p-3 text-left text-xs shadow-xl"
    >
      {cv.citation ? (
        <div>
          <div className="mb-1 flex items-center gap-2 text-[11px] uppercase tracking-wide text-slate-400">
            <Badge mono>{cv.citation.form_type || "filing"}</Badge>
            <span className="font-mono">{cv.citation.filing_date}</span>
            {cv.citation.exhibit && <span className="font-mono normal-case">· {cv.citation.exhibit}</span>}
          </div>
          {cv.citation.section && (
            <div className="text-[11px] text-slate-400">{cv.citation.section}</div>
          )}
          {cv.citation.quote && (
            <blockquote className="mt-1 border-l-2 border-accent/60 pl-2 text-[12px] italic text-slate-200">
              “{cv.citation.quote}”
            </blockquote>
          )}
          {cv.citation.source_url && (
            <a
              href={cv.citation.source_url}
              target="_blank"
              rel="noreferrer"
              className="mt-2 block truncate font-mono text-[11px] text-accent hover:underline"
            >
              ↗ Open source on SEC.gov
            </a>
          )}
        </div>
      ) : (
        <div>
          <Badge tone="watch" className="mb-1">Derived</Badge>
          {cv.formula && (
            <div className="font-mono text-[11px] text-slate-200">{cv.formula}</div>
          )}
          {cv.note && <div className="mt-1 text-[11px] text-slate-400">{cv.note}</div>}
        </div>
      )}
    </div>,
    document.body,
  );
}

export default function CitedNumber({ cv, className = "", placeholder = "—" }) {
  const [open, setOpen] = useState(false);
  const cardId = useId();
  const anchorRef = useRef(null);
  const cardRef = useRef(null);
  const closeTimer = useRef(null);

  // Close on any scroll (a fixed-position card must not drift from its anchor), on Escape,
  // and when a pointer or focus lands outside both the anchor and the card.
  useEffect(() => {
    if (!open) return;
    const inside = (t) => anchorRef.current?.contains(t) || cardRef.current?.contains(t);
    const onEvent = (e) => {
      if (e.type === "scroll" || (e.type === "keydown" ? e.key === "Escape" : !inside(e.target)))
        setOpen(false);
    };
    const types = ["scroll", "keydown", "pointerdown", "focusin"];
    types.forEach((t) => document.addEventListener(t, onEvent, true));
    return () => types.forEach((t) => document.removeEventListener(t, onEvent, true));
  }, [open]);

  useEffect(() => () => clearTimeout(closeTimer.current), []);

  if (!cv || (cv.value == null && !cv.display)) {
    return <span className="text-slate-400">{placeholder}</span>;
  }

  const label = cv.display ?? (cv.value != null ? String(cv.value) : placeholder);
  const isDerived = cv.derived;

  // The card is a portal, not a DOM child — hovering into it must cancel the
  // pending close so the SEC.gov link stays reachable.
  const openNow = () => {
    clearTimeout(closeTimer.current);
    setOpen(true);
  };
  const closeSoon = () => {
    clearTimeout(closeTimer.current);
    closeTimer.current = setTimeout(() => setOpen(false), 120);
  };

  return (
    <span className="relative inline-block" onMouseEnter={openNow} onMouseLeave={closeSoon}>
      <button
        type="button"
        ref={anchorRef}
        // a click reads the source; it must not also act on a clickable row around it
        onClick={(e) => { e.stopPropagation(); openNow(); }}
        onFocus={openNow}
        aria-label={`Source for ${label}`}
        aria-describedby={open ? cardId : undefined}
        className={`cite-link font-mono tabular-nums ${className} ${
          isDerived ? "decoration-amber-400/50" : ""
        }`}
      >
        {label}
        {isDerived && <sup className="ml-0.5 text-[11px] text-amber-400/80">ƒ</sup>}
      </button>
      {open && (
        <CiteCard id={cardId} anchorRef={anchorRef} cardRef={cardRef} cv={cv} onEnter={openNow} onLeave={closeSoon} />
      )}
    </span>
  );
}
