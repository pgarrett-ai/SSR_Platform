// Module-level per-session data cache so tab switches never refetch (a cached capstack
// overview is ~instant, but a non-cached live run is minutes — never re-trigger on nav).
// ponytail: plain object, no TTL — a page reload clears it, which is the right scope here.
import { useEffect, useState } from "react";

const store = {};

export function getCached(key) {
  return store[key];
}

export function setCached(key, value) {
  store[key] = value;
  return value;
}

export function clearCached(key) {
  delete store[key];
}

// Cache-first async loader shared by the data pages (CapitalPage keeps its own
// SSE streaming flow).
export function useAsync(key, loader, deps) {
  const [state, setState] = useState({ data: getCached(key) || null, error: null, loading: !!key && !getCached(key) });
  useEffect(() => {
    if (!key) {
      setState({ data: null, error: null, loading: false });
      return;
    }
    const cached = getCached(key);
    if (cached) {
      setState({ data: cached, error: null, loading: false });
      return;
    }
    let alive = true;
    setState({ data: null, error: null, loading: true });
    loader()
      .then((d) => alive && setState({ data: setCached(key, d), error: null, loading: false }))
      .catch((e) => alive && setState({ data: null, error: e.message, loading: false }));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
