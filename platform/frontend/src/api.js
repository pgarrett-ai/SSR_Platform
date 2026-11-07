// Thin API client for the FastAPI backend.

// ---- auth: shared bearer token -------------------------------------------------
// Stored in localStorage for fetch headers, mirrored to a same-origin cookie because
// EventSource (the two SSE streams) cannot send headers.

const TOKEN_KEY = "platform_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
  document.cookie = t
    ? `platform_token=${encodeURIComponent(t)}; path=/; SameSite=Lax`
    : "platform_token=; path=/; Max-Age=0";
}

function apiFetch(url, opts = {}) {
  const t = getToken();
  const headers = { ...(opts.headers || {}), ...(t ? { Authorization: `Bearer ${t}` } : {}) };
  return fetch(url, { ...opts, headers });
}

// Every JSON call ends here. The body is read once as text, so a proxy's HTML error page
// still yields a status message. FastAPI puts the human sentence in `detail` (a string,
// or for a 422 a list of {loc, msg}); `error` is the machine slug.
async function jsonOrThrow(r) {
  const text = await r.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    if (r.ok) throw new Error("the server sent an unreadable response");
  }
  if (!r.ok) {
    const d = body?.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d[0]?.msg : body?.error;
    throw new Error(msg || `request failed (${r.status})`);
  }
  return body;
}

const postJson = (url, body) =>
  apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(jsonOrThrow);

const co = (ticker) => `/api/company/${encodeURIComponent(ticker)}`;

export async function fetchHealth() {
  return jsonOrThrow(await apiFetch("/api/health"));
}

// One SSE pipeline: progress events, then a final `doneEvent` with the payload (or an
// `error` event). The connection closes as soon as the run settles.
function streamSse(url, doneEvent, onProgress) {
  return new Promise((resolve, reject) => {
    const es = new EventSource(url);
    let settled = false;
    es.addEventListener("progress", (e) => {
      try {
        onProgress?.(JSON.parse(e.data));
      } catch (_) {}
    });
    es.addEventListener(doneEvent, (e) => {
      settled = true;
      es.close();
      resolve(JSON.parse(e.data));
    });
    es.addEventListener("error", (e) => {
      if (e?.data) {
        settled = true;
        es.close();
        let body = null;
        try { body = JSON.parse(e.data); } catch (_) {}
        reject(new Error(body?.detail || body?.error || "pipeline error"));
      } else if (!settled) {
        es.close();
        reject(new Error("connection to server lost"));
      }
    });
  });
}

export function streamOverview(ticker, years, live, onProgress) {
  return streamSse(
    `/api/overview/stream?ticker=${encodeURIComponent(ticker)}&years=${years}&live=${live}`,
    "overview", onProgress);
}

export function streamHazard(ticker, years, onProgress) {
  return streamSse(
    `/api/hazard/stream?ticker=${encodeURIComponent(ticker)}&years=${years}`,
    "hazard", onProgress);
}

// ---- Capital structure -----------------------------------------------------------

export async function fetchHolders(ticker) {
  return jsonOrThrow(await apiFetch(`${co(ticker)}/holders`));
}

// Quotes matched per instrument (Recovery reads quote_by_instrument).
export async function fetchLadder(ticker, years = 3, recast = false) {
  return jsonOrThrow(
    await apiFetch(`${co(ticker)}/capital/ladder?years=${years}&recast_mezz=${recast ? 1 : 0}`)
  );
}

export async function fetchRefiWall(ticker, years = 3) {
  return jsonOrThrow(await apiFetch(`${co(ticker)}/capital/refi?years=${years}`));
}

export async function fetchScreen() {
  return jsonOrThrow(await apiFetch("/api/screen"));
}

// ---- Recovery (fulcrum) ------------------------------------------------------

export async function fetchRecoveryStructure(ticker, years = 3) {
  return jsonOrThrow(await apiFetch(`${co(ticker)}/recovery/structure?years=${years}`));
}

export function simulateRecovery(ticker, structure, sim, years = 3, extra = {}) {
  return postJson(`${co(ticker)}/recovery/simulate?years=${years}`, { structure, sim, ...extra });
}

export function exploreRecovery(ticker, body, years = 3) {
  return postJson(`${co(ticker)}/recovery/explore?years=${years}`, body);
}

export function exchangeRecovery(ticker, body, years = 3) {
  return postJson(`${co(ticker)}/recovery/exchange?years=${years}`, body);
}

export function liquidateRecovery(ticker, body, years = 3) {
  return postJson(`${co(ticker)}/recovery/liquidation?years=${years}`, body);
}

export function planRecovery(ticker, body, years = 3) {
  return postJson(`${co(ticker)}/recovery/plan?years=${years}`, body);
}

export function addDocketEvent(ticker, body) {
  return postJson(`${co(ticker)}/recovery/docket`, body);
}

// Undo for manual docket rows only — the server refuses detector-sourced ids.
export async function deleteEvent(eventId) {
  return jsonOrThrow(await apiFetch(`/api/events/${eventId}`, { method: "DELETE" }));
}

export async function fetchCh11Case(ticker, years = 3) {
  return jsonOrThrow(await apiFetch(`${co(ticker)}/recovery/case?years=${years}`));
}

export async function fetchCrisisScreen(ticker, years = 3) {
  return jsonOrThrow(await apiFetch(`${co(ticker)}/recovery/crisis?years=${years}`));
}

export function computeTax382(ticker, body, years = 3) {
  return postJson(`${co(ticker)}/recovery/tax382?years=${years}`, body);
}

export async function listScenarios(ticker) {
  return jsonOrThrow(await apiFetch(`${co(ticker)}/scenarios`));
}

export function saveScenario(ticker, payload) {
  return postJson(`${co(ticker)}/scenarios`, payload);
}

export async function deleteScenario(id) {
  return jsonOrThrow(await apiFetch(`/api/scenarios/${id}`, { method: "DELETE" }));
}

// ---- Events ----------------------------------------------------------------------

export async function fetchEvents({ ticker, cik, event_type = [], min_severity,
                                    since, until, limit = 100, offset = 0 } = {}) {
  const qs = new URLSearchParams();
  if (ticker) qs.set("ticker", ticker);
  if (cik) qs.set("cik", cik);
  event_type.forEach((t) => qs.append("event_type", t));
  if (min_severity) qs.set("min_severity", min_severity);
  if (since) qs.set("since", since);
  if (until) qs.set("until", until);
  qs.set("limit", limit);
  qs.set("offset", offset);
  return jsonOrThrow(await apiFetch(`/api/events?${qs}`));
}
