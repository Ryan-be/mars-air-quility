/**
 * incident_graph.js — Incident Correlation Graph
 *
 * Responsibilities:
 *  - Toolbar (window, severity, search) → fetch + filter incidents
 *  - Left panel: render incident cards, handle selection
 *  - Centre: Cytoscape.js hub-and-spoke graph
 *  - Right panel: narrative, causal ribbon (rux-tag), similar incidents, node overlay
 *
 * Dependencies: Cytoscape.js v3 loaded globally via CDN before this module.
 */

// ── State ─────────────────────────────────────────────────────────────────────

let cy = null;                  // Cytoscape instance
let currentIncidentId = null;   // selected incident ID
let allIncidents = [];          // full list from /api/incidents
let currentDetail = null;       // detail response for selected incident
let allIncidentDetails = {};    // incidentId → detail object (persistent cache)

// ── DOM refs ─────────────────────────────────────────────────────────────────

const elSearch      = document.getElementById('inc-search');
const elWindow      = document.getElementById('inc-window');
const elSeverity    = document.getElementById('inc-severity');
const elList        = document.getElementById('inc-list-items');
const elEmpty       = document.querySelector('.inc-detail-empty');
const elNarrative   = document.getElementById('inc-narrative');
const elNarrObs     = document.getElementById('inc-narrative-observed');
const elNarrInf     = document.getElementById('inc-narrative-inferred');
const elNarrImp     = document.getElementById('inc-narrative-impact');
const elCausal      = document.getElementById('inc-causal');
const elCausalItems = document.getElementById('inc-causal-items');
const elSimilar     = document.getElementById('inc-similar');
const elSimilarItems = document.getElementById('inc-similar-items');
const elNodeOverlay = document.getElementById('inc-node-overlay');
const elNodeTitle   = document.getElementById('inc-node-title');
const elNodeClose   = document.getElementById('inc-node-close');
const elNodeBody    = document.getElementById('inc-node-body');

// ── Toolbar state ─────────────────────────────────────────────────────────────

let activeWindow   = '24h';
let activeSeverity = 'all';
let searchQuery    = '';
let searchTimer    = null;

// ── Bootstrap ─────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  initToolbar();
  initViewControls();
  loadIncidents();
  if (elNodeClose) {
    elNodeClose.addEventListener('click', () => {
      if (elNodeOverlay) elNodeOverlay.hidden = true;
    });
  }
});

// ── Toolbar ───────────────────────────────────────────────────────────────────

function initToolbar() {
  elWindow.addEventListener('ruxchange', e => {
    activeWindow = (e.detail || '24h').toLowerCase();
    loadIncidents();
  });

  elSeverity.addEventListener('ruxchange', e => {
    const val = (e.detail || 'All').toLowerCase();
    activeSeverity = val === 'all' ? 'all' : val;
    renderList(applyClientFilter(allIncidents));
  });

  elSearch.addEventListener('ruxinput', e => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      searchQuery = (e.target.value || '').toLowerCase().trim();
      renderList(applyClientFilter(allIncidents));
    }, 300);
  });
}

function initViewControls() {
  document.getElementById('ctrl-fit-all').addEventListener('click', () => {
    if (cy) cy.fit(cy.elements(), 40);
  });

  document.getElementById('ctrl-fit-sel').addEventListener('click', () => {
    if (!cy || !currentIncidentId) return;
    fitToSelected(currentIncidentId);
  });

  document.getElementById('ctrl-reset-pos').addEventListener('click', () => {
    if (!currentIncidentId || !currentDetail) return;
    // Clear all saved positions for every incident
    const toRemove = Object.keys(localStorage).filter(k =>
      allIncidents.some(i => k.startsWith(i.id + '::'))
    );
    toRemove.forEach(k => localStorage.removeItem(k));
    renderGraph(currentDetail, allIncidents);
  });

  // Layout buttons
  let activeLayout = 'preset';
  document.querySelectorAll('.inc-layout-btn').forEach(btn => {
    if (btn.dataset.layout === activeLayout) btn.classList.add('active');
    btn.addEventListener('click', () => {
      const name = btn.dataset.layout;
      activeLayout = name;
      document.querySelectorAll('.inc-layout-btn').forEach(b => b.classList.toggle('active', b.dataset.layout === name));
      if (!cy || name === 'preset') return;
      const layoutOpts = {
        cose:          { name: 'cose', animate: true, animationDuration: 500, fit: false, padding: 40 },
        breadthfirst:  { name: 'breadthfirst', animate: true, animationDuration: 500, fit: false, padding: 40, directed: false },
        circle:        { name: 'circle', animate: true, animationDuration: 500, fit: false, padding: 40 },
      };
      cy.layout(layoutOpts[name] || { name }).run();
    });
  });
}

