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

// A token saved in an earlier session has outlived its cookie; re-mirror it for EventSource.
if (getToken()) setToken(getToken());

// EventSource hides the HTTP status of a refused stream, so one plain request reads it,
// sending the cookie only, as EventSource does. A 401 or 422 is answered before the route
// runs; a 200 means the server is back, and the request is cancelled at its headers.
async function refusedStreamMessage(url) {
  const ctl = new AbortController();
  try {
    const r = await fetch(url, { signal: ctl.signal });
    if (r.status === 401) return "API token required: enter it in the header, then retry";
    // a dev proxy answers a dead backend with an empty 5xx
    if (r.status >= 500 && !(await r.clone().text())) return "connection to server lost";
    if (!r.ok) await jsonOrThrow(r);
  } catch (e) {
    if (e.name !== "AbortError" && !(e instanceof TypeError)) return e.message;
  } finally {
    ctl.abort();
  }
  return "connection to server lost";
}

// One SSE pipeline: progress events, then a final `doneEvent` with the payload (or an
// `error` event). The connection closes as soon as the run settles.
function streamSse(url, doneEvent, onProgress) {
  return new Promise((resolve, reject) => {
    const es = new EventSource(url);
    let settled = false;
    let opened = false;
    es.addEventListener("open", () => { opened = true; });
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
        reject(new Error(body?.detail || body?.error || "the analysis could not be completed"));
      } else if (!settled) {
        settled = true;
        es.close();
        if (opened) reject(new Error("connection to server lost"));
        else refusedStreamMessage(url).then((m) => reject(new Error(m)));
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
export async function fetchLadder(ticker, years = 3) {
  return jsonOrThrow(await apiFetch(`${co(ticker)}/capital/ladder?years=${years}`));
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

export async function fetchCrisisScreen(ticker, years = 3) {
  return jsonOrThrow(await apiFetch(`${co(ticker)}/recovery/crisis?years=${years}`));
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
