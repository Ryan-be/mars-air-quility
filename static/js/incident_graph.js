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

import { connectedComponents } from './connected_components.mjs';
import { computeCentroids, MODES } from './compute_centroids.mjs';

// ── State ─────────────────────────────────────────────────────────────────────

let cy = null;                  // Cytoscape instance
let currentIncidentId = null;   // selected incident ID
let allIncidents = [];          // full list from /api/incidents
let currentDetail = null;       // detail response for selected incident
let allIncidentDetails = {};    // incidentId → detail object (persistent cache)

// localStorage key prefix for saved node drag positions.
// Bumped to 'tl1::' when the timeline layout landed so stale positions from
// the previous radial/hub-and-spoke layouts are ignored automatically rather
// than bypassing the new collision-stacking logic via loadSavedPosition.
const POS_KEY_PREFIX = 'tl1::';

// Client-side threshold for edge rendering + subdivision preview.
// Persisted in localStorage under inc.edge_p_floor. Default 0.20.
let edgePFloor = (() => {
  try {
    const v = parseFloat(localStorage.getItem('inc.edge_p_floor'));
    return Number.isFinite(v) ? v : 0.20;
  } catch (_) { return 0.20; }
})();

// Current view mode. Persisted per-user; defaults to 'manual'.
// Valid: 'manual' | 'compact' | 'chronological'.
let viewMode = (() => {
  try {
    const v = localStorage.getItem('inc.view_mode');
    return (v === 'compact' || v === 'chronological') ? v : 'manual';
  } catch (_) { return 'manual'; }
})();

function setViewMode(mode) {
  if (mode !== 'manual' && mode !== 'compact' && mode !== 'chronological') return;
  viewMode = mode;
  try { localStorage.setItem('inc.view_mode', mode); } catch (_) {}
  if (currentDetail) renderGraph(currentDetail, allIncidents);
}

// ── DOM refs ─────────────────────────────────────────────────────────────────

const elSearch      = document.getElementById('inc-search');
const elWindow      = document.getElementById('inc-window-group');
const elSeverity    = document.getElementById('inc-severity-group');
const elList        = document.getElementById('inc-list-items');
const elEmpty       = document.querySelector('.inc-detail-empty');
const elNarrative   = document.getElementById('inc-narrative');
const elNarrObs     = document.getElementById('inc-narrative-observed');
const elNarrInf     = document.getElementById('inc-narrative-inferred');
const elNarrImp     = document.getElementById('inc-narrative-impact');
const elNarrCorr    = document.getElementById('inc-narrative-correlation');
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
  if (elWindow) {
    elWindow.addEventListener('click', e => {
      const btn = e.target.closest('.range-btn');
      if (!btn) return;
      elWindow.querySelectorAll('.range-btn').forEach(b =>
        b.classList.toggle('active', b === btn)
      );
      activeWindow = (btn.dataset.window || '24h').toLowerCase();
      loadIncidents();
    });
  }

  if (elSeverity) {
    elSeverity.addEventListener('click', e => {
      const btn = e.target.closest('.range-btn');
      if (!btn) return;
      elSeverity.querySelectorAll('.range-btn').forEach(b =>
        b.classList.toggle('active', b === btn)
      );
      activeSeverity = (btn.dataset.sev || 'all').toLowerCase();
      renderList(applyClientFilter(allIncidents));
    });
  }

  if (elSearch) {
    elSearch.addEventListener('input', e => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        searchQuery = (e.target.value || '').toLowerCase().trim();
        renderList(applyClientFilter(allIncidents));
      }, 300);
    });
    // Also listen to AstroUXDS's custom event in case rux-input eventually
    // hydrates — harmless if it never fires.
    elSearch.addEventListener('ruxinput', e => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        searchQuery = (e.target.value || '').toLowerCase().trim();
        renderList(applyClientFilter(allIncidents));
      }, 300);
    });
  }

  const slider = document.getElementById('inc-edge-slider');
  const sliderValue = document.getElementById('inc-edge-slider-value');
  if (slider && sliderValue) {
    slider.value = String(edgePFloor);
    sliderValue.textContent = `P ≥ ${edgePFloor.toFixed(2)}`;
    slider.addEventListener('input', e => {
      edgePFloor = parseFloat(e.target.value);
      sliderValue.textContent = `P ≥ ${edgePFloor.toFixed(2)}`;
      try { localStorage.setItem('inc.edge_p_floor', String(edgePFloor)); }
      catch (_) {}
      // Re-apply edge styling + subdivision preview on the current graph.
      if (typeof applyEdgePStyling === 'function') applyEdgePStyling();
      if (typeof applySubdivisionPreview === 'function') applySubdivisionPreview();
    });
  }
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
    // Clear saved positions — both current tl1 keys AND any legacy keys so
    // the reset button has the effect users expect (no stale layout ghosts).
    const toRemove = Object.keys(localStorage).filter(k =>
      k.startsWith(POS_KEY_PREFIX) ||
      allIncidents.some(i => k.startsWith(i.id + '::'))
    );
    toRemove.forEach(k => localStorage.removeItem(k));
    renderGraph(currentDetail, allIncidents);
  });

  // ── Layout controls ────────────────────────────────────────────────
  // Manual (the useful default) is a prominent button. The rarely-used
  // Cytoscape alternates live in a dropdown so they don't dominate.
  const manualBtn = document.querySelector('.inc-layout-btn[data-layout="preset"]');
  const altSelect = document.getElementById('inc-layout-alt');

  function runLayout(name) {
    if (!cy) return;
    if (name === 'preset' || !name) {
      // "Manual" means re-render from the saved timeline positions.
      if (currentDetail) renderGraph(currentDetail, allIncidents);
      return;
    }
    const common = { animate: true, animationDuration: 500, fit: false, padding: 40 };
    const opts = {
      cose:         { ...common, name: 'cose' },
      breadthfirst: { ...common, name: 'breadthfirst', directed: false },
      circle:       { ...common, name: 'circle' },
      grid:         { ...common, name: 'grid', avoidOverlap: true, condense: false },
      concentric:   {
        ...common,
        name: 'concentric',
        // Critical in the centre, info on the outside.
        concentric: n => {
          const sev = (n.classes().find(c => c.startsWith('severity-')) || '').replace('severity-', '');
          return sev === 'critical' ? 3 : sev === 'warning' ? 2 : 1;
        },
        levelWidth: () => 1,
        minNodeSpacing: 40,
      },
    };
    cy.layout(opts[name] || { name }).run();
  }

  if (manualBtn) {
    manualBtn.addEventListener('click', () => {
      if (altSelect) altSelect.value = '';
      manualBtn.classList.add('active');
      runLayout('preset');
    });
  }
  if (altSelect) {
    altSelect.addEventListener('change', e => {
      const name = e.target.value;
      if (!name) return;
      if (manualBtn) manualBtn.classList.remove('active');
      runLayout(name);
    });
  }
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
const _FALLBACK_WINDOWS = ['24h', '14d'];

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

      const summary = data.summary || { top_sensors: [], hour_histogram: [] };
      const sensorsEl = document.getElementById('inc-summary-sensors');
      if (sensorsEl) {
        sensorsEl.textContent = summary.top_sensors.length
          ? summary.top_sensors.map(s => `${s.sensor} (${s.n})`).join(' · ')
          : '—';
      }
      const histEl = document.getElementById('inc-summary-hist-bars');
      if (histEl) {
        const max = Math.max(1, ...summary.hour_histogram);
        histEl.innerHTML = summary.hour_histogram
          .map(n => `<span class="bar" style="height:${(n / max * 100).toFixed(0)}%" title="${n}"></span>`)
          .join('');
      }

      if (allIncidents.length > 0 || win === windows[windows.length - 1]) {
        // Found results, or exhausted all fallback windows
        if (win !== activeWindow) {
          // Silently update the toolbar to reflect the wider window used
          activeWindow = win;
          _syncWindowButton(win);
        }
        renderList(applyClientFilter(allIncidents));

        // After a window change, the previously-selected incident may no
        // longer be in the filtered list. Reset and pick the newest. If it
        // IS still present, re-render the graph anyway so ghost clusters
        // reflect the new list instead of showing stale incidents from the
        // previous window.
        const stillValid = currentIncidentId &&
          allIncidents.some(i => i.id === currentIncidentId);

        if (allIncidents.length === 0) {
          currentIncidentId = null;
          currentDetail = null;
          if (cy) cy.elements().remove();
          if (elEmpty) elEmpty.hidden = false;
          if (elNarrative) elNarrative.hidden = true;
          if (elCausal) elCausal.hidden = true;
          if (elSimilar) elSimilar.hidden = true;
          if (elNodeOverlay) elNodeOverlay.hidden = true;
        } else if (stillValid && currentDetail) {
          renderGraph(currentDetail, allIncidents);
        } else {
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

/** Toggle the .active class on the .range-btn group to reflect the window. */
function _syncWindowButton(win) {
  if (!elWindow) return;
  elWindow.querySelectorAll('.range-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.window === win)
  );
}

// ── Render incident list ──────────────────────────────────────────────────────

function renderList(incidents) {
  if (!elList) return;
  if (incidents.length === 0) {
    elList.innerHTML = html`<div class="inc-loading">No incidents found.</div>`;
    return;
  }
  elList.innerHTML = html`${incidents.map(incidentCardTemplate)}`;
  elList.querySelectorAll('.inc-card').forEach(card => {
    card.addEventListener('click', () => selectIncident(card.dataset.id));
    // Enter/Space keyboard activation for role="button" cards.
    card.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        selectIncident(card.dataset.id);
      }
    });
  });
}