function applyClientFilter(incidents) {
  return incidents.filter(inc => {
    if (activeSeverity !== 'all' && inc.max_severity !== activeSeverity) return false;
    if (searchQuery) {
      const haystack = (inc.id + ' ' + (inc.title || '')).toLowerCase();
      if (!haystack.includes(searchQuery)) return false;
    }
    return true;
  });
}

// ── Fetch incident list ───────────────────────────────────────────────────────

// Fallback windows tried in order when the current window returns 0 results.
// On first page load with no recent activity, this auto-widens so the graph
// is never blank just because inferences are older than 24 h.
const _FALLBACK_WINDOWS = ['7d', '30d'];

async function loadIncidents() {
  if (elList) elList.innerHTML = '<div class="inc-loading">Loading…</div>';

  const windows = [activeWindow, ..._FALLBACK_WINDOWS.filter(w => w !== activeWindow)];

  for (const win of windows) {
    try {
      const params = new URLSearchParams({ window: win, limit: 100 });
      const resp = await fetch('/api/incidents?' + params);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      allIncidents = data.incidents || [];

      const counts = data.counts || { critical: 0, warning: 0, info: 0 };
      const set = (id, n) => { const el = document.getElementById(id); if (el) el.textContent = n; };
      set('pill-critical-count', counts.critical || 0);
      set('pill-warning-count',  counts.warning  || 0);
      set('pill-info-count',     counts.info     || 0);

      if (allIncidents.length > 0 || win === windows[windows.length - 1]) {
        // Found results, or exhausted all fallback windows
        if (win !== activeWindow) {
          // Silently update the toolbar to reflect the wider window used
          activeWindow = win;
          _syncWindowButton(win);
        }
        renderList(applyClientFilter(allIncidents));
        if (allIncidents.length > 0 && !currentIncidentId) {
          selectIncident(allIncidents[0].id);
        }
        return;
      }
      // Zero results — try the next wider window
    } catch (err) {
      if (elList) elList.innerHTML = `<div class="inc-loading">Error: ${err.message}</div>`;
      return;
    }
  }
}

/** Update the rux-segmented-button to reflect the auto-widened window. */
function _syncWindowButton(win) {
  if (!elWindow) return;
  try {
    const labels = ['1h', '6h', '24h', '7d', '30d'];
    const updated = labels.map(l => ({ label: l, selected: l === win }));
    elWindow.data = JSON.stringify(updated);
  } catch (_) {}
}

// ── Render incident list ──────────────────────────────────────────────────────

