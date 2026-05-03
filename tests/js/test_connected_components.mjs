// Fixture-based test for the client-side connectedComponents helper.
// Run: node tests/js/test_connected_components.mjs
// Exit 0 on pass, 1 on failure.

import { connectedComponents } from '../../static/js/connected_components.mjs';

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

// 1. Empty
expect('empty input', connectedComponents([], [], 0.05), []);

// 2. Singleton
expect('single alert => singleton', connectedComponents([1], [], 0.05), [[1]]);

// 3. No edges => all singletons
expect('no edges => N singletons',
  connectedComponents([1, 2, 3], [], 0.05),
  [[1], [2], [3]]);

// 4. One edge => one component
expect('one edge joins two',
  connectedComponents([1, 2], [{from: 1, to: 2, p: 0.9}], 0.05),
  [[1, 2]]);

// 5. Transitive chain
expect('transitive chain A-B-C',
  connectedComponents([1, 2, 3],
    [{from: 1, to: 2, p: 0.8}, {from: 2, to: 3, p: 0.7}], 0.05),
  [[1, 2, 3]]);

// 6. Two disjoint subgraphs
expect('two disjoint components',
  connectedComponents([1, 2, 3, 4],
    [{from: 1, to: 2, p: 0.9}, {from: 3, to: 4, p: 0.9}], 0.05),
  [[1, 2], [3, 4]]);

// 7. Threshold splits
expect('high threshold hides weak edge',
  connectedComponents([1, 2],
    [{from: 1, to: 2, p: 0.15}], 0.50),
  [[1], [2]]);

expect('low threshold keeps weak edge',
  connectedComponents([1, 2],
    [{from: 1, to: 2, p: 0.15}], 0.05),
  [[1, 2]]);

if (failures > 0) {
  console.log(`\n${failures} test(s) failed`);
  process.exit(1);
}
console.log('\nAll connected_components JS tests passed');
