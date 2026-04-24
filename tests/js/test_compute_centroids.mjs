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

// --- Row height scales with stack depth ----------------------------------
// 30 primary alerts → full stack (primary/3 >= 5) → half_h capped at 124.
// 2 primary alerts → shallow stack (ceil(2/3) = 1) → half_h = 44 + 16 = 60.
// Two rows of deep incidents must have MORE vertical gap than two rows of
// shallow incidents.
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

// No-overlap invariant: adjacent rows' clusters must not overlap
// vertically. row_gap >= half_h(r) + half_h(r+1).
// For 30-alert rows: half_h = 124. Two rows: delta >= 248 + INTER_ROW_GAP.
expect('deep rows: row1_y - row0_y >= 248',
  cd.D3.y - cd.D1.y >= 248, true);

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