function incidentCardTemplate(inc) {
  const start = (inc.started_at || '').replace('T', ' ').slice(11, 16);
  const end   = (inc.ended_at   || '').replace('T', ' ').slice(11, 16);
  const date  = (inc.started_at || '').slice(0, 10);
  const dur   = _formatDuration(inc.started_at, inc.ended_at);
  const sev   = inc.max_severity || 'info';
  const sel   = inc.id === currentIncidentId ? 'selected' : '';
  const count = inc.alert_count ?? 0;
  const conf  = Number(inc.confidence || 0);
  const confPct = Math.round(conf * 100);
  const confClass =
    conf >= 0.5 ? 'conf-high' :
    conf >= 0.3 ? 'conf-med'  :
                  'conf-low';
  // Role="button" + tabindex make the div focusable and discoverable to screen
  // readers; aria-pressed reflects the selected state. The click listener in
  // renderList() also handles keyboard activation via the `click` synthetic
  // event that fires on Space/Enter for role="button" elements.
  return html`
    <div class="inc-card ${sel}" data-id="${inc.id}"
         role="button" tabindex="0"
         aria-pressed="${sel ? 'true' : 'false'}"
         aria-label="${inc.id}: ${inc.title || ''}">
      <div class="inc-card-id">${inc.id}</div>
      <div class="inc-card-title" title="${inc.title || ''}">${inc.title || ''}</div>
      <div class="inc-card-time">
        <span>${date}</span><span>·</span>
        <span>${start}–${end}</span><span>·</span>
        <span>${dur}</span>
      </div>
      <div class="inc-card-meta">
        <span class="inc-sev-dot ${sev}"></span>
        <span>${sev}</span><span>·</span>
        <span>${count} alert${count === 1 ? '' : 's'}</span>
      </div>
      <div class="inc-card-conf ${confClass}" title="Causal confidence ${confPct}%">
        <div class="inc-card-conf-fill" style="width:${confPct}%"></div>
      </div>
    </div>
  `;
}

