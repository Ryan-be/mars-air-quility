/**
 * Sensor sanity list — third section of the Diagnostics tab panel.
 *
 * Renders one row per capability the server's diagnostics endpoint
 * surfaced (`sensor_sanity` slice), with one of three icons:
 *
 *   ✅ fresh      — last_seen_at exists, minutes_ago ≤ threshold
 *   ⚠ stale      — last_seen_at exists, minutes_ago > threshold
 *   🔌 never seen — last_seen_at is null
 *
 * The endpoint pre-computes is_stale + minutes_ago against the
 * configurable app_settings.grow_sensor_stale_threshold_min, so this
 * component just reads those flags and picks the icon. No threshold
 * logic in the frontend — the operator can tune the threshold via the
 * settings DB without a frontend release.
 *
 * minutes_ago renders to one decimal so a "0.5 min ago" reading is
 * legible alongside "12.3 min" stale entries. The threshold is also
 * surfaced in the stale-row text so the operator can see what the
 * server compared against without re-checking app_settings.
 *
 * Pure render — orchestrator hands us the slice; no fetch + no state.
 */


/** Pick an icon + a (status, severity) tuple for a single sanity row.
 *  Exported so tests can pin the per-row classification without
 *  re-rendering the full list. */
export function _classifySensor(row) {
  if (row.last_seen_at == null) {
    return { icon: "🔌", label: "never seen", severity: "never_seen" };
  }
  if (row.is_stale) {
    return { icon: "⚠", label: "STALE", severity: "stale" };
  }
  return { icon: "✅", label: "fresh", severity: "ok" };
}


function _formatMinutes(minutes) {
  if (minutes == null) return "—";
  if (!Number.isFinite(minutes)) return "—";
  // Always 1 decimal so "0.5 min ago" doesn't render as "0 min ago"
  // (which would suggest the sensor JUST reported, hiding any latency).
  return minutes.toFixed(1);
}


/**
 * Build the sensor-sanity panel.
 *
 * @param {Array<object>} sanity  `sensor_sanity` slice — list of
 *                                {channel, last_seen_at, minutes_ago,
 *                                 is_stale, stale_threshold_min}
 * @param {object} opts  { ownerDocument? }
 * @returns {HTMLElement}
 */
export function renderSensorSanity(sanity, opts = {}) {
  const doc = opts.ownerDocument || document;

  const wrap = doc.createElement("div");
  wrap.className = "du-panel diag-sensor-sanity";
  wrap.dataset.testid = "diag-sensor-sanity";

  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>🛰 Sensor sanity</span>";
  wrap.appendChild(head);

  const body = doc.createElement("div");
  body.className = "diag-sanity-body";
  wrap.appendChild(body);

  if (!sanity || sanity.length === 0) {
    const empty = doc.createElement("p");
    empty.className = "diag-empty";
    empty.textContent = "No capabilities reported yet.";
    body.appendChild(empty);
    return wrap;
  }

  const list = doc.createElement("ul");
  list.className = "diag-sanity-list";

  for (const row of sanity) {
    const li = doc.createElement("li");
    li.className = "diag-sanity-row";
    li.dataset.testid = `sanity-${row.channel}`;
    const cls = _classifySensor(row);
    li.dataset.severity = cls.severity;

    const iconEl = doc.createElement("span");
    iconEl.className = "diag-sanity-icon";
    iconEl.textContent = cls.icon;
    li.appendChild(iconEl);

    const channelEl = doc.createElement("span");
    channelEl.className = "diag-sanity-channel";
    channelEl.textContent = row.channel;
    li.appendChild(channelEl);

    const detailEl = doc.createElement("span");
    detailEl.className = "diag-sanity-detail";
    if (cls.severity === "never_seen") {
      detailEl.textContent = "— never seen";
    } else if (cls.severity === "stale") {
      const min = _formatMinutes(row.minutes_ago);
      const threshold = row.stale_threshold_min ?? "?";
      detailEl.textContent =
        `— STALE (${min} min ago, threshold ${threshold})`;
    } else {
      const min = _formatMinutes(row.minutes_ago);
      detailEl.textContent = `— last reading ${min} min ago`;
    }
    li.appendChild(detailEl);

    list.appendChild(li);
  }

  body.appendChild(list);
  return wrap;
}
