// Storyline — subway map of alerts on (sensor lane × time) axes.
//
// Public:  renderStoryline(rootEl, { storylineData, windowStart, windowEnd,
//                                     selectedId, edgePFloor, sensorFilter,
//                                     onSelect })
//
// storylineData: { incidents: [{ id, started_at, max_severity, alerts, edges }] }
//   from /api/incidents/storyline.
// windowStart / windowEnd: Date or ISO string. Defines the x-axis range.
// selectedId: string or null. The selected incident's curve renders solid blue.
// edgePFloor: 0..1. Curves whose minimum edge probability falls below this
//   are rendered as faded dotted ghosts (they still appear, just recede).
// sensorFilter: channel id or null. When set, only incidents touching that
//   channel render; other curves are dropped from the canvas.
// onSelect: callback (incidentId) => void.

import { ALL_CHANNELS, CHANNEL_LABEL, primaryChannel } from './sensor_map.mjs';

const SEV_COLOR = { critical: '#ff3838', warning: '#fc8c2f', info: '#2dccff' };

export function renderStoryline(rootEl, opts) {
  const { storylineData, windowStart, windowEnd, selectedId,
          edgePFloor = 0.20, sensorFilter = null, onSelect } = opts || {};
  if (!rootEl) return;
  const W = rootEl.clientWidth || 600;
  const H = rootEl.clientHeight || 240;
  const leftPad = 50, rightPad = 12, topPad = 14, bottomPad = 22;
  const laneCount = ALL_CHANNELS.length;
  const laneH = (H - topPad - bottomPad) / laneCount;
  const laneY = ch => topPad + (ALL_CHANNELS.indexOf(ch) + 0.5) * laneH;

  const incidents = (storylineData && storylineData.incidents) || [];
  if (incidents.length === 0) {
    rootEl.innerHTML = '<div class="inc-section-empty">No primary alerts in this window.</div>';
    return;
  }

  const tStart = windowStart instanceof Date ? windowStart.getTime() : Date.parse(windowStart);
  const tEnd   = windowEnd   instanceof Date ? windowEnd.getTime()   : Date.parse(windowEnd);
  const span   = Math.max(1, tEnd - tStart);
  const xOf = ts => leftPad + ((ts - tStart) / span) * (W - leftPad - rightPad);

  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.style.display = 'block';
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', '100%');

  // Lane backgrounds + labels.
  ALL_CHANNELS.forEach(ch => {
    const y = laneY(ch);
    const line = document.createElementNS(svgNS, 'line');
    line.setAttribute('x1', leftPad); line.setAttribute('x2', W - rightPad);
    line.setAttribute('y1', y); line.setAttribute('y2', y);
    line.setAttribute('stroke', '#3a4558'); line.setAttribute('stroke-width', '0.5'); line.setAttribute('opacity', '0.6');
    svg.appendChild(line);
    const txt = document.createElementNS(svgNS, 'text');
    txt.textContent = CHANNEL_LABEL[ch] || ch;
    txt.setAttribute('x', leftPad - 4); txt.setAttribute('y', y + 3);
    txt.setAttribute('font-size', '8'); txt.setAttribute('text-anchor', 'end');
    txt.setAttribute('fill', sensorFilter === ch ? '#4dacff' : '#9aa5bd');
    txt.setAttribute('font-family', 'monospace');
    svg.appendChild(txt);
  });

  // For each incident: place dots, draw connecting curve.
  for (const inc of incidents) {
    const isSel = inc.id === selectedId;
    const points = [];
    for (const a of inc.alerts) {
      const ch = primaryChannel(a.event_type);
      if (!ch || !ALL_CHANNELS.includes(ch)) continue;
      const ts = Date.parse(a.created_at);
      if (!Number.isFinite(ts)) continue;
      const x = xOf(ts);
      const y = laneY(ch);
      points.push({ x, y, sev: a.severity, ch, eventType: a.event_type, alertId: a.id, ts });
    }

    if (sensorFilter && !points.some(p => p.ch === sensorFilter)) continue;

    // Curve through points using quadratic-smoothed midpoints.
    if (points.length > 1) {
      const path = document.createElementNS(svgNS, 'path');
      let d = `M ${points[0].x} ${points[0].y}`;
      for (let i = 1; i < points.length; i++) {
        const p = points[i], q = points[i - 1];
        const mx = (p.x + q.x) / 2, my = (p.y + q.y) / 2;
        d += ` Q ${q.x} ${q.y} ${mx} ${my} T ${p.x} ${p.y}`;
      }
      path.setAttribute('d', d); path.setAttribute('fill', 'none');
      const minP = inc.edges && inc.edges.length
        ? Math.min(...inc.edges.map(e => e.p)) : 1.0;
      const isWeak = minP < edgePFloor;
      path.setAttribute('stroke', isSel ? '#4dacff' : '#9aa5bd');
      path.setAttribute('stroke-width', isSel ? 2.5 : 1.2);
      path.setAttribute('stroke-dasharray', (isSel || !isWeak) ? '' : '3 2');
      path.setAttribute('opacity', isSel ? 0.95 : (isWeak ? 0.35 : 0.55));
      path.style.cursor = 'pointer';
      path.addEventListener('click', () => onSelect && onSelect(inc.id));
      const tt = document.createElementNS(svgNS, 'title');
      tt.textContent = `${inc.id} · ${inc.max_severity || 'info'} · ${points.length} alerts`;
      path.appendChild(tt);
      svg.appendChild(path);
    }

    for (const p of points) {
      const c = document.createElementNS(svgNS, 'circle');
      c.setAttribute('cx', p.x); c.setAttribute('cy', p.y);
      c.setAttribute('r', isSel ? 5 : 3.5);
      c.setAttribute('fill', SEV_COLOR[p.sev] || '#2dccff');
      c.setAttribute('opacity', isSel ? 1.0 : 0.7);
      c.setAttribute('stroke', isSel ? '#4dacff' : 'none');
      c.setAttribute('stroke-width', isSel ? 1.5 : 0);
      c.style.cursor = 'pointer';
      c.addEventListener('click', () => onSelect && onSelect(inc.id));
      const tt = document.createElementNS(svgNS, 'title');
      tt.textContent = `${p.eventType} · ${p.sev} · ${new Date(p.ts).toISOString().slice(11, 19)}Z`;
      c.appendChild(tt);
      svg.appendChild(c);
    }
  }

  // Time axis ticks at 0 / 50 / 100% of the window.
  const tickFmt = ms => {
    const d = new Date(ms);
    const day = String(d.getUTCDate()).padStart(2, '0');
    return `${day} ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;
  };
  for (const frac of [0, 0.5, 1]) {
    const ts = tStart + frac * span;
    const t = document.createElementNS(svgNS, 'text');
    t.textContent = tickFmt(ts);
    t.setAttribute('x', leftPad + frac * (W - leftPad - rightPad));
    t.setAttribute('y', H - 6);
    t.setAttribute('font-size', '7'); t.setAttribute('fill', '#9aa5bd');
    t.setAttribute('text-anchor', frac === 1 ? 'end' : (frac === 0 ? 'start' : 'middle'));
    svg.appendChild(t);
  }

  rootEl.innerHTML = '';
  rootEl.appendChild(svg);
}