function renderList(incidents) {
  if (!elList) return;
  if (incidents.length === 0) {
    elList.innerHTML = '<div class="inc-loading">No incidents found.</div>';
    return;
  }

  elList.innerHTML = incidents.map(inc => {
    const start = (inc.started_at || '').replace('T', ' ').slice(11, 16);
    const end   = (inc.ended_at   || '').replace('T', ' ').slice(11, 16);
    const date  = (inc.started_at || '').slice(0, 10);
    const durMin = (() => {
      if (!inc.started_at || !inc.ended_at) return '';
      const a = new Date(inc.started_at.replace(' ', 'T'));
      const b = new Date(inc.ended_at.replace(' ', 'T'));
      const m = Math.round((b - a) / 60000);
      return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
    })();
    return `
      <div class="inc-card${inc.id === currentIncidentId ? ' selected' : ''}"
           data-id="${escHtml(inc.id)}">
        <div class="inc-card-id">${escHtml(inc.id)}</div>
        <div class="inc-card-title" title="${escHtml(inc.title || '')}">${escHtml(inc.title || '')}</div>
        <div class="inc-card-time">
          <span>${escHtml(date)}</span>
          <span>·</span>
          <span>${escHtml(start)}–${escHtml(end)}</span>
          <span>·</span>
          <span>${escHtml(durMin)}</span>
        </div>
        <div class="inc-card-meta">
          <span class="inc-sev-dot ${escHtml(inc.max_severity || 'info')}"></span>
          <span>${escHtml(inc.max_severity || 'info')}</span>
          <span>·</span>
          <span>${inc.alert_count ?? 0} alert${inc.alert_count === 1 ? '' : 's'}</span>
        </div>
      </div>
    `;
  }).join('');

  elList.querySelectorAll('.inc-card').forEach(card => {
    card.addEventListener('click', () => selectIncident(card.dataset.id));
  });
}

// ── Select incident ───────────────────────────────────────────────────────────