function _formatDuration(from, to) {
  if (!from || !to) return '';
  const a = new Date(String(from).replace(' ', 'T'));
  const b = new Date(String(to).replace(' ', 'T'));
  const m = Math.round((b - a) / 60000);
  if (!Number.isFinite(m) || m < 0) return '';
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
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

  const confEl = document.getElementById('inc-narrative-conf');
  const advEl  = document.getElementById('inc-narrative-advisory');
  const splitBtn = document.getElementById('inc-btn-split');
  const unsplitBtn = document.getElementById('inc-btn-unsplit');
  const conf = Number(detail.confidence || 1.0);
  const confPct = Math.round(conf * 100);
  if (confEl) confEl.textContent = `${detail.id} · confidence ${confPct}%`;

  // Advisory: weakest edge gap (if any edges).
  if (advEl) {
    if (conf < 0.5 && detail.edges && detail.edges.length) {
      const weakest = detail.edges.reduce(
        (a, b) => (a.p < b.p ? a : b), detail.edges[0]);
      const fromA = (detail.alerts || []).find(a => a.id === weakest.from);
      const toA   = (detail.alerts || []).find(a => a.id === weakest.to);
      let gapLabel = '';
      if (fromA && toA) {
        const mins = Math.round(
          (new Date(toA.created_at.replace(' ', 'T')) -
           new Date(fromA.created_at.replace(' ', 'T'))) / 60000);
        if (mins >= 60) gapLabel = `${Math.floor(mins / 60)}h ${mins % 60}m`;
        else gapLabel = `${mins}m`;
      }
      advEl.textContent = `⚠ Weakest causal link in this chain is ${gapLabel} wide — consider whether this is really one event.`;
      advEl.hidden = false;
    } else {
      advEl.hidden = true;
    }
  }

  // Split button appears when confidence < 0.5.
  if (splitBtn) {
    const hasEdges = detail.edges && detail.edges.length > 0;
    splitBtn.hidden = !(conf < 0.5 && hasEdges);
    splitBtn.onclick = () => { splitAtWeakestLink(detail); };
  }

  // Unsplit button appears when the earliest alert is a known split marker.
  if (unsplitBtn) {
    if (detail.operator_split && detail.split_alert_id) {
      unsplitBtn.hidden = false;
      unsplitBtn.onclick = async () => {
        try {
          const resp = await fetch(`/api/incidents/${encodeURIComponent(detail.id)}/unsplit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ alert_id: detail.split_alert_id }),
          });
          if (resp.ok) await loadIncidents();
        } catch (e) { console.error('Unsplit network error:', e); }
      };
    } else {
      unsplitBtn.hidden = true;
    }
  }

  const commitBtn = document.getElementById('inc-btn-commit-splits');
  if (commitBtn) {
    commitBtn.onclick = async () => {
      if (!currentDetail) return;
      const advEl = document.getElementById('inc-narrative-advisory');
      const alertIds = (currentDetail.alerts || [])
        .filter(a => a.is_primary)
        .map(a => a.id)
        .sort((x, y) => {
          // chronological order via the alert objects
          const ax = (currentDetail.alerts || []).find(a => a.id === x);
          const ay = (currentDetail.alerts || []).find(a => a.id === y);
          return (ax.created_at || '').localeCompare(ay.created_at || '');
        });
      const edges = (currentDetail.edges || []).map(e => ({
        from: e.from, to: e.to, p: e.p,
      }));
      const comps = connectedComponents(alertIds, edges, edgePFloor);
      if (comps.length < 2) return;
      // Sort comps by their earliest member's position in chronological order.
      const idPos = new Map(alertIds.map((id, i) => [id, i]));
      comps.sort((a, b) =>
        Math.min(...a.map(id => idPos.get(id))) -
        Math.min(...b.map(id => idPos.get(id))));
      // Split point for each component after the earliest: the earliest
      // member of that component.
      const splitPoints = comps.slice(1).map(comp =>
        comp.reduce((best, id) => idPos.get(id) < idPos.get(best) ? id : best, comp[0]));
      for (const alertId of splitPoints) {
        try {
          const resp = await fetch(
            `/api/incidents/${encodeURIComponent(currentDetail.id)}/split`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ alert_id: alertId }),
            },
          );
          if (!resp.ok) {
            console.error('Commit-split failed at alert', alertId);
            if (advEl) {
              advEl.textContent = `⚠ Commit failed (alert #${alertId}): server returned ${resp.status}. Check your permissions or try again.`;
              advEl.hidden = false;
            }
            return;
          }
        } catch (e) {
          console.error('Commit-split network error:', e);
          if (advEl) {
            advEl.textContent = '⚠ Commit failed: network error. Check your connection and try again.';
            advEl.hidden = false;
          }
          return;
        }
      }
      await loadIncidents();
    };
  }

  if (detail.narrative && elNarrative) {
    if (elNarrObs) elNarrObs.textContent = detail.narrative.observed || '';
    if (elNarrInf) elNarrInf.textContent = detail.narrative.inferred || '';
    if (elNarrImp) elNarrImp.textContent = detail.narrative.impact || '';
    if (elNarrCorr) {
      const corr = detail.narrative.correlation || '';
      elNarrCorr.textContent = corr;
      elNarrCorr.hidden = !corr;
    }
    elNarrative.hidden = false;
  }

  const causal = detail.causal_sequence || [];
  if (causal.length > 0 && elCausal) {
    const startTs = new Date(causal[0].created_at.replace(' ', 'T'));
    elCausalItems.innerHTML = html`
      <div class="inc-causal-ribbon">
        ${causal.map((a, i) => causalChipTemplate(a, i, startTs))}
      </div>
    `;
    elCausal.hidden = false;
  } else if (elCausal) {
    elCausal.hidden = true;
  }

  const similar = detail.similar || [];
  if (similar.length > 0 && elSimilar) {
    elSimilarItems.innerHTML = html`${similar.map(similarRowTemplate)}`;
    elSimilarItems.querySelectorAll('.inc-similar-item').forEach(el => {
      el.addEventListener('click', () => selectIncident(el.dataset.similarId));
    });
    elSimilar.hidden = false;
  } else if (elSimilar) {
    elSimilar.hidden = true;
  }

  if (elNodeOverlay) elNodeOverlay.hidden = true;
}

