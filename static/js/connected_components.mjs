/**
 * Pure client-side mirror of Python's connected_components.
 *
 * Given an alert id list and an edges array [{from, to, p}, ...], return
 * a list of components where each component is the list of alert ids
 * that belong together under the given threshold.
 *
 * Semantics match the server: edges with p < threshold are ignored for
 * membership; isolated ids become singleton components. Result is
 * deterministic — components are sorted by the minimum id in each.
 */
export function connectedComponents(alertIds, edges, threshold) {
  const parent = new Map();
  alertIds.forEach(id => parent.set(id, id));
  const find = x => {
    while (parent.get(x) !== x) {
      parent.set(x, parent.get(parent.get(x)));  // path compression
      x = parent.get(x);
    }
    return x;
  };
  const union = (x, y) => {
    const rx = find(x), ry = find(y);
    if (rx !== ry) parent.set(rx, ry);
  };
  for (const e of edges) {
    if (e.p < threshold) continue;
    if (parent.has(e.from) && parent.has(e.to)) union(e.from, e.to);
  }
  const buckets = new Map();
  for (const id of alertIds) {
    const root = find(id);
    if (!buckets.has(root)) buckets.set(root, []);
    buckets.get(root).push(id);
  }
  // Deterministic: sort by min id within each component.
  return Array.from(buckets.values())
    .sort((a, b) => Math.min(...a) - Math.min(...b));
}
