// Run: node tests/js/test_pca.mjs — exit 0 on pass, 1 on fail.
import { pca2d } from '../../static/js/pca.mjs';

let failures = 0;
function expect(label, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a === e) console.log(`  ok  ${label}`);
  else { console.log(`  FAIL ${label}\n    expected: ${e}\n    actual:   ${a}`); failures++; }
}

// Empty input → empty output
expect('empty', pca2d([]), []);

// Single point → single (0,0)
expect('singleton',
  pca2d([[1, 2, 3, 4]]).map(p => [Math.round(p[0]), Math.round(p[1])]),
  [[0, 0]]);

// Two identical points → both at origin
expect('duplicates',
  pca2d([[1, 2, 3], [1, 2, 3]]).map(p => [Math.round(p[0]), Math.round(p[1])]),
  [[0, 0], [0, 0]]);

// Three points along a line in N-d should land on a 1-D x-axis (y ~ 0)
const linePts = [[0, 0, 0, 0], [1, 1, 1, 1], [2, 2, 2, 2]];
const lineProj = pca2d(linePts);
expect('collinear: y values cluster near 0',
  lineProj.every(p => Math.abs(p[1]) < 0.5),
  true);
expect('collinear: x values are spread',
  lineProj[0][0] !== lineProj[2][0],
  true);

// Synthetic 2-cluster: should produce visible separation along x
const clusters = [
  [0, 0, 0, 0], [0.1, 0, 0, 0], [0, 0.1, 0, 0],
  [10, 10, 10, 10], [10.1, 10, 10, 10], [10, 10.1, 10, 10],
];
const cProj = pca2d(clusters);
const meanX1 = (cProj[0][0] + cProj[1][0] + cProj[2][0]) / 3;
const meanX2 = (cProj[3][0] + cProj[4][0] + cProj[5][0]) / 3;
expect('two clusters: mean x differs by > 5',
  Math.abs(meanX1 - meanX2) > 5,
  true);

if (failures) { console.error(`\n${failures} test(s) failed`); process.exit(1); }
console.log('\nAll PCA tests passed');