/**
 * Find the incident's weakest edge and mark the later endpoint as a
 * split marker via POST /api/incidents/<id>/split. Refreshes on success.
 */
async function splitAtWeakestLink(detail) {
  if (!detail.edges || !detail.edges.length) return;
  const weakest = detail.edges.reduce(
    (a, b) => (a.p < b.p ? a : b), detail.edges[0]);
  try {
    const resp = await fetch(`/api/incidents/${encodeURIComponent(detail.id)}/split`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ alert_id: weakest.to }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      console.error('Split failed:', err);
      return;
    }
    await loadIncidents();
  } catch (e) { console.error('Split network error:', e); }
}

function causalChipTemplate(a, i, startTs) {
  const clock = (a.created_at || '').slice(11, 16);
  const label = a.title || a.event_type || '';
  const sev = a.severity || 'info';
  const mins = Math.round((new Date(a.created_at.replace(' ', 'T')) - startTs) / 60000);
  const delta = mins === 0 ? 'start' : `+${mins}m`;
  return html`
    ${i > 0 ? html.raw('<span class="inc-causal-arrow">→</span>') : ''}
    <span class="inc-causal-chip-group" title="${clock} — ${label}">
      <span class="inc-causal-chip sev-chip-${sev}">${label}</span>
      <span class="inc-causal-chip-time">${delta}</span>
    </span>
  `;
}

function similarRowTemplate(s) {
  const pct = (s.similarity * 100).toFixed(0);
  return html`
    <div class="inc-similar-item" data-similar-id="${s.id}">
      <div class="inc-similar-main">
        <div class="inc-similar-id">${s.id}</div>
        <div class="inc-similar-title">${s.title || ''}</div>
        <div class="inc-similar-why">${s.why || ''}</div>
      </div>
      <div class="inc-similar-right">
        <div class="inc-similar-score">${pct}% similar</div>
        <span class="inc-similar-nav">›</span>
      </div>
    </div>
  `;
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
  const pct = x => `${((x || 0) * 100).toFixed(0)}%`;
  const ts  = s => (s || '').replace('T', ' ').slice(0, 19);

  // Build rows as [label, value] pairs.  value can be a plain string (which
  // will be auto-escaped when interpolated) or a SafeHTML block (already
  // escaped, e.g. the Correlates block).
  const rows = [
    ['ID',         `#${alert.id}`],
    ['Time',       ts(alert.created_at)],
    ['Type',       alert.event_type || ''],
    ['Severity',   alert.severity || ''],
    ['Method',     alert.detection_method || ''],
    ['Confidence', pct(alert.confidence)],
  ];
  if (alert.description) rows.push(['Detail', alert.description]);

  // Pearson-r correlations, strongest first, |r| >= 0.3 only.
  const strong = (alert.signal_deps || [])
    .filter(d => d.r !== null && Math.abs(d.r) >= 0.3)
    .sort((a, b) => Math.abs(b.r) - Math.abs(a.r))
    .slice(0, 6);
  if (strong.length) rows.push(['Correlates', correlatesBlock(strong)]);

  return html`
    <table>
      ${rows.map(([k, v]) => html`<tr><td>${k}</td><td>${v}</td></tr>`)}
    </table>
  `;
}

