// Galaxy — incident-similarity 2-D scatter via PCA over signature vectors.
//
// Public:  renderGalaxy(rootEl, { incidents, selectedId, onSelect })
// onSelect(incidentId): callback when a dot is clicked.

import { pca2d } from '../pca.mjs';

const SEV_COLOR = { critical: '#ff3838', warning: '#fc8c2f', info: '#2dccff' };
const _SEV_RANK = { info: 0, warning: 1, critical: 2 };

function parseSignature(s) {
  if (Array.isArray(s)) return s;
  if (typeof s !== 'string' || !s) return null;
  try { const v = JSON.parse(s); return Array.isArray(v) ? v : null; }
  catch (_) { return null; }
}

export function renderGalaxy(rootEl, { incidents, selectedId, onSelect }) {
  if (!rootEl) return;
  const W = rootEl.clientWidth || 400;
  const H = rootEl.clientHeight || 200;
  const margin = 18;

  const valid = (incidents || [])
    .map(i => ({ inc: i, sig: parseSignature(i.signature) }))
    .filter(o => o.sig && o.sig.length > 0);

  if (valid.length < 2) {
    rootEl.innerHTML = `<div class="inc-section-empty">
      Need at least 2 incidents with signatures to compute similarity.
      Try a wider window.
    </div>`;
    return;
  }

  // Detect "all signatures near zero" → fall back to (index, severity) layout.
  const allZero = valid.every(o => o.sig.every(x => Math.abs(x) < 1e-6));
  let coords;
  if (allZero) {
    coords = valid.map((o, i) => [i, _SEV_RANK[o.inc.max_severity] || 0]);
  } else {
    coords = pca2d(valid.map(o => o.sig));
  }

  const xs = coords.map(p => p[0]);
  const ys = coords.map(p => p[1]);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const yMin = Math.min(...ys), yMax = Math.max(...ys);
  const xRange = xMax - xMin || 1;
  const yRange = yMax - yMin || 1;

  const scaled = coords.map(([x, y]) => ({
    x: margin + ((x - xMin) / xRange) * (W - 2 * margin),
    y: margin + ((y - yMin) / yRange) * (H - 2 * margin),
  }));

  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.style.display = 'block';
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', '100%');

  valid.forEach((o, i) => {
    const c = document.createElementNS(svgNS, 'circle');
    const isSel = o.inc.id === selectedId;
    const r = 4 + Math.min(8, (o.inc.alert_count || 1));
    c.setAttribute('cx', scaled[i].x);
    c.setAttribute('cy', scaled[i].y);
    c.setAttribute('r', r);
    c.setAttribute('fill', SEV_COLOR[o.inc.max_severity] || '#2dccff');
    c.setAttribute('opacity', isSel ? 0.95 : 0.6);
    c.setAttribute('stroke', isSel ? '#4dacff' : 'none');
    c.setAttribute('stroke-width', isSel ? 2.5 : 0);
    c.style.cursor = 'pointer';
    c.dataset.incidentId = o.inc.id;
    c.addEventListener('click', () => onSelect && onSelect(o.inc.id));
    // Native title for hover; the overall section title gets a rux-tooltip.
    const t = document.createElementNS(svgNS, 'title');
    t.textContent = `${o.inc.id} · ${o.inc.max_severity || 'info'} · ${o.inc.alert_count || 0} alerts`;
    c.appendChild(t);
    svg.appendChild(c);
  });

  rootEl.innerHTML = '';
  rootEl.appendChild(svg);
}
