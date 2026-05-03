// Pure 2-D PCA via power iteration. No DOM, no dependencies — Node-testable.
//
// pca2d(rows): given an N×D matrix (array of arrays), return an N×2 matrix
// of (x, y) coordinates projecting each row onto its first two principal
// components. Centred to mean zero; not scaled.

function meanVec(rows) {
  const d = rows[0].length;
  const m = new Array(d).fill(0);
  for (const r of rows) for (let i = 0; i < d; i++) m[i] += r[i];
  for (let i = 0; i < d; i++) m[i] /= rows.length;
  return m;
}

function dot(a, b) { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * b[i]; return s; }
function normalise(v) {
  const n = Math.sqrt(dot(v, v)) || 1;
  return v.map(x => x / n);
}

function powerIter(centred, deflate) {
  // 30 iterations is more than enough for a 32-d covariance matrix.
  const d = centred[0].length;
  let v = new Array(d).fill(0).map(() => Math.random() - 0.5);
  v = normalise(v);
  for (let it = 0; it < 30; it++) {
    // multiply by C = Xᵀ X (we never form C explicitly).
    const Xv = centred.map(row => dot(row, v));
    const next = new Array(d).fill(0);
    for (let i = 0; i < centred.length; i++)
      for (let j = 0; j < d; j++)
        next[j] += centred[i][j] * Xv[i];
    if (deflate) {
      // remove the deflate-direction component
      const c = dot(next, deflate);
      for (let j = 0; j < d; j++) next[j] -= c * deflate[j];
    }
    v = normalise(next);
  }
  return v;
}

export function pca2d(rows) {
  if (!rows || rows.length === 0) return [];
  if (rows.length === 1) return [[0, 0]];
  const mean = meanVec(rows);
  const centred = rows.map(r => r.map((x, i) => x - mean[i]));
  const v1 = powerIter(centred, null);
  const v2 = powerIter(centred, v1);
  return centred.map(r => [dot(r, v1), dot(r, v2)]);
}
