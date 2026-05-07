/**
 * Stat tile: big number + label + optional sub. The "required-marker"
 * left border is green for capability=is_required, blue otherwise. The
 * stat-tile grid is data-driven from the unit's reported capabilities,
 * so the same component renders everywhere.
 *
 * Phase 2 sense-only-mode: when `health` is non-"connected", a small
 * pill is appended to the label so the user knows the tile is showing
 * stale data — same four health states the actuator buttons use.
 */

const HEALTH_PILL_LABELS = {
  untested: { text: "⏱ Untested", className: "untested" },
  unresponsive: { text: "⚠ Unresponsive", className: "unresponsive" },
  no_hardware: { text: "🔌 Not connected", className: "no-hardware" },
};

export function renderStatTile({
  value, label, sub = null, isRequired = false, variant = "normal",
  health = "connected",
  ownerDocument = (typeof document !== "undefined" ? document : null),
}) {
  const doc = ownerDocument;
  const tile = doc.createElement("div");
  tile.className = `du-stat ${isRequired ? "required-marker" : "optional-marker"}`;

  const v = doc.createElement("div");
  v.className = `v${variant === "warn" ? " warn" : variant === "ok" ? " ok" : ""}`;
  v.textContent = value;
  tile.appendChild(v);

  const l = doc.createElement("div");
  l.className = "l";
  l.textContent = label;
  // Append a health pill alongside the label when the capability is not
  // reporting cleanly. `connected` is the default and renders nothing —
  // existing tests that don't pass `health` continue to work unchanged.
  const pill = HEALTH_PILL_LABELS[health];
  if (pill) {
    const p = doc.createElement("span");
    p.className = `cap-health-pill ${pill.className}`;
    p.textContent = pill.text;
    l.appendChild(p);
  }
  tile.appendChild(l);

  if (sub) {
    const s = doc.createElement("div");
    s.className = "sub";
    s.textContent = sub;
    tile.appendChild(s);
  }
  return tile;
}
