/**
 * Stat tile: big number + label + optional sub. The "required-marker"
 * left border is green for capability=is_required, blue otherwise. The
 * stat-tile grid is data-driven from the unit's reported capabilities,
 * so the same component renders everywhere.
 */

export function renderStatTile({
  value, label, sub = null, isRequired = false, variant = "normal",
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
  tile.appendChild(l);

  if (sub) {
    const s = doc.createElement("div");
    s.className = "sub";
    s.textContent = sub;
    tile.appendChild(s);
  }
  return tile;
}
