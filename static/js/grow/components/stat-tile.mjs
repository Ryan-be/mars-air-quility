/**
 * Stat tile: big number + label + optional sub. The "required-marker"
 * left border is green for capability=is_required, blue otherwise. The
 * stat-tile grid is data-driven from the unit's reported capabilities,
 * so the same component renders everywhere.
 *
 * Phase 2 sense-only-mode: when `health` is non-"connected", a small
 * pill is appended to the label so the user knows the tile is showing
 * stale data — same four health states the actuator buttons use.
 *
 * Plant-happiness overlay (later): when `happiness` is non-null,
 * the left-border colour shifts away from the is_required green/blue
 * to a green/amber/red hint reflecting whether the reading is in the
 * ideal/tolerated/critical range for the unit's plant_type +
 * current_phase. The ideal range is also rendered as a "happy-range"
 * subtext beneath the label AND mirrored onto the tile's `title`
 * attribute so the operator can hover for the same info.
 */

const HEALTH_PILL_LABELS = {
  untested: { text: "⏱ Untested", className: "untested" },
  unresponsive: { text: "⚠ Unresponsive", className: "unresponsive" },
  no_hardware: { text: "🔌 Not connected", className: "no-hardware" },
};

// Map a happiness zone → CSS class suffix on the tile root. The 5
// zones collapse to 3 colour buckets:
//   ideal                                   → green
//   tolerated_low / tolerated_high          → amber
//   critical_low / critical_high            → red
// Anything else (null, unrecognised) → no extra class. The happy-*
// classes are appended AFTER required/optional-marker in the className
// string so the cascade resolves source-order in their favour without
// needing a specificity bump in CSS.
const HAPPINESS_TO_CLASS = {
  ideal: "happy-ideal",
  tolerated_low: "happy-tolerated",
  tolerated_high: "happy-tolerated",
  critical_low: "happy-critical",
  critical_high: "happy-critical",
};

export function renderStatTile({
  value, label, sub = null, isRequired = false, variant = "normal",
  health = "connected",
  happiness = null, idealRange = null,
  ownerDocument = (typeof document !== "undefined" ? document : null),
}) {
  const doc = ownerDocument;
  const tile = doc.createElement("div");
  let className = `du-stat ${isRequired ? "required-marker" : "optional-marker"}`;
  // Happiness class goes AFTER the marker class so the cascade picks
  // up its border-left-color override (same specificity, later wins).
  const happyClass = HAPPINESS_TO_CLASS[happiness];
  if (happyClass) {
    className += ` ${happyClass}`;
    // testid mirrors the class so tests can locate the tile by
    // happiness state without parsing className.
    tile.dataset.testid = `stat-tile-${happyClass}`;
  }
  tile.className = className;
  // Hover affordance: the same ideal_range text the happy-range
  // subtext shows is also surfaced as the title= attribute, so an
  // operator who's already squinting at the big number can hover to
  // see the bounds without scanning down to the subtext.
  if (idealRange) {
    tile.title = `Ideal: ${idealRange}`;
  }

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

  // Happiness range subtext sits between the label and any caller-
  // supplied `sub` so it reads "Moisture / Ideal: 35–60 % / target 55%"
  // top-to-bottom. Hidden when idealRange isn't passed, which keeps
  // backward-compat with the non-happiness consumers (lux, light, etc).
  if (idealRange && happyClass) {
    const range = doc.createElement("div");
    range.className = "happy-range";
    range.dataset.testid = "happy-range";
    range.textContent = `Ideal: ${idealRange}`;
    tile.appendChild(range);
  }

  if (sub) {
    const s = doc.createElement("div");
    s.className = "sub";
    s.textContent = sub;
    tile.appendChild(s);
  }
  return tile;
}
