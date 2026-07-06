// Thin API client for the FastAPI backend.

export async function fetchHealth() {
  const r = await fetch("/api/health");
  if (!r.ok) throw new Error("health check failed");
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

// ---- Recovery (fulcrum) ------------------------------------------------------

async function jsonOrThrow(r) {
  const body = await r.json();
  if (!r.ok) throw new Error(body?.error || body?.detail || `request failed (${r.status})`);
  return body;
}

export async function fetchRecoveryStructure(ticker, years = 3) {
  return jsonOrThrow(await fetch(`/api/company/${encodeURIComponent(ticker)}/recovery/structure?years=${years}`));
}

export async function simulateRecovery(ticker, structure, sim, years = 3) {
  return jsonOrThrow(
    await fetch(`/api/company/${encodeURIComponent(ticker)}/recovery/simulate?years=${years}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ structure, sim }),
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
