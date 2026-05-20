/**
 * Fetch wrappers for the /api/admin/backup/* endpoints.
 *
 * Every function takes an optional `fetchFn` for tests — defaults to
 * the browser's global `fetch`. The returned promises resolve to the
 * parsed JSON body for the happy path, or `{ok: false, error}` for
 * the failures we surface inline.
 *
 * No state, no DOM. Pure HTTP.
 */


const BASE = "/api/admin/backup";


/**
 * GET /api/admin/backup/config — returns the masked config dict
 * (password_set / secret_key_set booleans, never cleartext).
 */
export async function getConfig(fetchFn = fetch) {
  const r = await fetchFn(`${BASE}/config`);
  if (!r.ok) throw new Error(`getConfig HTTP ${r.status}`);
  return r.json();
}


/**
 * PUT /api/admin/backup/config — saves a partial config dict and
 * returns the post-save masked config. Empty-string passwords mean
 * "preserve existing" per the server contract.
 */
export async function putConfig(payload, fetchFn = fetch) {
  const r = await fetchFn(`${BASE}/config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(`putConfig HTTP ${r.status}`);
  return r.json();
}


/**
 * GET /api/admin/backup/status — returns the snapshot of pipeline
 * state at this instant. The SSE stream is the live-update mechanism;
 * this endpoint serves the initial paint.
 */
export async function getStatus(fetchFn = fetch) {
  const r = await fetchFn(`${BASE}/status`);
  if (!r.ok) throw new Error(`getStatus HTTP ${r.status}`);
  return r.json();
}


/**
 * POST /api/admin/backup/test?pipeline={db|files} — try to connect
 * with the currently-stored credentials. The server wraps connection
 * exceptions as `{ok: false, error}` so callers should NOT treat
 * `ok: false` as a 500.
 */
export async function testConnection(pipeline, fetchFn = fetch) {
  const r = await fetchFn(`${BASE}/test?pipeline=${pipeline}`, {
    method: "POST",
  });
  // Even on transport failure we want to surface a structured error
  // so the caller's UI doesn't crash.
  try {
    return await r.json();
  } catch (_e) {
    return { ok: false, error: `HTTP ${r.status}` };
  }
}


/**
 * POST /api/admin/backup/init?pipeline={db|files} — one-time setup
 * (create S3 buckets for files; stub for db pending Phase 9 schema).
 */
export async function initPipeline(pipeline, fetchFn = fetch) {
  const r = await fetchFn(`${BASE}/init?pipeline=${pipeline}`, {
    method: "POST",
  });
  try {
    return await r.json();
  } catch (_e) {
    return { ok: false, error: `HTTP ${r.status}` };
  }
}


/**
 * POST /api/admin/backup/maintenance — confirm-gated destructive
 * actions. Always sends `{action, confirm: true}` — the *UI* is
 * responsible for the confirmation flow (magic-word dialog).
 *
 * Supported actions: pause / resume / force_rebootstrap / clear_outbox.
 */
export async function maintenance(action, fetchFn = fetch) {
  const r = await fetchFn(`${BASE}/maintenance`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, confirm: true }),
  });
  try {
    return await r.json();
  } catch (_e) {
    return { ok: false, error: `HTTP ${r.status}` };
  }
}
