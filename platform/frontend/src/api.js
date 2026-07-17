// Thin API client for the FastAPI backend.

export async function fetchHealth() {
  const r = await fetch("/api/health");
  if (!r.ok) throw new Error("health check failed");
  return r.json();
}

export async function setLlmEnabled(enabled) {
  const r = await fetch("/api/settings/llm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!r.ok) throw new Error("toggle failed");
  return r.json();
}

export async function fetchOverview(ticker, years, live = false) {
  const url = `/api/overview?ticker=${encodeURIComponent(ticker)}&years=${years}&live=${live}`;
  const r = await fetch(url);
  const body = await r.json();
  if (!r.ok) {
    throw new Error(body?.detail || body?.error || `request failed (${r.status})`);
  }
  return body;
}

// Stream the progress log over SSE, then resolve with the final overview.
// Returns { promise, cancel } so the caller can close the connection on unmount or a new run —
// otherwise an in-flight EventSource leaks and keeps the page's network perpetually active.
export function streamOverview(ticker, years, live, onProgress) {
  const url = `/api/overview/stream?ticker=${encodeURIComponent(
    ticker
  )}&years=${years}&live=${live}`;
  const es = new EventSource(url);
  let settled = false;

  const promise = new Promise((resolve, reject) => {
    es.addEventListener("progress", (e) => {
      try {
        onProgress?.(JSON.parse(e.data));
      } catch (_) {}
    });
    es.addEventListener("overview", (e) => {
      settled = true;
      es.close();
      resolve(JSON.parse(e.data));
    });
    es.addEventListener("error", (e) => {
      if (e?.data) {
        settled = true;
        es.close();
        try {
          reject(new Error(JSON.parse(e.data)?.detail || "pipeline error"));
        } catch (_) {
          reject(new Error("pipeline error"));
        }
      } else if (!settled) {
        es.close();
        reject(new Error("connection to server lost"));
      }
    });
  });

  return {
    promise,
    cancel: () => {
      settled = true;
      es.close();
    },
  };
}

export function overviewJsonUrl(ticker, years, live = false) {
  return `/api/overview?ticker=${encodeURIComponent(ticker)}&years=${years}&live=${live}`;
}

// ---- Default risk (hazard) ---------------------------------------------------

export async function fetchHazard(ticker, years = 10) {
  const r = await fetch(`/api/company/${encodeURIComponent(ticker)}?years=${years}&sections=hazard`);
  const body = await r.json();
  if (!r.ok) throw new Error(body?.error || `request failed (${r.status})`);
  const hz = body?.sections?.hazard;
  if (hz?.error) throw new Error(hz.error);
  return hz;
}

// Same payload as fetchHazard, streamed: progress events over SSE, then the final data.
// Mirrors streamOverview's { promise, cancel } contract.
export function streamHazard(ticker, years, onProgress) {
  const url = `/api/hazard/stream?ticker=${encodeURIComponent(ticker)}&years=${years}`;
  const es = new EventSource(url);
  let settled = false;

  const promise = new Promise((resolve, reject) => {
    es.addEventListener("progress", (e) => {
      try {
        onProgress?.(JSON.parse(e.data));
      } catch (_) {}
    });
    es.addEventListener("hazard", (e) => {
      settled = true;
      es.close();
      resolve(JSON.parse(e.data));
    });
    es.addEventListener("error", (e) => {
      if (e?.data) {
        settled = true;
        es.close();
        try {
          reject(new Error(JSON.parse(e.data)?.detail || "hazard pipeline error"));
        } catch (_) {
          reject(new Error("hazard pipeline error"));
        }
      } else if (!settled) {
        es.close();
        reject(new Error("connection to server lost"));
      }
    });
  });

  return {
    promise,
    cancel: () => {
      settled = true;
      es.close();
    },
  };
}

// ---- Key rates -----------------------------------------------------------------

export async function fetchRates() {
  return jsonOrThrow(await fetch("/api/rates"));
}

export async function fetchHolders(ticker) {
  return jsonOrThrow(await fetch(`/api/company/${encodeURIComponent(ticker)}/holders`));
}

// ---- MD&A reader ---------------------------------------------------------------

export async function fetchMdnaPeriods(ticker) {
  return jsonOrThrow(await fetch(`/api/company/${encodeURIComponent(ticker)}/mdna`));
}

export async function fetchMdnaText(ticker, accessionNo) {
  return jsonOrThrow(
    await fetch(`/api/company/${encodeURIComponent(ticker)}/mdna/${encodeURIComponent(accessionNo)}`)
  );
}

// ---- Bonds + creation ladder (Moyer market layer) ------------------------------

export async function fetchBonds(ticker) {
  return jsonOrThrow(await fetch(`/api/company/${encodeURIComponent(ticker)}/bonds`));
}

export async function fetchLadder(ticker, years = 3, recast = false) {
  return jsonOrThrow(
    await fetch(
      `/api/company/${encodeURIComponent(ticker)}/capital/ladder?years=${years}&recast_mezz=${recast ? 1 : 0}`
    )
  );
}

export async function fetchCapacity(ticker, years = 3) {
  return jsonOrThrow(
    await fetch(`/api/company/${encodeURIComponent(ticker)}/capacity?years=${years}`)
  );
}

export async function fetchRefiWall(ticker, years = 3) {
  return jsonOrThrow(
    await fetch(`/api/company/${encodeURIComponent(ticker)}/capital/refi?years=${years}`)
  );
}

export async function fetchTelegraph(ticker, years = 3) {
  return jsonOrThrow(
    await fetch(`/api/company/${encodeURIComponent(ticker)}/telegraph?years=${years}`)
  );
}

// ---- Screening + full-text search --------------------------------------------

export async function fetchScreen() {
  return jsonOrThrow(await fetch("/api/screen"));
}

export async function searchText(q, ticker) {
  const t = ticker ? `&ticker=${encodeURIComponent(ticker)}` : "";
  return jsonOrThrow(await fetch(`/api/search?q=${encodeURIComponent(q)}${t}`));
}

// ---- Recovery (fulcrum) ------------------------------------------------------

async function jsonOrThrow(r) {
  const body = await r.json();
  if (!r.ok) throw new Error(body?.error || body?.detail || `request failed (${r.status})`);
  return body;
}

export async function fetchRecoveryStructure(ticker, years = 3) {
  return jsonOrThrow(await fetch(`/api/company/${encodeURIComponent(ticker)}/recovery/structure?years=${years}`));
}

export async function simulateRecovery(ticker, structure, sim, years = 3, extra = {}) {
  return jsonOrThrow(
    await fetch(`/api/company/${encodeURIComponent(ticker)}/recovery/simulate?years=${years}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ structure, sim, ...extra }),
    })
  );
}

export async function exploreRecovery(ticker, body, years = 3) {
  return jsonOrThrow(
    await fetch(`/api/company/${encodeURIComponent(ticker)}/recovery/explore?years=${years}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
  );
}

export async function liquidateRecovery(ticker, body, years = 3) {
  return jsonOrThrow(
    await fetch(`/api/company/${encodeURIComponent(ticker)}/recovery/liquidation?years=${years}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
  );
}

export async function listScenarios(ticker) {
  return jsonOrThrow(await fetch(`/api/company/${encodeURIComponent(ticker)}/scenarios`));
}

export async function saveScenario(ticker, payload) {
  return jsonOrThrow(
    await fetch(`/api/company/${encodeURIComponent(ticker)}/scenarios`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
  );
}

export async function deleteScenario(id) {
  return jsonOrThrow(await fetch(`/api/scenarios/${id}`, { method: "DELETE" }));
}
