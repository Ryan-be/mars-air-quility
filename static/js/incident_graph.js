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
const elNodeLink    = document.getElementById('inc-node-view-link');
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

  elList.innerHTML = incidents.map(inc => `
    <div class="inc-card${inc.id === currentIncidentId ? ' selected' : ''}"
         data-id="${escHtml(inc.id)}">
      <div class="inc-card-id">${escHtml(inc.id)}</div>
      <div class="inc-card-title" title="${escHtml(inc.title || '')}">${escHtml(inc.title || '')}</div>
      <div class="inc-card-meta">
        <span class="inc-sev-dot ${escHtml(inc.max_severity || 'info')}"></span>
        <span>${escHtml(inc.max_severity || 'info')}</span>
        <span>·</span>
        <span>${inc.alert_count ?? 0} alert${inc.alert_count === 1 ? '' : 's'}</span>
      </div>
    </div>
  `).join('');

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
    elCausalItems.innerHTML = '<div class="inc-causal-ribbon">'
      + causal.map((a, i) =>
          (i > 0 ? '<span class="inc-causal-arrow">→</span>' : '')
          + `<span class="inc-causal-chip sev-chip-${escHtml(a.severity || 'info')}" title="${escHtml(a.title || a.event_type)}">${escHtml(a.title || a.event_type)}</span>`
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

  if (nodeData.type === 'alert' && nodeData.alertId && currentIncidentId) {
    try {
      const resp = await fetch(
        `/api/incidents/${encodeURIComponent(currentIncidentId)}/alert/${nodeData.alertId}`
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const alert = await resp.json();
      if (elNodeLink) elNodeLink.href = `/inferences?id=${alert.id}`;
      if (elNodeBody) elNodeBody.innerHTML = renderAlertTable(alert);
    } catch (err) {
      if (elNodeBody) elNodeBody.textContent = 'Could not load alert detail.';
    }
  }
}

function renderAlertTable(alert) {
  const rows = [
    ['Type',       escHtml(alert.event_type || '')],
    ['Severity',   escHtml(alert.severity || '')],
    ['Method',     escHtml(alert.detection_method || '')],
    ['Confidence', `${((alert.confidence || 0) * 100).toFixed(0)}%`],
    ['Time',       escHtml((alert.created_at || '').slice(0, 16))],
  ];
  if (alert.description) {
    const desc = alert.description;
    rows.push(['Detail', escHtml(desc.slice(0, 120) + (desc.length > 120 ? '…' : ''))]);
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
        'background-color': '#131b2e',
        'border-width': 1.5,
        'border-color': '#2e3a50',
        'label': '',
        'color': '#6b7280',
        'font-size': 9,
        'font-family': 'Roboto, sans-serif',
        'text-valign': 'bottom',
        'text-margin-y': 4,
        'text-wrap': 'ellipsis',
        'text-max-width': 80,
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
        'background-opacity': 0.05,
        'border-width': 1,
        'border-style': 'solid',
        'border-color': '#5078c8',
        'border-opacity': 0.35,
        'shape': 'round-rectangle',
        'padding': '22px',
        'label': 'data(label)',
        'font-size': 9,
        'font-weight': 600,
        'color': '#8a9abf',
        'text-valign': 'top',
        'text-halign': 'center',
        'text-margin-y': -4,
        'width': 'label',
        'height': 'label',
      },
    },

    // ── Ghost hull (unselected incidents — clickable to navigate) ─────
    {
      selector: 'node.hull.ghost',
      style: {
        'background-color': '#141e32',
        'background-opacity': 0.35,
        'border-color': '#324670',
        'border-opacity': 0.3,
        'color': '#4b5b78',
        'cursor': 'pointer',
      },
    },

    // Ghost alert/root nodes (unselected incidents)
    { selector: 'node.ghost:not(.hull)', style: { 'cursor': 'pointer' } },

    // ── Node size / shape variants ────────────────────────────────────
    { selector: 'node.root-signal', style: { 'width': 12, 'height': 12, 'border-width': 1.5, 'background-color': '#0e1626' } },
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
    { selector: 'node.root-signal.labels-full', style: { 'label': 'data(label)' } },

    // ── Edges ─────────────────────────────────────────────────────────
    { selector: 'edge',            style: { 'width': 0.8, 'line-color': '#1e2b40', 'curve-style': 'bezier', 'opacity': 0.55 } },
    { selector: 'edge.intra-edge', style: { 'line-color': '#2a3850', 'width': 0.8 } },
    {
      selector: 'edge.dep-edge',
      style: { 'width': 'mapData(r, 0.3, 1.0, 0.5, 2.0)', 'line-color': '#2563eb', 'opacity': 0.45 },
    },
    { selector: 'edge.cross-edge', style: { 'line-color': '#5a9668', 'line-style': 'dashed', 'width': 0.8, 'opacity': 0.45 } },

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
  cy.nodes('.alert-node, .root-signal').forEach(n => {
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
        data: { id: `hull-${inc.id}`, label: inc.id, type: 'hull', incidentId: inc.id },
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
  elements.push({
    group: 'nodes',
    data: { id: `hull-${incId}`, label: incId, type: 'hull', incidentId: incId },
    classes: `hull${isGhost ? ' ghost' : ''} severity-${detail.max_severity || 'info'}`,
  });

  const primaryAlerts = (detail.alerts || []).filter(a => a.is_primary);
  const crossAlerts   = (detail.alerts || []).filter(a => !a.is_primary);
  const rootCount = Math.max(primaryAlerts.length, 1);

  primaryAlerts.forEach((alert, i) => {
    const angle = (2 * Math.PI * i) / rootCount - Math.PI / 2;
    const rootPos = {
      x: centre.x + 40 * Math.cos(angle),
      y: centre.y + 40 * Math.sin(angle),
    };
    const alertPos = {
      x: centre.x + 140 * Math.cos(angle),
      y: centre.y + 140 * Math.sin(angle),
    };

    elements.push({
      group: 'nodes',
      data: {
        id: `root-${alert.id}`,
        label: (alert.event_type || '').replace(/_/g, ' '),
        type: 'root',
        alertId: alert.id,
        incidentId: incId,
        parent: `hull-${incId}`,
        severity: alert.severity,
        method: alert.detection_method,
        title: alert.title || '',
      },
      position: loadSavedPosition(`${incId}::root-${alert.id}`) || rootPos,
      classes: `root-signal${isGhost ? ' ghost' : ''} severity-${alert.severity || 'info'} method-${alert.detection_method || 'threshold'}`,
    });

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

    elements.push({
      group: 'edges',
      data: { id: `e-root-${alert.id}`, source: `root-${alert.id}`, target: `alert-${alert.id}`, r: null },
      classes: 'intra-edge',
    });

    (alert.signal_deps || []).forEach(dep => {
      if (dep.r !== null && Math.abs(dep.r) >= 0.3) {
        elements.push({
          group: 'edges',
          data: { id: `dep-${alert.id}-${dep.sensor}`, source: `root-${alert.id}`, target: `alert-${alert.id}`, r: dep.r },
          classes: 'dep-edge',
        });
      }
    });
  });

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
  cy.nodes().forEach(n => {
    n.style('opacity', n.data('incidentId') === selectedId ? 1 : 0.3);
  });
  cy.edges().forEach(e => {
    const src = cy.$id(e.data('source'));
    const tgt = cy.$id(e.data('target'));
    const sel = src.data('incidentId') === selectedId || tgt.data('incidentId') === selectedId;
    e.style('opacity', sel ? 1 : 0.2);
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