function correlatesBlock(deps) {
  return html`
    <div class="evidence-block">
      ${deps.map(d => html`
        <div class="dep-row">
          <span>${d.sensor}</span>
          <span class="dep-r dep-r-${d.r >= 0 ? 'pos' : 'neg'}">
            r = ${d.r >= 0 ? '+' : ''}${d.r.toFixed(2)}
          </span>
        </div>
      `)}
    </div>
  `;
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

    // ── Hull border dash-pattern ramp by confidence tier ────────────
    // Severity border colour is unchanged; this is an orthogonal visual channel.
    { selector: 'node.hull.conf-high', style: { 'border-style': 'solid' } },
    { selector: 'node.hull.conf-med',  style: { 'border-style': 'dashed' } },
    { selector: 'node.hull.conf-low',  style: { 'border-style': 'dashed', 'border-width': 2.5 } },

    // ── Node size / shape variants ────────────────────────────────────
    { selector: 'node.alert-node',  style: { 'width': 20, 'height': 20, 'border-width': 1.5 } },

    // Headline: the first primary alert chronologically — the "entry point"
    // of the incident that the narrative panel leads with. Slightly larger
    // and with a thicker, brighter border so the eye lands on it first.
    { selector: 'node.alert-node.headline', style: {
        'width': 26,
        'height': 26,
        'border-width': 2.5,
        'font-weight': 700,
    } },
    {
      selector: 'node.cross-node',
      style: {
        'width': 18,
        'height': 18,
        'border-style': 'dashed',
        'border-color': '#64b482',
        'border-opacity': 0.75,
        'background-color': '#101c14',
        // Cross-incident nodes always carry a short human-readable label so
        // the band below the cluster grid is scannable without having to zoom.
        'label': 'data(label_short)',
        'font-size': 9,
        'color': '#9fc5a9',
        'text-valign': 'bottom',
        'text-margin-y': 3,
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
    // Short-label mode uses only HH:MM (data-label_time) so timestamps stop
    // overflowing when nodes are close in time. Full label shows the event
    // title (wrapping handled by text-wrap: ellipsis + text-max-width).
    { selector: 'node.alert-node.labels-ts',    style: { 'label': 'data(label_time)' } },
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

    // Subdivision preview overlay — dashed rectangles drawn inside a hull
    // when raising the slider would split the incident.
    {
      selector: 'node.subdiv-outline',
      style: {
        'shape': 'round-rectangle',
        'background-opacity': 0,
        'border-width': 1.5,
        'border-style': 'dashed',
        'border-color': '#4dacff',
        'border-opacity': 0.7,
        'label': '',
        'events': 'no',
      },
    },
  ];
}

// ── Progressive zoom ──────────────────────────────────────────────────────────

function applyZoomClasses(zoom) {
  if (!cy) return;
  // Thresholds:
  //   < 0.9  : no labels (overview)
  //   0.9–2.4: HH:MM only (data-label_time) — fits under a 20px node
  //   ≥ 2.4  : full event title (data-label) — only at close inspection
  // The full-title threshold is deliberately high because titles are ~80px
  // wide and overlap neighbours badly in dense clusters otherwise.
  cy.nodes('.alert-node').forEach(n => {
    if (zoom < 0.9) {
      n.removeClass('labels-ts labels-full');
    } else if (zoom < 2.4) {
      n.addClass('labels-ts');
      n.removeClass('labels-full');
    } else {
      n.addClass('labels-full');
      n.removeClass('labels-ts');
    }
  });
}

// ── Cytoscape init ────────────────────────────────────────────────────────────

// Teardown hook for the custom minimap so we can re-init cleanly when
// Cytoscape is recreated.
let _miniTeardown = null;

function initCytoscape() {
  if (typeof cytoscape === 'undefined') {
    console.error('Cytoscape.js not loaded — graph unavailable. Check CDN connectivity.');
    return;
  }
  if (_miniTeardown) { try { _miniTeardown(); } catch (_) {} _miniTeardown = null; }
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

  // Custom lightweight minimap — avoids the jQuery dependency of
  // cytoscape-navigator. Draws hull rectangles + a draggable viewport
  // indicator on a 240×150 canvas pinned bottom-right.
  _miniTeardown = initMinimap();

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
    // Key by the node's OWN incident, not the selected incident, so ghost
    // cluster drags persist correctly across selection changes.
    const incId = node.data('incidentId') || currentIncidentId;
    const key = `${POS_KEY_PREFIX}${incId}::${node.id()}`;
    try { localStorage.setItem(key, JSON.stringify(pos)); } catch (_) {}
  });

  // Hover tooltip on edges — "14 min apart · eco2_ppm · P = 0.82"
  cy.on('mouseover', 'edge.chrono-edge', evt => {
    const e = evt.target;
    const p = Number(e.data('p') || 0);
    const shared = String(e.data('shared_sensors') || '').split(',').filter(Boolean);
    const src = cy.$id(e.data('source'));
    const tgt = cy.$id(e.data('target'));
    const srcT = String(src.data('created_at') || '').replace('T', ' ');
    const tgtT = String(tgt.data('created_at') || '').replace('T', ' ');
    let gapStr = '';
    try {
      const mins = Math.round((new Date(tgtT) - new Date(srcT)) / 60000);
      gapStr = `${mins} min apart`;
    } catch (_) { gapStr = ''; }
    const sensorsStr = shared.length ? shared.join(', ') : '(no shared sensor)';
    e.data('tooltip', `${gapStr} · ${sensorsStr} · P = ${p.toFixed(2)}`);
    const el = document.getElementById('cy-graph');
    if (el) el.title = e.data('tooltip');
  });
  cy.on('mouseout', 'edge.chrono-edge', () => {
    const el = document.getElementById('cy-graph');
    if (el) el.title = '';
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
    applyEdgePStyling();
    applySubdivisionPreview();
  });

  applyEdgePStyling();
  applySubdivisionPreview();
}

// ── Edge styling by P ─────────────────────────────────────────────────────────

/**
 * Apply per-edge visual treatment based on P and the current slider threshold.
 *
 *   P >= 0.7  : opacity 1.0  width 2.0  solid
 *   0.4–0.7   : opacity 0.7  width 1.5  solid
 *   0.2–0.4   : opacity 0.5  width 1.0  dashed
 *   floor–0.2 : opacity 0.3  width 0.8  dotted
 *   < floor   : hidden
 *
 * Edges are expected to carry .data('p') from renderGraph.
 */
function applyEdgePStyling() {
  if (!cy) return;
  cy.edges('.chrono-edge').forEach(e => {
    const p = Number(e.data('p') || 0);
    if (p < edgePFloor) {
      e.style({ display: 'none' });
      return;
    }
    let opacity, width, lineStyle;
    if      (p >= 0.7) { opacity = 1.0; width = 2.0; lineStyle = 'solid'; }
    else if (p >= 0.4) { opacity = 0.7; width = 1.5; lineStyle = 'solid'; }
    else if (p >= 0.2) { opacity = 0.5; width = 1.0; lineStyle = 'dashed'; }
    else               { opacity = 0.3; width = 0.8; lineStyle = 'dotted'; }
    e.style({
      display: 'element',
      'opacity': opacity,
      'width': width,
      'line-style': lineStyle,
    });
  });
}

// ── Subdivision preview ───────────────────────────────────────────────────────

/**
 * Re-run connectedComponents at the current slider threshold, scoped to
 * each incident. If the incident would split into 2+ components, draw
 * dashed sub-outlines as overlay nodes inside the hull and update the
 * hull label with "Would split into N" badge.
 *
 * Purely client-side — no API calls, no server state changes.
 */
