/**
 * Reusable status pill component.
 * Maps unit status (online/stale/caution/offline) to an AstroUXDS-aligned
 * coloured pill. Used on fleet cards, detail header, anywhere we surface
 * a unit's health.
 */

export function classifyUnitStatus(lastSeenAt, now = new Date()) {
  if (lastSeenAt === null || lastSeenAt === undefined) return "offline";
  const ageMs = now.getTime() - new Date(lastSeenAt).getTime();
  if (ageMs < 30 * 1000) return "online";
  if (ageMs < 5 * 60 * 1000) return "stale";
  return "offline";
}

const STATUS_LABELS = {
  online: "Nominal",
  caution: "Caution",
  stale: "Stale",
  offline: "Offline",
};

const STATUS_CLASSES = {
  online: "st-normal",
  caution: "st-caution",
  stale: "st-standby",
  offline: "st-serious",
};

export function renderStatusPill(status, opts = {}) {
  const { ownerDocument = (typeof document !== "undefined" ? document : null) } = opts;
  let el;
  if (ownerDocument) {
    el = ownerDocument.createElement("span");
  } else {
    // Node test env — return a minimal stand-in
    el = {
      tagName: "SPAN",
      className: "",
      textContent: "",
    };
  }
  el.className = `gu-status ${STATUS_CLASSES[status] || ""}`;
  el.textContent = STATUS_LABELS[status] || "Unknown";
  return el;
}
