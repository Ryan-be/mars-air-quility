/**
 * Firmware info card — first section of the Diagnostics tab panel.
 *
 * Renders the three top-line metrics the consolidated diagnostics
 * endpoint surfaces from the grow_units row (Phase 3 Task 1 added the
 * columns; Task 2 keeps them current via WS messages):
 *
 *   - firmware_version (e.g. "0.3.1" or "dev")
 *   - uptime_s (seconds → formatted "Xd Yh Zm")
 *   - buffer_size (rows in the firmware-side replay buffer)
 *
 * Any null field → renders an em-dash so the card stays balanced even
 * when a unit hasn't reported metadata yet (e.g. firmware too old, or
 * still on the very first WS connection).
 *
 * Pure render — no fetch, no state. The orchestrator
 * (diagnostics-panel.mjs) does the single fetch and slices the response
 * into this child.
 */


/** Format seconds as "Xd Yh Zm". 30 seconds → "0m". 90 minutes → "1h 30m".
 *  Days are floored, hours = (s / 3600) % 24, minutes = (s / 60) % 60.
 *
 *  Exported for direct test access — covering the format helper alone
 *  lets us pin all three boundary cases (minutes-only, hours+minutes,
 *  days+hours+minutes) without re-rendering the full card each time.
 */
export function _formatUptime(seconds) {
  if (seconds == null) return "—";
  const s = Number(seconds);
  if (!Number.isFinite(s) || s < 0) return "—";
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const parts = [];
  if (days > 0) parts.push(`${days}d`);
  if (hours > 0 || days > 0) parts.push(`${hours}h`);
  parts.push(`${minutes}m`);
  return parts.join(" ");
}


/**
 * Build the firmware-info card.
 *
 * @param {object} data  Diagnostics response body. Reads `firmware_version`,
 *                       `uptime_s`, `buffer_size` directly off the top.
 * @param {object} opts  { ownerDocument? }
 * @returns {HTMLElement}
 */
export function renderFirmwareInfo(data, opts = {}) {
  const doc = opts.ownerDocument || document;

  const wrap = doc.createElement("div");
  wrap.className = "du-panel diag-firmware";
  wrap.dataset.testid = "diag-firmware";

  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>📡 Firmware</span>";
  wrap.appendChild(head);

  const grid = doc.createElement("div");
  grid.className = "diag-firmware-grid";
  wrap.appendChild(grid);

  const fields = [
    {
      label: "Version",
      value: data.firmware_version,
      testid: "diag-firmware-version",
    },
    {
      label: "Uptime",
      value: _formatUptime(data.uptime_s),
      // _formatUptime returns "—" for null already; guard preserves the
      // dash for the version + buffer-size fields too.
      testid: "diag-firmware-uptime",
    },
    {
      label: "Buffer size",
      value: data.buffer_size != null ? `${data.buffer_size} rows` : "—",
      testid: "diag-firmware-buffer",
    },
  ];

  for (const f of fields) {
    const cell = doc.createElement("div");
    cell.className = "diag-firmware-cell";

    const lbl = doc.createElement("div");
    lbl.className = "diag-firmware-label";
    lbl.textContent = f.label;
    cell.appendChild(lbl);

    const val = doc.createElement("div");
    val.className = "diag-firmware-value";
    val.dataset.testid = f.testid;
    val.textContent = f.value == null ? "—" : f.value;
    cell.appendChild(val);

    grid.appendChild(cell);
  }

  return wrap;
}
