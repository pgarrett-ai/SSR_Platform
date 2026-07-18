// Post-reorg equity technicals math (Moyer ch. 13). Pure functions, no React —
// so the selfcheck (.mjs sibling) can exercise them under node.

// Implied post-reorg equity market cap ($mm) = plan EV − post-reorg debt, floored at 0.
export const impliedEquityCap = (planEv, postReorgDebt) =>
  Math.max((planEv || 0) - (postReorgDebt || 0), 0);

// Investable float ($mm) after control block is carved out.
export const effectiveFloat = (impliedCap, controlPct) =>
  impliedCap * (1 - (controlPct || 0) / 100);

// Institutional-minimum tier (Moyer ch.13: ~$500MM small-cap / ~$50MM micro-cap floors).
export const capPenalty = (impliedCap) =>
  impliedCap < 50 ? "micro-cap" : impliedCap < 500 ? "small-cap" : "institutional";

// Forced-seller overhang % = banks that must dispose within ~2yr + CDOs capped on equity.
export const overhangPct = (bankPct, cdoPct) => (bankPct || 0) + (cdoPct || 0);