function applySubdivisionPreview() {
  if (!cy || !currentDetail) return;
  const incId = currentDetail.id;
  const alertIds = (currentDetail.alerts || [])
    .filter(a => a.is_primary)
    .map(a => a.id);
  const edges = (currentDetail.edges || []).map(e => ({
    from: e.from, to: e.to, p: e.p,
  }));
  const components = connectedComponents(alertIds, edges, edgePFloor);
  const hull = cy.$id(`hull-${incId}`);
  // Remove previous subdivision overlay outlines.
  cy.nodes('.subdiv-outline').remove();
  if (components.length < 2) {
    // No subdivision; restore the plain hull label.
    if (hull && hull.length) hull.data('label', incId);
    return;
  }
  if (hull && hull.length) {
    hull.data('label', `${incId}  ·  Would split into ${components.length} at P ≥ ${edgePFloor.toFixed(2)}`);
  }
  // For each component, draw a dashed rectangle overlay node that
  // surrounds the component's alert nodes.
  components.forEach((compIds, idx) => {
    const nodes = compIds.map(id => cy.$id(`alert-${id}`)).filter(n => n.length);
    if (!nodes.length) return;
    let xs = nodes.flatMap(n => [n.position('x')]);
    let ys = nodes.flatMap(n => [n.position('y')]);
    const pad = 28;
    const x1 = Math.min(...xs) - pad;
    const x2 = Math.max(...xs) + pad;
    const y1 = Math.min(...ys) - pad;
    const y2 = Math.max(...ys) + pad;
    cy.add({
      group: 'nodes',
      data: {
        id: `subdiv-${incId}-${idx}`,
        label: '',
      },
      position: { x: (x1 + x2) / 2, y: (y1 + y2) / 2 },
      classes: 'subdiv-outline',
      style: {
        width: x2 - x1,
        height: y2 - y1,
      },
    });
  });
  const commitBtn = document.getElementById('inc-btn-commit-splits');
  if (commitBtn) {
    commitBtn.hidden = components.length < 2;
  }
}

// ── Centroid placement ────────────────────────────────────────────────────────

