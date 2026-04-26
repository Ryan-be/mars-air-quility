// Fixture-based test for the client-side computeCentroids helper.
// Run: node tests/js/test_compute_centroids.mjs
// Exit 0 on pass, 1 on failure.

import { computeCentroids } from '../../static/js/compute_centroids.mjs';

let failures = 0;
function expect(label, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a === e) {
    console.log(`  ok  ${label}`);
  } else {
    console.log(`  FAIL ${label}\n       expected: ${e}\n       actual:   ${a}`);
    failures++;
  }
}

// --- Manual mode ---------------------------------------------------------
const two = [
  { id: 'A', alert_count: 2, primary_count: 2 },
  { id: 'B', alert_count: 2, primary_count: 2 },
];
const c1 = computeCentroids(two, 'manual');
expect('manual: 2 incidents fit on one row (y=0)',
  [c1.A.y, c1.B.y], [0, 0]);
expect('manual: A left of B',
  c1.A.x < c1.B.x, true);

// Four incidents → 2 rows of 2.
const four = [
  { id: 'A', alert_count: 2, primary_count: 2 },
  { id: 'B', alert_count: 2, primary_count: 2 },
  { id: 'C', alert_count: 2, primary_count: 2 },
  { id: 'D', alert_count: 2, primary_count: 2 },
];
const c2 = computeCentroids(four, 'manual');
expect('manual: row 1 centre y > 0', c2.C.y > 0, true);
expect('manual: rows A and C share x lane', c2.A.x === c2.C.x, true);

// --- Row height scales with stack depth (one-lane worst case) ------------
// clusterHalfHeight(primary, manual) =
//   LANE_HEIGHT_PX(44) + stackSlots * STACK_DY_PX(20) + CYTO_HULL_PADDING_Y(30)
// where stackSlots = min(MAX_STACK_STEPS, max(1, ceil((primary-1)/2)))
// For primary=30: stackSlots=min(5, ceil(29/2))=5, halfH = 44 + 100 + 30 = 174
// For primary=2:  stackSlots=min(5, ceil(1/2))=1,  halfH = 44 + 20 + 30 = 94
const deep = [
  { id: 'D1', alert_count: 30, primary_count: 30 },
  { id: 'D2', alert_count: 30, primary_count: 30 },
  { id: 'D3', alert_count: 30, primary_count: 30 },
  { id: 'D4', alert_count: 30, primary_count: 30 },
];
const shallow = [
  { id: 'S1', alert_count: 2, primary_count: 2 },
  { id: 'S2', alert_count: 2, primary_count: 2 },
  { id: 'S3', alert_count: 2, primary_count: 2 },
  { id: 'S4', alert_count: 2, primary_count: 2 },
];
const cd = computeCentroids(deep, 'manual');
const cs = computeCentroids(shallow, 'manual');
expect('deep rows get more vertical gap than shallow rows',
  cd.D3.y > cs.S3.y, true);

// No-overlap invariant — row spacing must accommodate full hull extent of
// both rows: delta >= half_h(r) + half_h(r+1).
// For deep rows half_h = 174: delta >= 348.
expect('deep rows: row1_y - row0_y >= 348 (2 * 174)',
  cd.D3.y - cd.D1.y >= 348, true);

// For shallow rows half_h = 94: delta >= 188.
expect('shallow rows: row1_y - row0_y >= 188 (2 * 94)',
  cs.S3.y - cs.S1.y >= 188, true);

// One-lane worst case — ten primaries all-same-severity pile into one
// lane and reach step ±5. halfHeight must match deep rows.
const oneLane = [
  { id: 'L1', alert_count: 10, primary_count: 10 },
  { id: 'L2', alert_count: 10, primary_count: 10 },
  { id: 'L3', alert_count: 10, primary_count: 10 },
  { id: 'L4', alert_count: 10, primary_count: 10 },
];
const cl = computeCentroids(oneLane, 'manual');
expect('one-lane 10 primaries: row1_y - row0_y >= 348',
  cl.L3.y - cl.L1.y >= 348, true);

// --- Clamp boundary: single-primary incident --------------------------
// ceil((1-1)/2) = 0 → clamp to 1 slot → halfH = 44 + 20 + 30 = 94.
// Locks the Math.max(1, ...) clamp so a refactor that drops the clamp
// would fail here instead of silently shrinking hulls for lone events.
const lone = [
  { id: 'X1', alert_count: 1, primary_count: 1 },
  { id: 'X2', alert_count: 1, primary_count: 1 },
  { id: 'X3', alert_count: 1, primary_count: 1 },
  { id: 'X4', alert_count: 1, primary_count: 1 },
];
const cxl = computeCentroids(lone, 'manual');
expect('singleton-primary clamp: row1_y - row0_y >= 188 (2 * 94)',
  cxl.X3.y - cxl.X1.y >= 188, true);

// --- Chronological mode: all clusters on row 0 ---------------------------
const chronoIn = [
  { id: 'A', alert_count: 2, primary_count: 2, started_at: '2026-04-24 10:00:00' },
  { id: 'B', alert_count: 2, primary_count: 2, started_at: '2026-04-24 09:00:00' },
  { id: 'C', alert_count: 2, primary_count: 2, started_at: '2026-04-24 11:00:00' },
];
const cc = computeCentroids(chronoIn, 'chronological');
expect('chronological: all y = 0',
  [cc.A.y, cc.B.y, cc.C.y], [0, 0, 0]);
expect('chronological: sorted by started_at ascending (B<A<C)',
  cc.B.x < cc.A.x && cc.A.x < cc.C.x, true);

// --- Compact mode: narrower widths than manual ---------------------------
const ck = computeCentroids(two, 'compact');
expect('compact: B.x < manual B.x (tighter packing)',
  ck.B.x < c1.B.x, true);

if (failures) {
  console.error(`\n${failures} test(s) failed`);
  process.exit(1);
}
console.log('\nAll computeCentroids tests passed');
