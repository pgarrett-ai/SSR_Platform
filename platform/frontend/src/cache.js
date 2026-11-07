// Module-level per-session data cache so tab switches never refetch (a cached capstack
// overview is ~instant, but a non-cached live run is minutes — never re-trigger on nav).
// ponytail: plain objects, no TTL — a page reload clears them, which is the right scope here.
import { useEffect, useState } from "react";

const store = {};

// Cache-first async loader for the small per-section fetches.
export function useAsync(key, loader, deps) {
  const [state, setState] = useState({ data: store[key] || null, error: null, loading: !!key && !store[key] });
  useEffect(() => {
    if (!key) {
      setState({ data: null, error: null, loading: false });
      return;
    }
    if (store[key]) {
      setState({ data: store[key], error: null, loading: false });
      return;
    }
    let alive = true;
    setState({ data: null, error: null, loading: true });
    loader()
      .then((d) => alive && setState({ data: (store[key] = d), error: null, loading: false }))
      .catch((e) => alive && setState({ data: null, error: e.message, loading: false }));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

// The two SSE pipelines (overview, hazard): one run per key for the session. The run —
// promise, progress events, outcome — is cached rather than just the result, so a second
// caller or a remount mid-stream joins the in-flight run instead of opening another
// stream, and a run outlives the component that started it. A failed run retries on the
// next mount or on retry(). `start(onProgress)` returns the stream's promise.
const runs = {};

export function useStreamed(key, start) {
  const [, rerender] = useState(0);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!key) return;
    let run = runs[key];
    if (!run || run.error) {
      run = runs[key] = { events: [], subs: new Set() };
      const ping = () => run.subs.forEach((f) => f());
      run.promise = start((e) => { run.events = [...run.events, e]; ping(); })
        .then((d) => { run.data = d; }, (e) => { run.error = e.message || String(e); })
        .finally(ping);
    }
    const sub = () => rerender((n) => n + 1);
    run.subs.add(sub);
    sub();   // a run that settled between render and this effect re-renders now
    return () => run.subs.delete(sub);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, attempt]);
  const run = key ? runs[key] : null;
  return {
    data: run?.data ?? null,
    error: run?.error ?? null,
    events: run?.events ?? [],
    loading: !!key && !run?.data && !run?.error,
    retry: () => {
      if (runs[key]?.error) delete runs[key];
      setAttempt((n) => n + 1);
    },
  };
}