function buildCentroids(incidents) {
  return computeCentroids(incidents, viewMode);
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
  const hullConf = Number(detail.confidence || 1.0);
  const hullConfClass =
    hullConf >= 0.5 ? 'conf-high' :
    hullConf >= 0.3 ? 'conf-med'  :
                      'conf-low';
  elements.push({
    group: 'nodes',
    data: { id: `hull-${incId}`, label: hullLabel, type: 'hull', incidentId: incId },
    classes: `hull${isGhost ? ' ghost' : ''} severity-${detail.max_severity || 'info'} ${hullConfClass}`,
  });

  const primaryAlerts = (detail.alerts || []).filter(a => a.is_primary);
  const crossAlerts   = (detail.alerts || []).filter(a => !a.is_primary);
  const rootCount = Math.max(primaryAlerts.length, 1);

  // ── Timeline layout: x = minutes from incident start, y = severity lane ──
  // Each alert is placed at (baseX, baseY) computed from its timestamp and
  // severity.  When two alerts share time + severity they'd sit at the same
  // point, so we walk a sequence of y-offsets from the lane centre and take
  // the first slot that is free (no already-placed alert in the same x-band
  // with the same y).  This is strictly better than counting neighbours: a
  // dense cluster of many close-in-time alerts gets spread correctly instead
  // of pairs colliding at the same computed stackDir.
  //
  // TIMELINE_WIDTH_PX is adaptive: 360px minimum, but grows with alert count
  // so that a 40-alert incident doesn't jam everything into the same narrow
  // window.  ~32px per alert gives a node-width of 20 + ~12 gap on average,
  // enough for HH:MM labels not to overlap at default zoom.
  // Pull the active-view-mode constants so alert placement matches the
  // hull sizing done in computeCentroids(). MODES keys: manual / compact /
  // chronological.
  const modeCfg           = MODES[viewMode] || MODES.manual;
  const PX_PER_ALERT      = modeCfg.PX_PER_ALERT;
  const MIN_WIDTH_PX      = modeCfg.MIN_WIDTH_PX;
  const TIMELINE_WIDTH_PX = Math.max(MIN_WIDTH_PX, primaryAlerts.length * PX_PER_ALERT);
  const LANE_HEIGHT_PX    = modeCfg.LANE_HEIGHT_PX;
  const LANE_BY_SEVERITY  = { critical: 0, warning: 1, info: 2 };
  const COLLISION_X_PX    = Math.max(PX_PER_ALERT - 2, 14);  // scales with alert spacing
  const STACK_DY_PX       = modeCfg.STACK_DY_PX;
  const STACK_DX_PX       = modeCfg.STACK_DY_PX;  // keep DX = DY for symmetric diagonal stacking
  // Order: centre first, then alternating out so the stack stays balanced.
  const STACK_STEPS = [0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5];

  const startMs = new Date((detail.started_at || '').replace(' ', 'T')).getTime();
  const endMs   = new Date((detail.ended_at   || '').replace(' ', 'T')).getTime();
  const validSpan = Number.isFinite(startMs) && Number.isFinite(endMs);
  const spanMs  = validSpan ? Math.max(endMs - startMs, 60_000) : 60_000;

  // Placed alerts: {x, y, lane}. y is the FINAL post-stack y, so later alerts
  // can detect which slots are actually taken.
  const placed = [];

  // primaryAlerts come from the API in chronological order (SELECT ... ORDER BY
  // created_at). Index 0 is therefore the "headline" alert — the same event
  // the narrative leads with ("Anomaly: TVOC at 13:41."). Tagging it with a
  // `headline` class lets the stylesheet make it visually dominant so the
  // reader's eye lands on the incident's entry point without reading text.
  primaryAlerts.forEach((alert, idx) => {
    const isHeadline = idx === 0;
    const alertMs = new Date((alert.created_at || '').replace(' ', 'T')).getTime();
    const t = (validSpan && Number.isFinite(alertMs))
      ? Math.max(0, Math.min(1, (alertMs - startMs) / spanMs))
      : 0;
    const lane = LANE_BY_SEVERITY[alert.severity] ?? 2;
    const baseX = centre.x - TIMELINE_WIDTH_PX / 2 + t * TIMELINE_WIDTH_PX;
    const baseY = centre.y - LANE_HEIGHT_PX + lane * LANE_HEIGHT_PX;

    // Walk STACK_STEPS to find the first (x, y) slot not occupied by another
    // placement. Each step fans diagonally: step k => (baseX + k*DX, baseY +
    // k*DY). Diagonal placement keeps HH:MM labels from colliding when many
    // alerts share an exact timestamp (all those alerts have identical baseX,
    // so pure vertical stacking would collide their labels at the same x).
    let finalX = baseX, finalY = baseY;
    for (const step of STACK_STEPS) {
      const candidateX = baseX + step * STACK_DX_PX;
      const candidateY = baseY + step * STACK_DY_PX;
      const taken = placed.some(p =>
        Math.abs(p.x - candidateX) < COLLISION_X_PX &&
        Math.abs(p.y - candidateY) < STACK_DY_PX
      );
      if (!taken) { finalX = candidateX; finalY = candidateY; break; }
    }

    const alertPos = { x: finalX, y: finalY };
    placed.push({ x: finalX, y: finalY });

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
        // HH:MM only — used by labels-ts so close-in-time nodes don't overlap.
        label_time: (alert.created_at || '').slice(11, 16),
      },
      position: loadSavedPosition(`${POS_KEY_PREFIX}${incId}::alert-${alert.id}`) || alertPos,
      classes: `alert-node${isGhost ? ' ghost' : ''}${isHeadline ? ' headline' : ''} severity-${alert.severity || 'info'} method-${alert.detection_method || 'threshold'}`,
    });
  });

  // Chronological arrows between primary alerts, styled by edge-probability
  // P from the API response. P drives opacity/width/style via
  // applyEdgePStyling(); shared_sensors feeds the hover tooltip.
  if (!isGhost) {
    (detail.edges || []).forEach(edge => {
      elements.push({
        group: 'edges',
        data: {
          id: `edge-${incId}-${edge.from}-${edge.to}`,
          source: `alert-${edge.from}`,
          target: `alert-${edge.to}`,
          incidentId: incId,
          p: edge.p,
          shared_sensors: (edge.shared_sensors || []).join(','),
        },
        classes: 'chrono-edge',
      });
    });
  }

  crossAlerts.forEach(alert => {
    const pos = computeCrossIncidentPosition(incId, alert.id, centroids);
    // Short label that's always visible on the band.  Compact event-type
    // abbreviation ("hourly", "daily", "pattern", "annotation").
    const et = alert.event_type || '';
    const labelShort =
      et.startsWith('hourly_summary') ? 'hourly'
      : et.startsWith('daily_summary') ? 'daily'
      : et.startsWith('daily_pattern') ? 'pattern'
      : et.startsWith('annotation_context_') ? 'annotation'
      : et.replace(/_/g, ' ').slice(0, 12);
    elements.push({
      group: 'nodes',
      data: {
        id: `cross-${alert.id}`,
        label: (alert.event_type || '').replace(/_/g, ' '),
        label_short: labelShort,
        type: 'cross',
        alertId: alert.id,
        incidentId: incId,
        severity: alert.severity,
        method: alert.detection_method,
        title: alert.title || '',
      },
      position: loadSavedPosition(`${POS_KEY_PREFIX}${incId}::cross-${alert.id}`) || pos,
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

// Tracks how many cross nodes have been placed per row so we can stagger in
// two rows when many cross-nodes land in the band.  Keyed by the centroids
// object so it resets on each re-render.
const _crossPlaced = new WeakMap();

/** Place cross-incident nodes on a horizontal band below the cluster grid.
 *  Uses deterministic hash-spread for x, then adds a second-row offset when
 *  an earlier node already landed within CROSS_NODE_SPACING. Keeps the band
 *  readable instead of piling dashed squares on top of each other. */
function computeCrossIncidentPosition(incId, alertId, centroids) {
  const bandY = centroids.__crossBandY || 800;
  const BAND_WIDTH_PX    = 2200;  // roomy landscape strip
  const CROSS_NODE_SPACE = 60;    // minimum x-gap before we stack to row 2
  const ROW_HEIGHT_PX    = 50;

  // Hash (incId, alertId) → x across BAND_WIDTH_PX.
  const key = `${incId}-${alertId}`;
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
  const baseX = (Math.abs(h) % BAND_WIDTH_PX) - BAND_WIDTH_PX / 2;

  // Find an x-slot that isn't already claimed.  Walk outward from baseX in
  // ±CROSS_NODE_SPACE increments, alternating rows, until we find a gap.
  let placed = _crossPlaced.get(centroids);
  if (!placed) { placed = []; _crossPlaced.set(centroids, placed); }

  const OFFSETS = [0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 6, -6];
  for (const off of OFFSETS) {
    const x = baseX + off * CROSS_NODE_SPACE;
    for (const row of [0, 1]) {
      const y = bandY + row * ROW_HEIGHT_PX;
      const clash = placed.some(p =>
        Math.abs(p.x - x) < CROSS_NODE_SPACE && p.y === y
      );
      if (!clash) { placed.push({ x, y }); return { x, y }; }
    }
  }
  // Saturated band — return baseX on row 0 as a last resort.
  placed.push({ x: baseX, y: bandY });
  return { x: baseX, y: bandY };
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
  // Key each node by its OWN incident, not the currently selected one.
  // The previous implementation used currentIncidentId for every node, which
  // meant ghost nodes looked up positions under the wrong namespace.
  cy.nodes().forEach(node => {
    const incId = node.data('incidentId');
    if (!incId) return;
    const saved = loadSavedPosition(`${POS_KEY_PREFIX}${incId}::${node.id()}`);
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

// ── Minimap ───────────────────────────────────────────────────────────────────

/**
 * Lightweight canvas-based minimap.
 *
 * Draws one rectangle per incident hull onto a 240x150 canvas, with a blue
 * viewport indicator showing the portion of the world Cytoscape is currently
 * rendering. Click or click-drag the minimap to pan the main view.
 *
 * Returns a teardown function that removes listeners + DOM children so it can
 * be called safely when the Cytoscape instance is recreated.
 */
function initMinimap() {
  const mini = document.getElementById('cy-minimap');
  if (!mini || !cy) return null;

  const MINI_W = mini.clientWidth || 240;
  const MINI_H = mini.clientHeight || 150;

  mini.innerHTML = '';
  const canvas = document.createElement('canvas');
  canvas.width = MINI_W;
  canvas.height = MINI_H;
  const viewport = document.createElement('div');
  viewport.className = 'mini-viewport';
  const label = document.createElement('span');
  label.className = 'mini-label';
  label.textContent = 'Overview';
  mini.appendChild(canvas);
  mini.appendChild(viewport);
  mini.appendChild(label);

  let scale = 1, offX = 0, offY = 0;

  function computeTransform() {
    const bb = cy.elements().boundingBox({ includeLabels: false });
    const worldW = Math.max(bb.w, 1);
    const worldH = Math.max(bb.h, 1);
    const pad = 8;
    scale = Math.min((MINI_W - pad * 2) / worldW, (MINI_H - pad * 2) / worldH);
    offX = pad - bb.x1 * scale + (MINI_W - pad * 2 - worldW * scale) / 2;
    offY = pad - bb.y1 * scale + (MINI_H - pad * 2 - worldH * scale) / 2;
  }

  function render() {
    if (!cy) return;
    computeTransform();
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, MINI_W, MINI_H);

    // Hull rectangles — one per incident. Severity-coloured border.
    cy.nodes('.hull').forEach(n => {
      const bb = n.boundingBox();
      const x = bb.x1 * scale + offX;
      const y = bb.y1 * scale + offY;
      const w = Math.max(2, bb.w * scale);
      const h = Math.max(2, bb.h * scale);
      const sev = (n.classes().find(c => c.startsWith('severity-')) || '').replace('severity-', '');
      const isGhost = n.hasClass('ghost');
      const color = sev === 'critical' ? '#ff3838'
                  : sev === 'warning'  ? '#fc8c2f'
                  : sev === 'info'     ? '#2dccff'
                  : '#5a6a85';
      ctx.fillStyle = isGhost ? 'rgba(30,40,60,0.55)' : 'rgba(45,100,255,0.06)';
      ctx.fillRect(x, y, w, h);
      ctx.strokeStyle = color;
      ctx.globalAlpha = isGhost ? 0.35 : 0.75;
      ctx.lineWidth = 1;
      ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
      ctx.globalAlpha = 1;
    });

    // Viewport rectangle: convert Cytoscape's pan/zoom into world coords.
    const vw = cy.width();
    const vh = cy.height();
    const z  = cy.zoom();
    const p  = cy.pan();
    const vx1 = -p.x / z;
    const vy1 = -p.y / z;
    const vx2 = vx1 + vw / z;
    const vy2 = vy1 + vh / z;
    const vLeft = vx1 * scale + offX;
    const vTop  = vy1 * scale + offY;
    const vWidth  = Math.max(6, (vx2 - vx1) * scale);
    const vHeight = Math.max(6, (vy2 - vy1) * scale);
    viewport.style.left   = vLeft + 'px';
    viewport.style.top    = vTop + 'px';
    viewport.style.width  = vWidth + 'px';
    viewport.style.height = vHeight + 'px';
  }

  // Map a minimap pixel to a world-coord point.
  function miniToWorld(px, py) {
    return { x: (px - offX) / scale, y: (py - offY) / scale };
  }

  // Pan the main view so its centre lands at a given world point.
  function panTo(world) {
    const z = cy.zoom();
    cy.pan({ x: cy.width() / 2 - world.x * z, y: cy.height() / 2 - world.y * z });
  }

  let dragging = false;
  function onPointerDown(e) {
    dragging = true;
    const rect = mini.getBoundingClientRect();
    panTo(miniToWorld(e.clientX - rect.left, e.clientY - rect.top));
  }
  function onPointerMove(e) {
    if (!dragging) return;
    const rect = mini.getBoundingClientRect();
    panTo(miniToWorld(e.clientX - rect.left, e.clientY - rect.top));
  }
  function onPointerUp() { dragging = false; }

  mini.addEventListener('pointerdown', onPointerDown);
  window.addEventListener('pointermove', onPointerMove);
  window.addEventListener('pointerup', onPointerUp);

  // Re-render on any viewport change or topology change.
  const rerender = () => render();
  cy.on('pan zoom render position', rerender);
  cy.on('add remove', rerender);
  render();

  return function teardown() {
    try {
      cy.off('pan zoom render position', rerender);
      cy.off('add remove', rerender);
    } catch (_) {}
    mini.removeEventListener('pointerdown', onPointerDown);
    window.removeEventListener('pointermove', onPointerMove);
    window.removeEventListener('pointerup', onPointerUp);
    mini.innerHTML = '';
  };
}

// ── Utility ───────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * Tiny HTML tagged-template literal. Auto-escapes interpolated values,
 * flattens arrays of template results, provides `html.raw()` as a rare
 * escape hatch for already-trusted fragments.
 *
 *   html`<div class="x">${user.name}</div>`           // name is escaped
 *   html`<ul>${items.map(i => html`<li>${i}</li>`)}</ul>`  // composes
 *   html`${html.raw('<em>safe</em>')}`                // explicit opt-out
 *
 * Returns a SafeHTML instance that coerces to a string when assigned to
 * innerHTML and is recognised as pre-escaped when re-interpolated.
 */
class SafeHTML extends String {}

function html(strings, ...values) {
  let out = '';
  for (let i = 0; i < strings.length; i++) {
    out += strings[i];
    if (i >= values.length) continue;
    const v = values[i];
    if (v == null || v === false) continue;
    if (Array.isArray(v)) {
      for (const item of v) out += _interp(item);
    } else {
      out += _interp(v);
    }
  }
  return new SafeHTML(out);
}
html.raw = s => new SafeHTML(String(s));

function _interp(v) {
  if (v == null || v === false) return '';
  return v instanceof SafeHTML ? v.toString() : escHtml(String(v));
}

function severityToStatus(sev) {
  return { critical: 'critical', warning: 'caution', info: 'normal' }[sev] || 'normal';
}
