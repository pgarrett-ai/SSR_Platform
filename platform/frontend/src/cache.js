// Module-level per-session data cache so tab switches never refetch (a cached capstack
// overview is ~instant, but a non-cached live run is minutes — never re-trigger on nav).
// ponytail: plain object, no TTL — a page reload clears it, which is the right scope here.
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
