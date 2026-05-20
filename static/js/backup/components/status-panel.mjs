/**
 * Status panel — renders the master-enabled empty-state OR
 * per-pipeline status cards.
 *
 * Each pipeline card shows:
 *   - Colour-coded state chip (idle/draining/backoff/paused/disabled)
 *   - Thread liveness indicator (alive / dead / starting)
 *   - Pending counts (rows + blobs + delete_scope)
 *   - Last attempt + last success timestamps
 *   - Collapsed error block when last_error is set
 *
 * The renderer is reactive in one direction only: each card has a
 * stable [data-pipeline] root so `update(pipeline, snapshot)` can
 * replace ONLY that card's inner markup when an SSE event fires,
 * leaving the other pipeline's DOM untouched.
 */


/** State value → CSS modifier suffix (matches backup.css). */
const STATE_CLASS = {
  disabled: "bk-state-disabled",
  idle:     "bk-state-idle",
  draining: "bk-state-draining",
  backoff:  "bk-state-backoff",
  paused:   "bk-state-paused",
};


/** Pretty-print an ISO-8601 timestamp for the UI. Falls back to em-dash. */
function _fmtTs(iso) {
  if (!iso) return "—";
  // Strip fractional seconds + T separator for legibility.
  return iso.replace("T", " ").replace(/\.\d+/, "").replace(/Z?$/, " UTC");
}


/**
 * Build the inner markup for a single pipeline card given a /status
 * payload's per-pipeline dict (enabled / thread_alive / snapshot).
 *
 * Always-present elements: state chip, thread indicator, pending
 * counts, timestamps. Conditional: starting placeholder when
 * enabled=true + no snapshot; error block when last_error.
 */
function _renderPipelineInner(doc, name, info) {
  const { enabled, thread_alive, snapshot } = info;

  if (!enabled) {
    return `
      <div class="bk-pipeline-head">
        <span class="bk-pipeline-name">${name.toUpperCase()}</span>
        <span class="bk-state-chip ${STATE_CLASS.disabled}">DISABLED</span>
      </div>
      <p class="bk-empty-pipeline">Pipeline disabled — flip the toggle below to enable it.</p>
    `;
  }

  if (snapshot == null) {
    // Worker hasn't published yet. thread_alive will flip true once it
    // does — operators see "starting…" with a spinner.
    return `
      <div class="bk-pipeline-head">
        <span class="bk-pipeline-name">${name.toUpperCase()}</span>
        <span class="bk-state-chip ${STATE_CLASS.idle}">STARTING</span>
      </div>
      <p class="bk-empty-pipeline">
        Waiting for first drain… ${thread_alive ? "" : "(thread not yet started)"}
      </p>
    `;
  }

  const stateCls = STATE_CLASS[snapshot.state] || STATE_CLASS.idle;
  const aliveBadge = thread_alive
    ? `<span class="bk-thread-alive" title="Worker thread is running">●&nbsp;alive</span>`
    : `<span class="bk-thread-dead"  title="Worker thread NOT running — check logs">✕&nbsp;not running</span>`;

  const errorBlock = snapshot.last_error
    ? `<details class="bk-error-wrap">
         <summary>Last error</summary>
         <pre class="bk-error">${_escapeHtml(snapshot.last_error)}</pre>
       </details>`
    : "";

  // Pending counts: the totals depend on pipeline (db has all three;
  // files only has blobs). Show all three uniformly — the API contract
  // guarantees the fields exist on every snapshot.
  return `
    <div class="bk-pipeline-head">
      <span class="bk-pipeline-name">${name.toUpperCase()}</span>
      <span class="bk-state-chip ${stateCls}">${snapshot.state.toUpperCase()}</span>
      ${aliveBadge}
    </div>
    <div class="bk-pending-row">
      <span class="bk-pending"><strong>${snapshot.pending_rows}</strong> rows</span>
      <span class="bk-pending"><strong>${snapshot.pending_blobs}</strong> blobs</span>
      <span class="bk-pending"><strong>${snapshot.pending_delete_scope}</strong> deletes</span>
    </div>
    <div class="bk-ts-row">
      <span class="bk-ts">Last attempt: ${_fmtTs(snapshot.last_attempt_at)}</span>
      <span class="bk-ts">Last success: ${_fmtTs(snapshot.last_success_at)}</span>
    </div>
    <div class="bk-backoff-row">
      <span class="bk-backoff">Backoff: ${snapshot.backoff_delay_s}s</span>
    </div>
    ${errorBlock}
  `;
}


function _escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}


/**
 * Render the status panel root + initial paint.
 *
 * Returns the root <div> element with `update(pipeline, snapshot)`
 * monkey-patched on so the orchestrator can refresh a single card.
 */
export function renderStatusPanel({ status, ownerDocument }) {
  const doc = ownerDocument || document;
  const root = doc.createElement("section");
  root.className = "card bk-status-panel";
  root.id = "bk-status";

  // Empty-state: master switch off → just say so.
  if (!status.enabled) {
    root.innerHTML = `
      <h3>Backup status</h3>
      <p class="bk-empty">Backup is disabled. Enable it below to start shipping data.</p>
    `;
    // Still attach the update() shim so unknown-pipeline pushes are
    // harmless after a future enable.
    root.update = () => {};
    return root;
  }

  const pausedBanner = status.paused
    ? `<p class="bk-paused-banner">Shipping is paused. Resume from the controls below.</p>`
    : "";

  root.innerHTML = `
    <h3>Backup status</h3>
    ${pausedBanner}
    <div class="bk-pipelines">
      <div class="bk-pipeline-card" data-pipeline="db"></div>
      <div class="bk-pipeline-card" data-pipeline="files"></div>
    </div>
  `;

  // Paint each pipeline.
  for (const name of ["db", "files"]) {
    const card = root.querySelector(`[data-pipeline='${name}']`);
    card.innerHTML = _renderPipelineInner(doc, name, status.pipelines[name]);
  }

  /**
   * Update a single pipeline card from an SSE snapshot. The orchestrator
   * is responsible for upstream filtering — we just re-render the inner
   * markup. Defensive: unknown pipelines are silently ignored.
   */
  root.update = function (pipeline, snapshot) {
    const card = root.querySelector(`[data-pipeline='${pipeline}']`);
    if (!card) return;
    // Reconstruct the {enabled, thread_alive, snapshot} envelope from
    // the snapshot — the SSE event carries only the snapshot dict, but
    // a snapshot only exists when the pipeline is enabled, and the
    // worker only publishes after starting → thread_alive is true at
    // the moment of publication. (Stale snapshots from a freshly-stopped
    // pipeline are filtered by the orchestrator against /status.)
    card.innerHTML = _renderPipelineInner(doc, pipeline, {
      enabled: true,
      thread_alive: true,
      snapshot,
    });
  };

  return root;
}
