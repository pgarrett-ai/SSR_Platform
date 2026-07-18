// Runnable check: node platform/frontend/src/lib/postReorgMath.selfcheck.mjs
import assert from "node:assert";
import { impliedEquityCap, effectiveFloat, capPenalty, overhangPct } from "./postReorgMath.js";

assert.strictEqual(impliedEquityCap(700, 100), 600);
assert.strictEqual(impliedEquityCap(80, 120), 0);        // floored, never negative
assert.strictEqual(effectiveFloat(600, 50), 300);
assert.strictEqual(capPenalty(400), "small-cap");
assert.strictEqual(capPenalty(600), "institutional");
assert.strictEqual(capPenalty(40), "micro-cap");
assert.strictEqual(overhangPct(30, 10), 40);

console.log("postReorgMath selfcheck OK");
