/**
 * Buffer-inspector card — Diagnostics tab section that renders WHAT is
 * queued in the firmware-side buffers, not just the count.
 *
 * Closes the Phase 3 partial: the firmware-info card shows
 * `buffer_size` (a single integer) and the fleet card shows the
 * "📦 N buffered" badge, but neither exposed the per-msg-type
 * breakdown, total bytes, or oldest/newest timestamps. This card does.
 *
 * Two summaries side-by-side:
 *   - Text buffer   — telemetry/event/capabilities messages SQLite-backed
 *                     on the unit. Includes a per-msg_type kinds list.
 *   - Photo buffer  — JPEG queue on disk. Same shape minus `kinds`
 *                     (photos are all the same kind).
 *
 * Both are last-known-state caches updated on every Nth telemetry frame
 * (see SafetyLoop._BUFFER_SUMMARY_EVERY_N_TICKS); a brand-new unit may
 * have null summaries until the first piggyback lands. The server
 * stores them as JSON-in-TEXT on grow_units.last_*_summary_json with
 * omit-doesnt-clobber semantics so non-piggyback frames don't reset
 * them between updates.
 *
 * Pure render — no fetch, no state. The orchestrator
 * (diagnostics-panel.mjs) does the single fetch and passes both
 * summaries down to this child.
 */


/** Format bytes as human-friendly KB/MB. 0 → "0 B". 1024 → "1.0 KB".
 *  1_500_000 → "1.4 MB". Two-decimal for KB+, integer for B.
 *
 *  Exported for direct test access — the byte-formatting boundary
 *  cases (B vs KB vs MB) are easier to pin in isolation than via
 *  full-card render.
 */
export function _formatBytes(bytes) {
  if (bytes == null) return "—";
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${Math.round(n)} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}


/** Format an ISO-8601 timestamp string as a locale string. Null → "—".
 *  Invalid timestamps fall back to the raw string so the operator
 *  can see something even when the firmware sends junk (better than
 *  showing nothing or crashing the render).
 */
export function _formatTs(iso) {
  if (iso == null) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString();
}


/**
 * Build one summary card (text OR photo). Differentiated by whether
 * `summary.kinds` exists — text buffer has it, photo buffer doesn't.
 *
 * @param {string} label    "Message buffer" or "Photo buffer"
 * @param {string} testid   data-testid for the card root
 * @param {object|null} summary  parsed summary or null/undefined
 * @param {Document} doc    owner document
 * @returns {HTMLElement}
 */
function renderOneSummary(label, testid, summary, doc) {
  const wrap = doc.createElement("div");
  wrap.className = "diag-buffer-summary";
  wrap.dataset.testid = testid;

  const head = doc.createElement("h4");
  head.className = "diag-buffer-summary-label";
  head.textContent = label;
  wrap.appendChild(head);

  // Empty state: no summary received yet OR an empty buffer. Both
  // render the same way — operator just needs to know there's
  // nothing queued.
  if (!summary || (summary.size ?? 0) === 0) {
    const empty = doc.createElement("p");
    empty.className = "diag-buffer-summary-empty";
    empty.dataset.testid = `${testid}-empty`;
    empty.textContent = summary == null ? "no summary yet" : "empty";
    wrap.appendChild(empty);
    return wrap;
  }

  // Top-line stats: size + total bytes
  const stats = doc.createElement("p");
  stats.className = "diag-buffer-summary-stats";
  stats.dataset.testid = `${testid}-stats`;
  stats.textContent =
    `${summary.size} items · ${_formatBytes(summary.total_bytes)}`;
  wrap.appendChild(stats);

  // Time window (oldest → newest)
  const window = doc.createElement("p");
  window.className = "diag-buffer-summary-window";
  window.dataset.testid = `${testid}-window`;
  window.textContent =
    `oldest ${_formatTs(summary.oldest_ts)} · newest ${_formatTs(summary.newest_ts)}`;
  wrap.appendChild(window);

  // Per-msg_type breakdown — text buffer only. Photo buffer has no
  // kinds field; the JS branches on its presence rather than checking
  // the label string so future buffer types don't need to teach this
  // file about themselves.
  if (summary.kinds && Object.keys(summary.kinds).length > 0) {
    const kindsList = doc.createElement("ul");
    kindsList.className = "diag-buffer-summary-kinds";
    kindsList.dataset.testid = `${testid}-kinds`;
    for (const [kind, count] of Object.entries(summary.kinds)) {
      const li = doc.createElement("li");
      li.textContent = `${kind}: ${count}`;
      kindsList.appendChild(li);
    }
    wrap.appendChild(kindsList);
  }

  return wrap;
}


/**
 * Build the buffer-inspector card.
 *
 * @param {object} data  Diagnostics response body. Reads
 *                       `buffer_summary` + `photo_buffer_summary`.
 * @param {object} opts  { ownerDocument? }
 * @returns {HTMLElement}
 */
export function renderBufferInspector(data, opts = {}) {
  const doc = opts.ownerDocument || document;

  const wrap = doc.createElement("div");
  wrap.className = "du-panel diag-buffer-inspector";
  wrap.dataset.testid = "diag-buffer-inspector";

  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>📦 Buffered messages</span>";
  wrap.appendChild(head);

  const body = doc.createElement("div");
  body.className = "diag-buffer-inspector-body";
  wrap.appendChild(body);

  body.appendChild(renderOneSummary(
    "Message buffer", "diag-buffer-text",
    data.buffer_summary, doc,
  ));
  body.appendChild(renderOneSummary(
    "Photo buffer", "diag-buffer-photos",
    data.photo_buffer_summary, doc,
  ));

  return wrap;
}
