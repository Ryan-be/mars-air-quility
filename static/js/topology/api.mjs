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


/**
 * POST /api/effectors/<id>/state — set an effector's mode.
 *
 * Body is `{state: "auto"|"on"|"off"}`. `on` + `off` also flip
 * `auto_mode=0` server-side (forced override); `auto` re-enables
 * rule-driven control. Returns the parsed response body.
 *
 * Surfaced errors:
 *   * 400 — invalid state value (validated server-side).
 *   * 403 — viewer role (controller + admin only).
 *   * 404 — effector id not found.
 *
 * @param {number} effectorId Numeric smart_plugs.id (e.g. 1).
 * @param {"auto"|"on"|"off"} mode
 * @param {Function} [fetchFn=fetch] Stubbed in tests.
 */
export async function setEffectorState(effectorId, mode, fetchFn = fetch) {
  const r = await fetchFn(`/api/effectors/${effectorId}/state`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state: mode }),
  });
  if (!r.ok) {
    throw new Error(`setEffectorState HTTP ${r.status}`);
  }
  return r.json();
}


/**
 * POST /api/effectors/layout/reset — admin-only nuke of all persisted
 * node positions (Phase 11 Task 11.2). The server truncates the
 * `node_layout` table and NULLs every `smart_plugs.layout_json`; the
 * client follows up with `autoLayout(nodes)` to repopulate defaults.
 *
 * @param {Function} [fetchFn=fetch]
 */
export async function resetLayout(fetchFn = fetch) {
  const r = await fetchFn("/api/effectors/layout/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!r.ok) {
    throw new Error(`resetLayout HTTP ${r.status}`);
  }
  if (r.status === 204) return null;
  return r.json();
}


/**
 * PATCH /api/effectors/layout — bulk-save the supplied node positions.
 *
 * The boot orchestrator debounces drag-ends (Phase 11 Task 11.1) and
 * calls this exactly once per debounce window with the accumulated
 * positions. Body shape matches the v2 endpoint:
 *
 *   { positions: [{ kind: 'hub'|'grow'|'effector', id, x, y }, ...] }
 *
 * Returns the parsed response body (`{saved: N}` on success).
 *
 * @param {Array<{kind: string, id: string|number, x: number, y: number}>} positions
 * @param {Function} [fetchFn=fetch]
 */
export async function patchLayout(positions, fetchFn = fetch) {
  const r = await fetchFn("/api/effectors/layout", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ positions }),
  });
  if (!r.ok) {
    throw new Error(`patchLayout HTTP ${r.status}`);
  }
  // 204 No Content has no body; guard the json() call in that case.
  if (r.status === 204) return null;
  return r.json();
}


