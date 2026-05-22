/**
 * Radial auto-layout for the MLSS topology graph.
 *
 * Pure function: given the full node list (hub + grows + effectors), it
 * returns a `{nodeId: {x, y}}` map in world coordinates with the hub at
 * the origin. The page boot layers server-persisted overrides on top so
 * a user who has dragged a node sees their position, not the radial one.
 *
 * Algorithm (ported verbatim from
 * `docs/assets/effector-map-handoff/layout.js`):
 *
 *   1. Hub at (0, 0).
 *   2. Effectors parented to the hub fan across the upper half — ring
 *      radius 320, angles −160° → −20° (i.e. above + slightly L/R of
 *      the hub).
 *   3. Grow units fan across the lower half — ring radius 360, angles
 *      +20° → +160°. Each grow's angle is cached so its children can
 *      cluster around it.
 *   4. Per-grow effectors cluster around their parent grow on a smaller
 *      ring (r=175) facing AWAY from the hub — angles span ±50° of the
 *      grow's outward direction.
 *
 * Single-node degenerate cases (one effector, one grow) get pinned to
 * the centre of their arc (−90° / +90°) so a fresh-install tent with
 * one grow doesn't render the single card stuffed against the canvas
 * edge.
 */

const ROOM_EFFECTOR_RING_R = 320;
const GROW_RING_R = 360;
const SUB_EFFECTOR_R = 175;


function deg(d) {
  return (d * Math.PI) / 180;
}


/**
 * Compute deterministic radial positions for every node in the topology.
 *
 * @param {Array<{id: string, kind: string, parent?: string}>} nodes
 * @returns {Object<string, {x: number, y: number}>}
 */
export function autoLayout(nodes) {
  const positions = {};

  // 1. Hub at world origin. Bail early if the input has no hub — the
  // page boot guarantees one but the pure function shouldn't crash.
  const hub = nodes.find((n) => n.kind === "hub");
  if (!hub) return positions;
  positions[hub.id] = { x: 0, y: 0 };

  // 2. Room effectors → upper arc. Spread evenly across −160° → −20°
  // (above the hub, slightly tilted left + right).
  const roomEffectors = nodes.filter(
    (n) => n.kind === "effector" && n.parent === hub.id,
  );
  const roomCount = roomEffectors.length;
  roomEffectors.forEach((n, i) => {
    const startA = -160;
    const endA = -20;
    const a = roomCount === 1
      ? -90
      : startA + (endA - startA) * (i / (roomCount - 1));
    positions[n.id] = {
      x: Math.cos(deg(a)) * ROOM_EFFECTOR_RING_R,
      y: Math.sin(deg(a)) * ROOM_EFFECTOR_RING_R,
    };
  });

  // 3. Grow units → lower arc (+20° → +160°). Cache each grow's angle
  // so its children can cluster around it (step 4).
  const grows = nodes.filter((n) => n.kind === "grow");
  const growCount = grows.length;
  const growAngles = {};
  grows.forEach((n, i) => {
    const startA = 20;
    const endA = 160;
    const a = growCount === 1
      ? 90
      : startA + (endA - startA) * (i / (growCount - 1));
    growAngles[n.id] = a;
    positions[n.id] = {
      x: Math.cos(deg(a)) * GROW_RING_R,
      y: Math.sin(deg(a)) * GROW_RING_R,
    };
  });

  // 4. Per-grow effectors → smaller cluster around the grow. The
  // "outward" direction is the same angle from hub-to-grow; we fan
  // ±50° around it so the children sit on the far side of the grow
  // (away from the hub).
  grows.forEach((grow) => {
    const subs = nodes.filter(
      (n) => n.kind === "effector" && n.parent === grow.id,
    );
    if (!subs.length) return;
    const baseA = growAngles[grow.id];
    const spread = 100; // total degrees, ±50°
    const halfSpread = spread / 2;
    subs.forEach((s, i) => {
      const t = subs.length === 1 ? 0.5 : i / (subs.length - 1);
      const a = baseA - halfSpread + spread * t;
      positions[s.id] = {
        x: positions[grow.id].x + Math.cos(deg(a)) * SUB_EFFECTOR_R,
        y: positions[grow.id].y + Math.sin(deg(a)) * SUB_EFFECTOR_R,
      };
    });
  });

  return positions;
}
