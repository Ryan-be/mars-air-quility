/**
 * Tests for the pure auto-layout function (Phase 5 Task 5.1).
 *
 * `autoLayout(nodes)` returns a deterministic `{id: {x, y}}` map for
 * the radial topology arrangement:
 *   - Hub sits at world origin (0, 0).
 *   - Effectors whose `parent === hub.id` arc above the hub (y < 0).
 *   - Grow units arc below the hub (y > 0), spread left-to-right.
 *
 * Port of `docs/assets/effector-map-handoff/layout.js` to ESM with the
 * localStorage helpers dropped — server-persisted positions are layered
 * on top by the page boot (Phase 11), not by this pure function.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { autoLayout } from "../../static/js/topology/layout.mjs";


test("autoLayout: single hub sits at origin", () => {
  const positions = autoLayout([{ id: "hub", kind: "hub" }]);
  assert.deepEqual(positions.hub, { x: 0, y: 0 });
});


test("autoLayout: a hub-scoped effector lands above the hub (y < 0)", () => {
  const nodes = [
    { id: "hub", kind: "hub" },
    { id: "effector:1", kind: "effector", parent: "hub" },
  ];
  const positions = autoLayout(nodes);
  assert.ok(positions["effector:1"], "effector should have a position");
  // Above the hub means negative y in world coords (we render y-down so
  // negative y maps to the upper half of the canvas).
  assert.ok(
    positions["effector:1"].y < 0,
    `expected y < 0, got ${positions["effector:1"].y}`,
  );
});


test("autoLayout: multiple grows arc below the hub, leftmost has smallest x", () => {
  const nodes = [
    { id: "hub", kind: "hub" },
    { id: "grow:1", kind: "grow", parent: "hub" },
    { id: "grow:2", kind: "grow", parent: "hub" },
    { id: "grow:3", kind: "grow", parent: "hub" },
  ];
  const positions = autoLayout(nodes);
  // All grows should be below the hub (y > 0).
  for (const id of ["grow:1", "grow:2", "grow:3"]) {
    assert.ok(positions[id].y > 0, `${id} should be below hub (y > 0)`);
  }
  // Leftmost = smallest x. The prototype spreads grows across angles
  // 20° → 160°, so grow:1 (angle 20° on the right) actually ends up
  // with the LARGEST x. Per spec: "leftmost has smallest x".
  // In the radial layout, the index assigned to the leftmost slot is
  // the last one (i = n-1, angle 160°, cos(160°) is most negative).
  const xs = [
    positions["grow:1"].x,
    positions["grow:2"].x,
    positions["grow:3"].x,
  ];
  assert.ok(
    Math.min(...xs) < Math.max(...xs),
    "grows should span a range of x values",
  );
  // The radial-arc layout in the prototype assigns angle 20° to i=0 and
  // 160° to i=n-1, so grow:3 (last) is the leftmost (negative x).
  assert.ok(
    positions["grow:3"].x < positions["grow:1"].x,
    `expected grow:3.x < grow:1.x (rightmost first), got ` +
      `${positions["grow:3"].x} vs ${positions["grow:1"].x}`,
  );
});
