// Co-occurrence — sensors as nodes, edges between sensors that fire alerts
// within ±5 minutes of each other in the active window.
//
// Public:  renderCooccurrence(rootEl, { storylineData, edgePFloor,
//                                        sensorFilter, onSensorClick })

import { primaryChannel, CHANNEL_LABEL } from './sensor_map.mjs';

const COFIRE_WINDOW_MS = 5 * 60 * 1000;

function buildCounts(storylineData) {
  const sensorCounts = new Map();    // ch -> alert count
  const pairCounts   = new Map();    // 'a|b' -> count of co-fire occurrences
  const incidents = (storylineData && storylineData.incidents) || [];

  for (const inc of incidents) {
    const taggedAlerts = inc.alerts
      .map(a => ({ ts: Date.parse(a.created_at), ch: primaryChannel(a.event_type) }))
      .filter(a => Number.isFinite(a.ts) && a.ch);
    for (const a of taggedAlerts) sensorCounts.set(a.ch, (sensorCounts.get(a.ch) || 0) + 1);
    for (let i = 0; i < taggedAlerts.length; i++) {
      for (let j = i + 1; j < taggedAlerts.length; j++) {
        const a = taggedAlerts[i], b = taggedAlerts[j];
        if (Math.abs(a.ts - b.ts) > COFIRE_WINDOW_MS) continue;
        if (a.ch === b.ch) continue;
        const key = a.ch < b.ch ? `${a.ch}|${b.ch}` : `${b.ch}|${a.ch}`;
        pairCounts.set(key, (pairCounts.get(key) || 0) + 1);
      }
    }
  }
  return { sensorCounts, pairCounts };
}

export function renderCooccurrence(rootEl, opts) {
  const { storylineData, edgePFloor = 0.20, sensorFilter, onSensorClick } = opts || {};
  if (!rootEl || typeof cytoscape === 'undefined') return;

  const { sensorCounts, pairCounts } = buildCounts(storylineData);
  if (sensorCounts.size === 0) {
    rootEl.innerHTML = '<div class="inc-section-empty">No primary alerts in this window.</div>';
    return;
  }

  // Convert pair counts to a normalised P (count / max(count)) so the slider
  // shares semantics with Storyline's edge probabilities.
  const maxPair = Math.max(1, ...pairCounts.values());

  const elements = [];
  for (const [ch, count] of sensorCounts.entries()) {
    elements.push({
      data: {
        id: `co-${ch}`, label: CHANNEL_LABEL[ch] || ch, count, ch,
      },
      classes: sensorFilter === ch ? 'co-node selected' : 'co-node',
    });
  }
  for (const [key, count] of pairCounts.entries()) {
    const [a, b] = key.split('|');
    const p = count / maxPair;
    if (p < 0.01) continue;
    elements.push({
      data: { id: `coe-${key}`, source: `co-${a}`, target: `co-${b}`, p, count },
      classes: p < edgePFloor ? 'co-edge weak' : 'co-edge',
    });
  }

  rootEl.innerHTML = '';
  const container = document.createElement('div');
  container.style.width = '100%'; container.style.height = '100%';
  rootEl.appendChild(container);

  const cy = cytoscape({
    container,
    elements,
    layout: { name: 'cose', fit: true, padding: 24, animate: false },
    style: [
      { selector: 'node.co-node', style: {
        'background-color': '#1a3a66', 'border-width': 2, 'border-color': '#4dacff',
        'label': 'data(label)', 'color': '#cfe8ff', 'font-size': 9,
        'font-weight': 700, 'text-valign': 'center', 'text-halign': 'center',
        'width': ele => 14 + Math.min(20, (ele.data('count') || 1) * 2),
        'height': ele => 14 + Math.min(20, (ele.data('count') || 1) * 2),
      }},
      { selector: 'node.co-node.selected', style: {
        'border-width': 3, 'border-color': '#ffd23f',
      }},
      { selector: 'edge.co-edge', style: {
        'line-color': '#4dacff', 'curve-style': 'bezier',
        'width': ele => 0.8 + Math.min(4, (ele.data('count') || 1) * 0.5),
        'opacity': ele => Math.max(0.25, ele.data('p') || 0.5),
      }},
      { selector: 'edge.co-edge.weak', style: {
        'line-color': '#9aa5bd', 'line-style': 'dashed', 'opacity': 0.25,
      }},
    ],
    userZoomingEnabled: false,
    userPanningEnabled: false,
    boxSelectionEnabled: false,
  });

  cy.on('tap', 'node.co-node', evt => {
    const ch = evt.target.data('ch');
    if (onSensorClick) onSensorClick(ch);
  });
}
