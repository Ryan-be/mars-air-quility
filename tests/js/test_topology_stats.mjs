/**
 * Tests for the `computeStats(nodes)` pure function (Phase 7 Task 7.2).
 *
 * Pure rollup that produces the data the topbar paints. Input is the
 * flat node array `_flattenTopology()` builds for the graph renderer.
 * Output:
 *
 *   {
 *     total:     total node count (hub + grows + effectors),
 *     active:    effectors with current_state === "on",
 *     grows:     count of kind === "grow",
 *     effectors: count of kind === "effector",
 *     auto:      effectors with mode === "auto",
 *     forced:    effectors with mode !== "auto",
 *   }
 *
 * No DOM, no side effects — input array in, summary dict out. Lives in
 * its own module so the boot orchestrator can import it without pulling
 * in any of the topbar's renderer footprint.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { computeStats } from "../../static/js/topology/stats.mjs";


function _node(over) {
  // Minimal node shape — caller layers fields as needed.
  return { id: "x:0", kind: "grow", ...over };
}


test("computeStats: empty array returns all zeroes", () => {
  const s = computeStats([]);
  assert.equal(s.total, 0);
  assert.equal(s.active, 0);
  assert.equal(s.grows, 0);
  assert.equal(s.effectors, 0);
  assert.equal(s.auto, 0);
  assert.equal(s.forced, 0);
});


test("computeStats: counts the hub in total but not in grows/effectors", () => {
  const s = computeStats([
    { id: "hub", kind: "hub", label: "MLSS Hub" },
  ]);
  assert.equal(s.total, 1);
  assert.equal(s.grows, 0);
  assert.equal(s.effectors, 0);
  // Hub has no mode/state so neither auto/forced/active touches it.
  assert.equal(s.auto, 0);
  assert.equal(s.forced, 0);
  assert.equal(s.active, 0);
});


test("computeStats: counts grow kind nodes", () => {
  const s = computeStats([
    _node({ id: "grow:1", kind: "grow" }),
    _node({ id: "grow:2", kind: "grow" }),
    _node({ id: "grow:3", kind: "grow" }),
  ]);
  assert.equal(s.total, 3);
  assert.equal(s.grows, 3);
  assert.equal(s.effectors, 0);
});


test("computeStats: counts effector kind nodes", () => {
  const s = computeStats([
    _node({ id: "effector:1", kind: "effector", mode: "auto",
            current_state: "off" }),
    _node({ id: "effector:2", kind: "effector", mode: "auto",
            current_state: "off" }),
  ]);
  assert.equal(s.total, 2);
  assert.equal(s.effectors, 2);
  assert.equal(s.grows, 0);
});


test("computeStats: active counts only effectors whose state is 'on'", () => {
  const s = computeStats([
    _node({ id: "effector:1", kind: "effector", mode: "auto",
            current_state: "on" }),
    _node({ id: "effector:2", kind: "effector", mode: "auto",
            current_state: "off" }),
    _node({ id: "effector:3", kind: "effector", mode: "on",
            current_state: "on" }),
    _node({ id: "effector:4", kind: "effector", mode: "off",
            current_state: "off" }),
    // Grows with current_state="on" should NOT count toward active.
    _node({ id: "grow:1", kind: "grow", current_state: "on" }),
  ]);
  assert.equal(s.active, 2,
    `expected 2 active effectors (one of which has mode=auto, ` +
    `the other mode=on), got ${s.active}`);
});


test("computeStats: auto/forced partitions effectors by mode", () => {
  const s = computeStats([
    _node({ id: "effector:1", kind: "effector", mode: "auto",
            current_state: "off" }),
    _node({ id: "effector:2", kind: "effector", mode: "auto",
            current_state: "on" }),
    _node({ id: "effector:3", kind: "effector", mode: "on",
            current_state: "on" }),
    _node({ id: "effector:4", kind: "effector", mode: "off",
            current_state: "off" }),
    _node({ id: "effector:5", kind: "effector", mode: "off",
            current_state: "off" }),
  ]);
  assert.equal(s.auto, 2);
  assert.equal(s.forced, 3, `mode='on' + mode='off' both count as forced; ` +
    `got ${s.forced}`);
});


test("computeStats: full mixed snapshot", () => {
  // The realistic shape — one hub, two grows, four effectors.
  const s = computeStats([
    { id: "hub", kind: "hub" },
    _node({ id: "grow:1", kind: "grow" }),
    _node({ id: "grow:2", kind: "grow" }),
    _node({ id: "effector:1", kind: "effector", mode: "auto",
            current_state: "on" }),
    _node({ id: "effector:2", kind: "effector", mode: "auto",
            current_state: "off" }),
    _node({ id: "effector:3", kind: "effector", mode: "on",
            current_state: "on" }),
    _node({ id: "effector:4", kind: "effector", mode: "off",
            current_state: "off" }),
  ]);
  assert.equal(s.total, 7);
  assert.equal(s.grows, 2);
  assert.equal(s.effectors, 4);
  assert.equal(s.active, 2);     // effector:1 (auto+on) + effector:3 (forced on)
  assert.equal(s.auto, 2);       // effector:1 + effector:2
  assert.equal(s.forced, 2);     // effector:3 + effector:4
});


test("computeStats: missing mode is treated as forced", () => {
  // A node that's missing mode (e.g. an old DB row before auto_mode
  // existed) shouldn't crash; rollup it as forced so the operator's
  // "needs attention" counter doesn't silently lose entries.
  const s = computeStats([
    _node({ id: "effector:1", kind: "effector", current_state: "off" }),
  ]);
  assert.equal(s.effectors, 1);
  assert.equal(s.auto, 0);
  assert.equal(s.forced, 1);
});
