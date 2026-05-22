/**
 * Fetch wrappers for the topology endpoints (Phase 4 Task 4.3+).
 *
 * Every function accepts an optional `fetchFn` so tests can inject a
 * stub without monkey-patching the global. The promise resolves to
 * the parsed JSON body for the happy path; non-2xx responses throw
 * with a useful message so the boot module can paint a single
 * statusbar error rather than letting the promise chain dead-end.
 *
 * Pure HTTP — no DOM, no global state. Mirrors static/js/backup/api.mjs.
 */


/**
 * GET /api/topology — returns the full snapshot used by the first
 * paint of the /controls page:
 *   {
 *     hub:       { id, kind: "hub",      label, sensors, ... },
 *     grows:     [{ id, kind: "grow",    label, sensors, ... }, ...],
 *     effectors: [{ id, kind: "effector", parent, label, mode, ... }, ...],
 *     layout:    { "<node-id>": { x, y }, ... },
 *   }
 *
 * Subsequent live updates land via the SSE bus (Phase 10 wiring) — this
 * endpoint is just the cold-start fetch.
 */
export async function fetchTopology(fetchFn = fetch) {
  const r = await fetchFn("/api/topology");
  if (!r.ok) {
    throw new Error(`fetchTopology HTTP ${r.status}`);
  }
  return r.json();
}
