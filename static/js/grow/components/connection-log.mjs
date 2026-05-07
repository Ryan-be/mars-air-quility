/**
 * Connection log table — second section of the Diagnostics tab panel.
 *
 * Renders the last 20 online/offline events from the consolidated
 * diagnostics endpoint (server-side filter on
 * `kind IN ('online', 'offline')`). Rows arrive newest-first by id DESC
 * so an offline entry's "duration offline" value is the gap between
 * THAT row's timestamp_utc and the timestamp_utc of the most-recent
 * earlier `online` row.
 *
 * Pairing algorithm (client-side, since the server doesn't pre-compute
 * pair edges):
 *
 *   - Walk rows in id-DESC order (the order they arrive).
 *   - When we see an `offline`, look BACK in the array (lower index)
 *     for the most recent `online` whose id > offline.id. That online
 *     is the one that resolved this offline; the gap is its
 *     timestamp_utc - the offline's timestamp_utc.
 *   - When we see an `online`, look FORWARD (higher index, older rows)
 *     for the most recent `offline` whose id < online.id. The duration
 *     attached to the offline shows the resolution gap.
 *
 * Edge cases:
 *   - Unresolved offline (no later online in the visible window) →
 *     "ongoing" badge instead of a duration.
 *   - Initial online (no prior offline) → "—" in the duration column.
 *
 * Pure render — orchestrator (diagnostics-panel.mjs) hands us the
 * already-fetched `connection_log` slice. No state, no fetch.
 */


/** Format two timestamp_utc strings into a duration like "12m" / "1h 5m".
 *  Inputs are ISO-8601 strings or null. Returns "—" if either is null. */
function _formatDuration(startIso, endIso) {
  if (!startIso || !endIso) return "—";
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const minutes = Math.floor(ms / 60000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rem = minutes % 60;
  return rem === 0 ? `${hours}h` : `${hours}h ${rem}m`;
}


/** Format an ISO timestamp as a short "HH:MM:SS" — full date is rarely
 *  useful in the table since the operator is typically scanning the
 *  most recent ~10 events. The hover-tooltip retains the raw ISO so
 *  copy-paste into a debug ticket still has a precise timestamp. */
function _formatTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const h = String(d.getUTCHours()).padStart(2, "0");
  const m = String(d.getUTCMinutes()).padStart(2, "0");
  const s = String(d.getUTCSeconds()).padStart(2, "0");
  return `${h}:${m}:${s}`;
}


/**
 * For each `offline` row, find the resolving `online` (id > offline.id,
 * closest in time). Returns a Map keyed by offline.id → resolving online
 * row OR null when there is no later online in the visible window.
 *
 * Exported for direct test coverage — pairing is the only meaningful
 * logic here; the rest is DOM glue.
 */
export function _pairOfflineToOnline(rows) {
  const result = new Map();
  // The server returns rows id-DESC, but be defensive: copy + sort.
  // Walking id-ASC means a later online (higher id) appears AFTER its
  // resolving offline; the algorithm becomes a single forward scan.
  const sorted = rows.slice().sort((a, b) => a.id - b.id);
  for (let i = 0; i < sorted.length; i++) {
    const row = sorted[i];
    if (row.kind !== "offline") continue;
    let resolver = null;
    for (let j = i + 1; j < sorted.length; j++) {
      if (sorted[j].kind === "online") {
        resolver = sorted[j];
        break;
      }
      // If we hit another offline before an online, that's a malformed
      // log — keep walking (the second offline is its own row to render
      // with no resolver).
    }
    result.set(row.id, resolver);
  }
  return result;
}


/**
 * Build the connection-log table panel.
 *
 * @param {Array<object>} log  `connection_log` slice from the diagnostics
 *                             response — list of {id, timestamp_utc, kind, resolved_at}
 * @param {object} opts  { ownerDocument? }
 * @returns {HTMLElement}
 */
export function renderConnectionLog(log, opts = {}) {
  const doc = opts.ownerDocument || document;

  const wrap = doc.createElement("div");
  wrap.className = "du-panel diag-connection-log";
  wrap.dataset.testid = "diag-connection-log";

  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>🔌 Connection log</span>";
  wrap.appendChild(head);

  const body = doc.createElement("div");
  body.className = "diag-table-body";
  wrap.appendChild(body);

  if (!log || log.length === 0) {
    const empty = doc.createElement("p");
    empty.className = "diag-empty";
    empty.textContent = "No connection events recorded yet.";
    body.appendChild(empty);
    return wrap;
  }

  const table = doc.createElement("table");
  table.className = "diag-table";

  // Header
  const thead = doc.createElement("thead");
  const headRow = doc.createElement("tr");
  for (const lbl of ["Time", "Event", "Duration offline"]) {
    const th = doc.createElement("th");
    th.textContent = lbl;
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = doc.createElement("tbody");
  const pairs = _pairOfflineToOnline(log);

  // Render in the order we received (id DESC = newest first), so the
  // operator sees the most recent event at the top.
  for (const row of log) {
    const tr = doc.createElement("tr");
    tr.dataset.testid = `conn-row-${row.id}`;
    tr.dataset.kind = row.kind;

    const tdTime = doc.createElement("td");
    tdTime.textContent = _formatTime(row.timestamp_utc);
    tdTime.title = row.timestamp_utc || "";
    tr.appendChild(tdTime);

    const tdEvent = doc.createElement("td");
    tdEvent.className = `diag-event diag-event-${row.kind}`;
    tdEvent.textContent = row.kind;
    tr.appendChild(tdEvent);

    const tdDur = doc.createElement("td");
    tdDur.dataset.testid = `conn-row-${row.id}-duration`;
    if (row.kind === "offline") {
      const resolver = pairs.get(row.id);
      if (resolver) {
        tdDur.textContent = _formatDuration(
          row.timestamp_utc, resolver.timestamp_utc,
        );
      } else {
        tdDur.textContent = "ongoing";
        tdDur.className = "diag-duration-ongoing";
      }
    } else {
      // online row — duration column reads "—" (an online doesn't have
      // an "offline duration" of its own; the resolution is shown on
      // the offline row that came before it).
      tdDur.textContent = "—";
    }
    tr.appendChild(tdDur);

    tbody.appendChild(tr);
  }

  table.appendChild(tbody);
  body.appendChild(table);
  return wrap;
}
