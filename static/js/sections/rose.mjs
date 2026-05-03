// Rose — daily rhythm polar bar chart.
//
// Public:  renderRose(rootEl, { hour_histogram, severity_by_hour, selectedHour, onSelect })
//
// hour_histogram: 24-int array of incident counts per hour-of-day (UTC).
// severity_by_hour: 24-int array of severity ranks (-1, 0, 1, 2). Source of truth
//   for wedge colour. -1 means "no incidents that hour".
// selectedHour: int 0-23 or null. When set, that wedge gets a blue stroke.
// onSelect: callback (hour) => void.

const SEV_COLOR_BY_RANK = ['#2dccff', '#fc8c2f', '#ff3838']; // 0,1,2
const RANK_TO_OPACITY = [0.45, 0.7, 0.9];

export function renderRose(rootEl, { hour_histogram, severity_by_hour, selectedHour, onSelect }) {
  if (!rootEl) return;
  const W = rootEl.clientWidth || 200;
  const H = rootEl.clientHeight || 200;
  const cx = W / 2, cy = H / 2;
  const innerR = Math.min(W, H) * 0.16;
  const outerR = Math.min(W, H) * 0.42;

  const counts = Array.isArray(hour_histogram) ? hour_histogram : new Array(24).fill(0);
  const sevs   = Array.isArray(severity_by_hour) ? severity_by_hour : new Array(24).fill(-1);
  const maxCount = Math.max(1, ...counts);

  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.style.display = 'block';
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', '100%');

  // Axis circles.
  for (const rFrac of [0.5, 1.0]) {
    const c = document.createElementNS(svgNS, 'circle');
    c.setAttribute('cx', cx); c.setAttribute('cy', cy);
    c.setAttribute('r', innerR + (outerR - innerR) * rFrac);
    c.setAttribute('fill', 'none'); c.setAttribute('stroke', '#2a3346'); c.setAttribute('stroke-width', 0.5);
    svg.appendChild(c);
  }

  for (let h = 0; h < 24; h++) {
    const a0 = (h / 24) * Math.PI * 2 - Math.PI / 2;
    const a1 = ((h + 1) / 24) * Math.PI * 2 - Math.PI / 2;
    const r = innerR + (outerR - innerR) * (counts[h] / maxCount);
    const sevRank = sevs[h];
    const fill = sevRank >= 0 ? SEV_COLOR_BY_RANK[sevRank] : '#1a2540';
    const opacity = sevRank >= 0 ? RANK_TO_OPACITY[sevRank] : 0.25;

    const x0i = cx + innerR * Math.cos(a0), y0i = cy + innerR * Math.sin(a0);
    const x1i = cx + innerR * Math.cos(a1), y1i = cy + innerR * Math.sin(a1);
    const x0o = cx + r       * Math.cos(a0), y0o = cy + r       * Math.sin(a0);
    const x1o = cx + r       * Math.cos(a1), y1o = cy + r       * Math.sin(a1);
    const path = document.createElementNS(svgNS, 'path');
    path.setAttribute('d',
      `M ${x0i} ${y0i} L ${x0o} ${y0o} A ${r} ${r} 0 0 1 ${x1o} ${y1o} L ${x1i} ${y1i} Z`);
    path.setAttribute('fill', fill);
    path.setAttribute('opacity', selectedHour === h ? 1.0 : opacity);
    path.setAttribute('stroke', selectedHour === h ? '#4dacff' : 'none');
    path.setAttribute('stroke-width', selectedHour === h ? 1.5 : 0);
    path.style.cursor = 'pointer';
    path.dataset.hour = String(h);
    path.addEventListener('click', () => onSelect && onSelect(h));
    const tt = document.createElementNS(svgNS, 'title');
    tt.textContent = `${String(h).padStart(2,'0')}:00 UTC · ${counts[h]} incident${counts[h] === 1 ? '' : 's'}`;
    path.appendChild(tt);
    svg.appendChild(path);
  }

  // Hour labels at compass points.
  const labels = [['00', cx, cy - outerR - 4], ['06', cx + outerR + 8, cy], ['12', cx, cy + outerR + 12], ['18', cx - outerR - 8, cy]];
  for (const [t, x, y] of labels) {
    const tx = document.createElementNS(svgNS, 'text');
    tx.textContent = t;
    tx.setAttribute('x', x); tx.setAttribute('y', y);
    tx.setAttribute('font-size', '7'); tx.setAttribute('fill', '#7a8497');
    tx.setAttribute('text-anchor', 'middle'); tx.setAttribute('font-family', 'monospace');
    svg.appendChild(tx);
  }

  rootEl.innerHTML = '';
  rootEl.appendChild(svg);
}