async function selectIncident(id) {
  currentIncidentId = id;

  elList && elList.querySelectorAll('.inc-card').forEach(c => {
    c.classList.toggle('selected', c.dataset.id === id);
  });

  try {
    const resp = await fetch(`/api/incidents/${encodeURIComponent(id)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    currentDetail = await resp.json();
    renderDetail(currentDetail);
    renderGraph(currentDetail, allIncidents);
  } catch (err) {
    console.error('Failed to load incident detail:', err);
  }
}

// ── Right panel: narrative, causal, similar ───────────────────────────────────

function renderDetail(detail) {
  if (elEmpty) elEmpty.hidden = true;

  if (detail.narrative && elNarrative) {
    if (elNarrObs) elNarrObs.textContent = detail.narrative.observed || '';
    if (elNarrInf) elNarrInf.textContent = detail.narrative.inferred || '';
    if (elNarrImp) elNarrImp.textContent = detail.narrative.impact || '';
    elNarrative.hidden = false;
  }

  const causal = detail.causal_sequence || [];
  if (causal.length > 0 && elCausal) {
    const startTs = causal[0] ? new Date(causal[0].created_at.replace(' ', 'T')) : null;
    const fmtDelta = (iso) => {
      if (!startTs) return '';
      const t = new Date(iso.replace(' ', 'T'));
      const mins = Math.round((t - startTs) / 60000);
      return mins === 0 ? 'start' : `+${mins}m`;
    };
    const fmtClock = (iso) => (iso || '').slice(11, 16);

    elCausalItems.innerHTML = '<div class="inc-causal-ribbon">'
      + causal.map((a, i) =>
          (i > 0 ? '<span class="inc-causal-arrow">→</span>' : '')
          + `<span class="inc-causal-chip-group" title="${escHtml(fmtClock(a.created_at))} — ${escHtml(a.title || a.event_type)}">`
          +   `<span class="inc-causal-chip sev-chip-${escHtml(a.severity || 'info')}">${escHtml(a.title || a.event_type)}</span>`
          +   `<span class="inc-causal-chip-time">${escHtml(fmtDelta(a.created_at))}</span>`
          + `</span>`
        ).join('')
      + '</div>';
    elCausal.hidden = false;
  } else if (elCausal) {
    elCausal.hidden = true;
  }

  const similar = detail.similar || [];
  if (similar.length > 0 && elSimilar) {
    elSimilarItems.innerHTML = similar.map(s => `
      <div class="inc-similar-item" data-similar-id="${escHtml(s.id)}">
        <div>
          <div style="font-size:0.75rem;font-weight:700;color:var(--text-muted)">${escHtml(s.id)}</div>
          <div style="font-size:0.8rem">${escHtml(s.title || '')}</div>
        </div>
        <div style="text-align:right">
          <div class="inc-similar-score">${(s.similarity * 100).toFixed(0)}% similar</div>
          <span class="inc-similar-nav">›</span>
        </div>
      </div>
    `).join('');

    elSimilarItems.querySelectorAll('.inc-similar-item').forEach(el => {
      el.addEventListener('click', () => selectIncident(el.dataset.similarId));
    });
    elSimilar.hidden = false;
  } else if (elSimilar) {
    elSimilar.hidden = true;
  }

  if (elNodeOverlay) elNodeOverlay.hidden = true;
}

// ── Node overlay ──────────────────────────────────────────────────────────────

async function showNodeOverlay(nodeData) {
  if (!elNodeOverlay) return;
  if (elNodeTitle) elNodeTitle.textContent = nodeData.title || nodeData.id;
  elNodeOverlay.hidden = false;

  if (!(nodeData.type === 'alert' && nodeData.alertId && currentDetail)) {
    if (elNodeBody) elNodeBody.innerHTML = '';
    return;
  }

  const alert = (currentDetail.alerts || []).find(a => a.id === nodeData.alertId);
  if (!alert) {
    if (elNodeBody) elNodeBody.textContent = 'Alert not found in current incident.';
    return;
  }
  if (elNodeBody) elNodeBody.innerHTML = renderAlertTable(alert);
}

function renderAlertTable(alert) {
  const pct = (x) => `${((x || 0) * 100).toFixed(0)}%`;
  const ts  = (s) => (s || '').replace('T', ' ').slice(0, 19);
  const rows = [
    ['ID',         `#${alert.id}`],
    ['Time',       escHtml(ts(alert.created_at))],
    ['Type',       escHtml(alert.event_type || '')],
    ['Severity',   escHtml(alert.severity || '')],
    ['Method',     escHtml(alert.detection_method || '')],
    ['Confidence', pct(alert.confidence)],
  ];
  if (alert.description) {
    rows.push(['Detail', escHtml(alert.description)]);
  }
  // Signal correlations (Pearson r per sensor) — only show |r| >= 0.3
  const strongDeps = (alert.signal_deps || [])
    .filter(d => d.r !== null && Math.abs(d.r) >= 0.3)
    .sort((a, b) => Math.abs(b.r) - Math.abs(a.r))
    .slice(0, 6);
  if (strongDeps.length) {
    const depsHtml = strongDeps.map(d => {
      const sign = d.r >= 0 ? '+' : '';
      const colour = d.r >= 0 ? '#4dacff' : '#ff8a8a';
      return `<div class="dep-row"><span>${escHtml(d.sensor)}</span><span style="color:${colour}">r = ${sign}${d.r.toFixed(2)}</span></div>`;
    }).join('');
    rows.push(['Correlates', `<div class="evidence-block">${depsHtml}</div>`]);
  }
  return '<table>' + rows.map(([k, v]) =>
    `<tr><td>${k}</td><td>${v}</td></tr>`
  ).join('') + '</table>';
}

// ── Cytoscape stylesheet ──────────────────────────────────────────────────────

const SEV_BORDER = { critical: '#ff3838', warning: '#fc8c2f', info: '#2dccff' };

function buildCytoscapeStyle() {
  return [
    // ── Base node ──────────────────────────────────────────────────────
    {
      selector: 'node',
      style: {
        'background-color': '#1a2540',
        'border-width': 1.5,
        'border-color': '#5a6a85',
        'label': '',
        'color': '#c9d1d9',
        'font-size': 10,
        'font-weight': 500,
        'font-family': 'Roboto, sans-serif',
        'text-valign': 'bottom',
        'text-margin-y': 4,
        'text-wrap': 'ellipsis',
        'text-max-width': 80,
        'text-outline-color': '#0d1117',
        'text-outline-width': 2,
        'text-outline-opacity': 0.8,
        'width': 20,
        'height': 20,
        'shape': 'ellipse',
      },
    },

    // ── Compound hull (selected incident) ─────────────────────────────
    // NOTE: Cytoscape.js strips alpha from rgba() colours — it ignores the
    // alpha channel and treats *-opacity as 1.  Use explicit *-opacity props.
    {
      selector: 'node.hull',
      style: {
        'background-color': '#2d64ff',
        'background-opacity': 0.08,
        'border-width': 1.5,
        'border-style': 'solid',
        'border-color': '#6a92e0',
        'border-opacity': 0.55,
        'shape': 'round-rectangle',
        'padding': '30px 40px 30px 40px',
        'label': 'data(label)',
        'font-size': 11,
        'font-weight': 700,
        'color': '#d0dbf0',
        'text-valign': 'top',
        'text-halign': 'center',
        'text-margin-y': -4,
        'text-outline-color': '#0d1117',
        'text-outline-width': 2,
        'text-outline-opacity': 0.8,
        'width': 'label',
        'height': 'label',
      },
    },

    // ── Ghost hull (unselected incidents — clickable to navigate) ─────
    {
      selector: 'node.hull.ghost',
      style: {
        'background-color': '#1a2540',
        'background-opacity': 0.45,
        'border-color': '#5a7eb8',
        'border-opacity': 0.5,
        'color': '#a8b5d0',
        'cursor': 'pointer',
      },
    },

    // Ghost alert/root nodes (unselected incidents)
    { selector: 'node.ghost:not(.hull)', style: { 'cursor': 'pointer' } },

    // ── Node size / shape variants ────────────────────────────────────
    { selector: 'node.alert-node',  style: { 'width': 20, 'height': 20, 'border-width': 1.5 } },
    {
      selector: 'node.cross-node',
      style: {
        'width': 16,
        'height': 16,
        'border-style': 'dashed',
        'border-color': '#64b482',
        'border-opacity': 0.6,
        'background-color': '#101c14',
      },
    },

    // ── Severity → border colour (AstroUXDS status palette) ──────────
    { selector: 'node.severity-critical', style: { 'border-color': SEV_BORDER.critical } },
    { selector: 'node.severity-warning',  style: { 'border-color': SEV_BORDER.warning } },
    { selector: 'node.severity-info',     style: { 'border-color': SEV_BORDER.info } },

    // ── Detection method → node shape ─────────────────────────────────
    { selector: 'node.method-threshold',   style: { 'shape': 'ellipse' } },
    { selector: 'node.method-ml',          style: { 'shape': 'diamond' } },
    { selector: 'node.method-statistical', style: { 'shape': 'hexagon' } },
    { selector: 'node.method-fingerprint', style: { 'shape': 'pentagon' } },
    { selector: 'node.method-summary',     style: { 'shape': 'round-rectangle' } },

    // ── Progressive labels ────────────────────────────────────────────
    { selector: 'node.alert-node.labels-ts',    style: { 'label': 'data(created_at)' } },
    { selector: 'node.alert-node.labels-full',  style: { 'label': 'data(label)' } },

    // ── Edges ─────────────────────────────────────────────────────────
    { selector: 'edge',            style: { 'width': 1.2, 'line-color': '#4a5e85', 'curve-style': 'bezier', 'opacity': 0.75 } },
    {
      selector: 'edge.chrono-edge',
      style: {
        'width': 1.6,
        'line-color': '#4dacff',
        'opacity': 0.8,
        'curve-style': 'bezier',
        'target-arrow-shape': 'triangle',
        'target-arrow-color': '#4dacff',
        'arrow-scale': 0.9,
      },
    },
    { selector: 'edge.cross-edge', style: { 'line-color': '#7ec090', 'line-style': 'dashed', 'width': 1, 'opacity': 0.65 } },

    // ── Selection highlight ───────────────────────────────────────────
    // Hulls get no fill change when selected (avoid opaque box covering children);
    // only the border glows brighter.
    { selector: 'node.hull:selected', style: { 'border-width': 2, 'border-color': '#4dacff', 'border-opacity': 0.9, 'background-opacity': 0.08 } },
    { selector: 'node:selected:not(.hull)', style: { 'border-width': 2.5, 'border-color': '#4dacff', 'background-color': '#0f2040' } },
  ];
}

// ── Progressive zoom ──────────────────────────────────────────────────────────

function applyZoomClasses(zoom) {
  if (!cy) return;
  cy.nodes('.alert-node').forEach(n => {
    if (zoom < 0.9) {
      n.removeClass('labels-ts labels-full');
    } else if (zoom < 1.6) {
      n.addClass('labels-ts');
      n.removeClass('labels-full');
    } else {
      n.addClass('labels-full');
      n.removeClass('labels-ts');
    }
  });
}

// ── Cytoscape init ────────────────────────────────────────────────────────────

function initCytoscape() {
  if (typeof cytoscape === 'undefined') {
    console.error('Cytoscape.js not loaded — graph unavailable. Check CDN connectivity.');
    return;
  }
  if (cy) { cy.destroy(); cy = null; }

  cy = cytoscape({
    container: document.getElementById('cy-graph'),
    userZoomingEnabled: true,
    userPanningEnabled: true,
    boxSelectionEnabled: false,
    minZoom: 0.15,
    maxZoom: 6,
    style: buildCytoscapeStyle(),
    elements: [],
    layout: { name: 'preset' },
  });

  cy.on('zoom', () => applyZoomClasses(cy.zoom()));

  cy.on('tap', 'node[type="alert"]:not(.ghost)', evt => {
    showNodeOverlay(evt.target.data());
  });

  cy.on('tap', 'node.ghost', evt => {
    const incId = evt.target.data('incidentId');
    if (incId) selectIncident(incId);
  });

  cy.on('dragfree', 'node', evt => {
    const node = evt.target;
    const pos = node.position();
    const key = `${currentIncidentId}::${node.id()}`;
    try { localStorage.setItem(key, JSON.stringify(pos)); } catch (_) {}
  });
}

// ── Graph element builder ─────────────────────────────────────────────────────

/** Build a short one-line summary ("INC-...-0928 · 7 alerts · CO₂") used as
 *  the ghost hull label. Falls back gracefully if alerts are missing. */
function ghostSummaryLabel(incId, alerts, alertCount) {
  const count = (alerts && alerts.length) || alertCount || 0;
  const primary = (alerts || []).filter(a => a.is_primary);
  const topEvent = primary[0]
    ? (primary[0].title || primary[0].event_type || '').split(' ').slice(0, 3).join(' ')
    : '';
  const parts = [incId];
  if (count) parts.push(`${count} alert${count === 1 ? '' : 's'}`);
  if (topEvent) parts.push(topEvent);
  return parts.join(' · ');
}

function fitToSelected(incidentId) {
  if (!cy) return;
  const selected = incidentId ? cy.$(`[incidentId="${incidentId}"]`) : null;
  if (selected && selected.length > 0) {
    cy.fit(selected, 60);
  } else {
    cy.fit(cy.elements(), 40);
  }
}

async function renderGraph(detail, incidents) {
  initCytoscape();
  if (!cy) return;

  cy.elements().remove();
  const centroids = buildCentroids(incidents);

  // Render selected incident immediately from already-fetched detail
  if (detail) {
    allIncidentDetails[detail.id] = detail;
    cy.add(buildIncidentElements(detail, centroids, false));
  }

  // Render ghosts: use cached detail if available, else placeholder hull
  const ghostIncs = incidents.filter(i => i.id !== (detail && detail.id));
  ghostIncs.forEach(inc => {
    const cached = allIncidentDetails[inc.id];
    if (cached) {
      cy.add(buildIncidentElements(cached, centroids, true));
    } else {
      // Placeholder hull only until fetch completes
      cy.add([{
        group: 'nodes',
        data: {
          id: `hull-${inc.id}`,
          label: ghostSummaryLabel(inc.id, null, inc.alert_count),
          type: 'hull',
          incidentId: inc.id,
        },
        position: centroids[inc.id] || { x: 0, y: 0 },
        classes: `hull ghost severity-${inc.max_severity || 'info'}`,
      }]);
    }
  });

  restorePositions();
  fitToSelected(detail && detail.id);
  applyZoomClasses(cy.zoom());
  applySelectionOpacity(detail && detail.id);

  // Progressively fetch ghost details and expand in background
  ghostIncs.forEach(async inc => {
    if (allIncidentDetails[inc.id]) return; // already cached
    const d = await fetchIncidentDetail(inc.id);
    if (!d || !cy) return;
    // Replace placeholder hull with full nodes
    cy.$(`[incidentId="${inc.id}"]`).remove();
    cy.add(buildIncidentElements(d, centroids, true));
    restorePositions();
    applySelectionOpacity(currentIncidentId);
    applyZoomClasses(cy.zoom());
  });
}

// ── Centroid placement ────────────────────────────────────────────────────────

function buildCentroids(incidents) {
  const GRID_SPACING = 400;
  const cols = Math.ceil(Math.sqrt(Math.max(incidents.length, 1)));
  const centroids = {};
  incidents.forEach((inc, i) => {
    centroids[inc.id] = {
      x: (i % cols) * GRID_SPACING,
      y: Math.floor(i / cols) * GRID_SPACING,
    };
  });
  return centroids;
}

// ── Build elements for the selected incident ──────────────────────────────────

function buildIncidentElements(detail, centroids, isGhost = false) {
  const elements = [];
  const incId = detail.id;
  const centre = centroids[incId] || { x: 0, y: 0 };

  // Compound hull
  const hullLabel = isGhost
    ? ghostSummaryLabel(incId, detail.alerts || [], (detail.alerts || []).length)
    : incId;
  elements.push({
    group: 'nodes',
    data: { id: `hull-${incId}`, label: hullLabel, type: 'hull', incidentId: incId },
    classes: `hull${isGhost ? ' ghost' : ''} severity-${detail.max_severity || 'info'}`,
  });

  const primaryAlerts = (detail.alerts || []).filter(a => a.is_primary);
  const crossAlerts   = (detail.alerts || []).filter(a => !a.is_primary);
  const rootCount = Math.max(primaryAlerts.length, 1);

  // ── Timeline layout: x = minutes from incident start, y = severity lane ──
  const TIMELINE_WIDTH_PX = 360;   // px allocated to the time axis per cluster
  const LANE_HEIGHT_PX    = 40;
  const LANE_BY_SEVERITY  = { critical: 0, warning: 1, info: 2 };

  const startMs = new Date((detail.started_at || '').replace(' ', 'T')).getTime();
  const endMs   = new Date((detail.ended_at   || '').replace(' ', 'T')).getTime();
  const spanMs  = Math.max(endMs - startMs, 60_000);  // min 1 min to avoid /0

  primaryAlerts.forEach((alert) => {
    const alertMs = new Date((alert.created_at || '').replace(' ', 'T')).getTime();
    const t = Math.max(0, Math.min(1, (alertMs - startMs) / spanMs));
    const lane = LANE_BY_SEVERITY[alert.severity] ?? 2;
    const alertPos = {
      x: centre.x - TIMELINE_WIDTH_PX / 2 + t * TIMELINE_WIDTH_PX,
      y: centre.y - LANE_HEIGHT_PX + lane * LANE_HEIGHT_PX,
    };
    elements.push({
      group: 'nodes',
      data: {
        id: `alert-${alert.id}`,
        label: alert.title || alert.event_type || '',
        type: 'alert',
        alertId: alert.id,
        incidentId: incId,
        parent: `hull-${incId}`,
        severity: alert.severity,
        method: alert.detection_method,
        title: alert.title || '',
        created_at: (alert.created_at || '').slice(0, 16),
      },
      position: loadSavedPosition(`${incId}::alert-${alert.id}`) || alertPos,
      classes: `alert-node${isGhost ? ' ghost' : ''} severity-${alert.severity || 'info'} method-${alert.detection_method || 'threshold'}`,
    });
  });

  // Chronological arrows between consecutive primary alerts (by created_at).
  // Only on non-ghost incidents to keep ghost clusters uncluttered.
  if (!isGhost) {
    const chronological = [...primaryAlerts].sort(
      (a, b) => (a.created_at || '').localeCompare(b.created_at || '')
    );
    for (let j = 0; j < chronological.length - 1; j++) {
      const src = chronological[j];
      const tgt = chronological[j + 1];
      elements.push({
        group: 'edges',
        data: { id: `chrono-${src.id}-${tgt.id}`, source: `alert-${src.id}`, target: `alert-${tgt.id}` },
        classes: 'chrono-edge',
      });
    }
  }

  crossAlerts.forEach(alert => {
    const pos = computeCrossIncidentPosition(incId, allIncidents, centroids);
    elements.push({
      group: 'nodes',
      data: {
        id: `cross-${alert.id}`,
        label: (alert.event_type || '').replace(/_/g, ' '),
        type: 'cross',
        alertId: alert.id,
        incidentId: incId,
        severity: alert.severity,
        method: alert.detection_method,
        title: alert.title || '',
      },
      position: loadSavedPosition(`${incId}::cross-${alert.id}`) || pos,
      classes: `cross-node${isGhost ? ' ghost' : ''} severity-${alert.severity || 'info'} method-${alert.detection_method || 'summary'}`,
    });

    elements.push({
      group: 'edges',
      data: { id: `ce-${incId}-${alert.id}`, source: `hull-${incId}`, target: `cross-${alert.id}`, r: null },
      classes: 'cross-edge',
    });
  });

  return elements;
}

// ── Cross-incident node placement ─────────────────────────────────────────────

function computeCrossIncidentPosition(incId, incidents, centroids) {
  const others = incidents.filter(i => i.id !== incId);
  if (others.length === 0) {
    const c = centroids[incId] || { x: 0, y: 0 };
    return { x: c.x + 200, y: c.y };
  }
  const sumX = others.reduce((s, i) => s + ((centroids[i.id] || {}).x || 0), 0);
  const sumY = others.reduce((s, i) => s + ((centroids[i.id] || {}).y || 0), 0);
  return { x: sumX / others.length, y: sumY / others.length };
}

// ── localStorage position persistence ────────────────────────────────────────

function loadSavedPosition(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch (_) { return null; }
}

async function fetchIncidentDetail(id) {
  if (allIncidentDetails[id]) return allIncidentDetails[id];
  try {
    const resp = await fetch(`/api/incidents/${encodeURIComponent(id)}`);
    if (!resp.ok) return null;
    const d = await resp.json();
    allIncidentDetails[id] = d;
    return d;
  } catch (_) { return null; }
}

function restorePositions() {
  if (!cy) return;
  cy.nodes().forEach(node => {
    const key = `${currentIncidentId}::${node.id()}`;
    const saved = loadSavedPosition(key);
    if (saved) node.position(saved);
  });
}

// ── Selection opacity ─────────────────────────────────────────────────────────

function applySelectionOpacity(selectedId) {
  if (!cy) return;
  // Ghost dim raised from 0.3 → 0.55 so unselected clusters stay readable in
  // light-room / high-ambient conditions.
  cy.nodes().forEach(n => {
    n.style('opacity', n.data('incidentId') === selectedId ? 1 : 0.55);
  });
  cy.edges().forEach(e => {
    const src = cy.$id(e.data('source'));
    const tgt = cy.$id(e.data('target'));
    const sel = src.data('incidentId') === selectedId || tgt.data('incidentId') === selectedId;
    e.style('opacity', sel ? 1 : 0.4);
  });
}

// ── Utility ───────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function severityToStatus(sev) {
  return { critical: 'critical', warning: 'caution', info: 'normal' }[sev] || 'normal';
}
