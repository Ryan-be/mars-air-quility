/**
 * incident_graph.js — Incident Correlation Graph (orchestrator)
 *
 * Responsibilities:
 *  - Toolbar (window, severity, search, edge-slider) → fetch + filter
 *  - Left panel: render incident cards, handle selection
 *  - Centre dashboard: dispatch to four section modules
 *  - Right panel: narrative, causal ribbon, similar incidents, node overlay
 *
 * Section modules each export one render* function (pure, idempotent):
 *   renderGalaxy       — scatter of incident dots
 *   renderRose         — 24-h severity rose / histogram
 *   renderStoryline    — timeline / swimlane
 *   renderCooccurrence — sensor co-occurrence matrix
 */

import { renderGalaxy }       from './sections/galaxy.mjs';
import { renderRose }         from './sections/rose.mjs';
import { renderStoryline }    from './sections/storyline.mjs';
import { renderCooccurrence } from './sections/cooccurrence.mjs';

// ── DOM refs ──────────────────────────────────────────────────────────────────

const elSearch       = document.getElementById('inc-search');
const elWindow       = document.getElementById('inc-window-group');
const elSeverity     = document.getElementById('inc-severity-group');
const elList         = document.getElementById('inc-list-items');
const elEmpty        = document.querySelector('.inc-detail-empty');
const elNarrative    = document.getElementById('inc-narrative');
const elNarrObs      = document.getElementById('inc-narrative-observed');
const elNarrInf      = document.getElementById('inc-narrative-inferred');
const elNarrImp      = document.getElementById('inc-narrative-impact');
const elNarrCorr     = document.getElementById('inc-narrative-correlation');
const elCausal       = document.getElementById('inc-causal');
const elCausalItems  = document.getElementById('inc-causal-items');
const elSimilar      = document.getElementById('inc-similar');
const elSimilarItems = document.getElementById('inc-similar-items');
const elNodeOverlay  = document.getElementById('inc-node-overlay');
const elNodeTitle    = document.getElementById('inc-node-title');
const elNodeClose    = document.getElementById('inc-node-close');
const elNodeBody     = document.getElementById('inc-node-body');

// ── Module state ──────────────────────────────────────────────────────────────

let allIncidents      = [];     // full list from /api/incidents
let currentIncidentId = null;   // selected incident ID
let currentDetail     = null;   // detail response for selected incident
let lastListSummary   = null;   // caches summary block from /api/incidents
let storylineData     = null;   // caches /storyline payload

let activeWindow   = '24h';
let activeSeverity = 'all';
let searchQuery    = '';
let searchTimer    = null;

let filterHour   = null;   // Rose chip — hour-of-day (0-23 or null)
let filterSensor = null;   // Co-occurrence chip — sensor channel name or null

// Monotonic token — prevents stale tag-fetch responses from an earlier node
// click overwriting the panel opened by a later click.
let lastTagFetchToken = 0;

// Client-side threshold for edge rendering. Persisted in localStorage.
let edgePFloor = (() => {
  try {
    const v = parseFloat(localStorage.getItem('inc.edge_p_floor'));
    return Number.isFinite(v) ? v : 0.20;
  } catch (_) { return 0.20; }
})();

// ── Tag vocab cache ───────────────────────────────────────────────────────────

let tagVocab = null;
const TAG_EMOJI = {
  cooking:              '🍳',
  external_pollution:   '🌫️',
  vehicle_exhaust:      '🚗',
  biological_offgas:    '🧬',
  chemical_offgassing:  '🧪',
  combustion:           '🔥',
  cleaning_products:    '🧹',
  human_activity:       '👤',
  mould_voc:            '🍄',
  personal_care:        '🧴',
};

async function fetchTagVocab() {
  if (tagVocab) return tagVocab;
  try {
    const resp = await fetch('/api/tags');
    if (resp.ok) {
      const data = await resp.json();
      tagVocab = data.tags || [];
    } else {
      tagVocab = [];
    }
  } catch (_) { tagVocab = []; }
  return tagVocab;
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  initToolbar();
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
      loadIncidents();
    });
  }

  if (elSearch) {
    elSearch.addEventListener('input', e => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        searchQuery = (e.target.value || '').toLowerCase().trim();
        renderList(applyClientFilter(allIncidents));
        renderDashboard();
      }, 300);
    });
    elSearch.addEventListener('ruxinput', e => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        searchQuery = (e.target.value || '').toLowerCase().trim();
        renderList(applyClientFilter(allIncidents));
        renderDashboard();
      }, 300);
    });
  }

  const slider = document.getElementById('inc-edge-slider');
  const sliderValue = document.getElementById('inc-edge-slider-value');
  if (slider) {
    slider.value = String(edgePFloor);
    _updateSliderLabel();
    slider.addEventListener('input', e => {
      edgePFloor = parseFloat(e.target.value);
      try { localStorage.setItem('inc.edge_p_floor', String(edgePFloor)); } catch (_) {}
      _updateSliderLabel();
      renderDashboard();
    });
  }
  if (!slider && sliderValue) _updateSliderLabel();
}

function _updateSliderLabel() {
  const el = document.getElementById('inc-edge-slider-value');
  if (el) el.textContent = `P ≥ ${edgePFloor.toFixed(2)}`;
}

// ── Client-side filter ────────────────────────────────────────────────────────

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

const _FALLBACK_WINDOWS = ['24h', '14d'];

async function loadIncidents() {
  if (elList) elList.innerHTML = '<div class="inc-loading">Loading…</div>';

  const windows = [activeWindow, ..._FALLBACK_WINDOWS.filter(w => w !== activeWindow)];

  for (const win of windows) {
    try {
      const params = new URLSearchParams({ window: win, limit: 200 });
      const resp = await fetch('/api/incidents?' + params);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      allIncidents = data.incidents || [];
      lastListSummary = data.summary || null;

      const counts = data.counts || { critical: 0, warning: 0, info: 0 };
      const set = (id, n) => { const el = document.getElementById(id); if (el) el.textContent = n; };
      set('pill-critical-count', counts.critical || 0);
      set('pill-warning-count',  counts.warning  || 0);
      set('pill-info-count',     counts.info     || 0);

      if (allIncidents.length > 0 || win === windows[windows.length - 1]) {
        if (win !== activeWindow) {
          activeWindow = win;
          _syncWindowButton(win);
        }
        renderList(applyClientFilter(allIncidents));

        const stillValid = currentIncidentId &&
          allIncidents.some(i => i.id === currentIncidentId);

        if (allIncidents.length === 0) {
          currentIncidentId = null;
          currentDetail = null;
          if (elEmpty) elEmpty.hidden = false;
          if (elNarrative) elNarrative.hidden = true;
          if (elCausal) elCausal.hidden = true;
          if (elSimilar) elSimilar.hidden = true;
          if (elNodeOverlay) elNodeOverlay.hidden = true;
        } else if (!stillValid) {
          await loadIncidentDetail(allIncidents[0].id);
        }

        await fetchStorylineData();
        renderDashboard();
        return;
      }
    } catch (err) {
      if (elList) elList.innerHTML = `<div class="inc-loading">Error: ${err.message}</div>`;
      return;
    }
  }
}

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
    card.addEventListener('click', () => loadIncidentDetail(card.dataset.id));
    card.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        loadIncidentDetail(card.dataset.id);
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

// ── Orchestrator ──────────────────────────────────────────────────────────────

async function fetchStorylineData() {
  try {
    const url = `/api/incidents/storyline?window=${encodeURIComponent(activeWindow)}`
              + `&severity=${encodeURIComponent(activeSeverity)}`;
    const r = await fetch(url);
    storylineData = r.ok ? await r.json() : { incidents: [] };
  } catch (_) { storylineData = { incidents: [] }; }
}

function currentWindowRange() {
  const HOURS = { '15m': 0.25, '1h': 1, '6h': 6, '12h': 12, '24h': 24, '14d': 336 };
  const hours = HOURS[activeWindow] || 24;
  const end = new Date();
  const start = new Date(end.getTime() - hours * 60 * 60 * 1000);
  return { start, end };
}

function applyChipFilters(incidents) {
  let filtered = incidents;
  if (filterHour !== null) {
    filtered = filtered.filter(inc => {
      const s = inc.started_at || '';
      if (s.length < 13) return false;
      const h = parseInt(s.slice(11, 13), 10);
      return h === filterHour;
    });
  }
  return filtered;
}

function renderChips() {
  const el = document.getElementById('inc-filter-chips');
  if (!el) return;
  const chips = [];
  if (filterHour !== null) {
    chips.push({
      label: `hour ${String(filterHour).padStart(2, '0')}:00`,
      clear: () => { filterHour = null; renderChips(); renderDashboard(); },
    });
  }
  if (filterSensor !== null) {
    chips.push({
      label: `sensor ${filterSensor}`,
      clear: () => { filterSensor = null; renderChips(); renderDashboard(); },
    });
  }
  if (chips.length === 0) {
    el.hidden = true; el.innerHTML = ''; return;
  }
  el.hidden = false;
  el.innerHTML = '';
  chips.forEach(c => {
    const span = document.createElement('span');
    span.className = 'chip';
    span.textContent = c.label;
    const close = document.createElement('span');
    close.className = 'chip-close';
    close.textContent = '×';
    close.addEventListener('click', c.clear);
    span.appendChild(close);
    el.appendChild(span);
  });
}

function renderDashboard() {
  const galaxyEl = document.getElementById('inc-galaxy');
  const roseEl   = document.getElementById('inc-rose');
  const storyEl  = document.getElementById('inc-storyline');
  const coEl     = document.getElementById('inc-cooccurrence');

  const filteredIncidents = applyChipFilters(applyClientFilter(allIncidents));

  if (galaxyEl) renderGalaxy(galaxyEl, {
    incidents: filteredIncidents,
    selectedId: currentIncidentId,
    onSelect: id => loadIncidentDetail(id),
  });

  if (roseEl && lastListSummary) renderRose(roseEl, {
    hour_histogram: lastListSummary.hour_histogram,
    severity_by_hour: lastListSummary.severity_by_hour,
    selectedHour: filterHour,
    onSelect: h => {
      filterHour = (filterHour === h ? null : h);
      renderChips();
      renderDashboard();
    },
  });

  const { start: ws, end: we } = currentWindowRange();
  if (storyEl) renderStoryline(storyEl, {
    storylineData,
    windowStart: ws, windowEnd: we,
    selectedId: currentIncidentId,
    edgePFloor,
    sensorFilter: filterSensor,
    onSelect: id => loadIncidentDetail(id),
  });

  if (coEl) renderCooccurrence(coEl, {
    storylineData,
    edgePFloor,
    sensorFilter: filterSensor,
    onSensorClick: ch => {
      filterSensor = (filterSensor === ch ? null : ch);
      renderChips();
      renderDashboard();
    },
  });
}

// ── Incident detail ───────────────────────────────────────────────────────────

async function loadIncidentDetail(id) {
  currentIncidentId = id;

  elList && elList.querySelectorAll('.inc-card').forEach(c => {
    c.classList.toggle('selected', c.dataset.id === id);
    c.setAttribute('aria-pressed', c.dataset.id === id ? 'true' : 'false');
  });

  try {
    const resp = await fetch(`/api/incidents/${encodeURIComponent(id)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    currentDetail = await resp.json();
    renderDetail(currentDetail);
  } catch (err) {
    console.error('Failed to load incident detail:', err);
    return;
  }

  renderDashboard();
}

// ── Right panel: narrative, causal, similar ───────────────────────────────────

function renderDetail(detail) {
  if (elEmpty) elEmpty.hidden = true;

  const confEl   = document.getElementById('inc-narrative-conf');
  const advEl    = document.getElementById('inc-narrative-advisory');
  const splitBtn = document.getElementById('inc-btn-split');
  const unsplitBtn = document.getElementById('inc-btn-unsplit');
  const conf     = Number(detail.confidence || 1.0);
  const confPct  = Math.round(conf * 100);
  if (confEl) {
    const primaryCount = (detail.alerts || []).filter(a => a.is_primary).length;
    const edgeCount    = (detail.edges  || []).length;
    const edgeHint = edgeCount === 0 && primaryCount >= 2
      ? '  ·  no causal links (alerts uncorrelated or >4h apart)'
      : '';
    confEl.textContent =
      `${detail.id} · confidence ${confPct}%  ·  ${primaryCount} alerts  ·  ${edgeCount} edges${edgeHint}`;
  }

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

  if (splitBtn) {
    const hasEdges = detail.edges && detail.edges.length > 0;
    splitBtn.hidden = !(conf < 0.5 && hasEdges);
    splitBtn.onclick = () => { splitAtWeakestLink(detail); };
  }

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
      const advElInner = document.getElementById('inc-narrative-advisory');
      const alertIds = (currentDetail.alerts || [])
        .filter(a => a.is_primary)
        .map(a => a.id)
        .sort((x, y) => {
          const ax = (currentDetail.alerts || []).find(a => a.id === x);
          const ay = (currentDetail.alerts || []).find(a => a.id === y);
          return (ax.created_at || '').localeCompare(ay.created_at || '');
        });
      const edges = (currentDetail.edges || []).map(e => ({
        from: e.from, to: e.to, p: e.p,
      }));
      // Simple connected-components without importing connectedComponents module
      // (that module is still available; just use it directly for split logic)
      const comps = _connectedComponents(alertIds, edges, edgePFloor);
      if (comps.length < 2) return;
      const idPos = new Map(alertIds.map((id, i) => [id, i]));
      comps.sort((a, b) =>
        Math.min(...a.map(id => idPos.get(id))) -
        Math.min(...b.map(id => idPos.get(id))));
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
            if (advElInner) {
              advElInner.textContent = `⚠ Commit failed (alert #${alertId}): server returned ${resp.status}.`;
              advElInner.hidden = false;
            }
            return;
          }
        } catch (e) {
          console.error('Commit-split network error:', e);
          if (advElInner) {
            advElInner.textContent = '⚠ Commit failed: network error.';
            advElInner.hidden = false;
          }
          return;
        }
      }
      await loadIncidents();
    };
  }

  if (detail.narrative && elNarrative) {
    if (elNarrObs)  elNarrObs.textContent  = detail.narrative.observed  || '';
    if (elNarrInf)  elNarrInf.textContent  = detail.narrative.inferred  || '';
    if (elNarrImp)  elNarrImp.textContent  = detail.narrative.impact    || '';
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
      el.addEventListener('click', () => loadIncidentDetail(el.dataset.similarId));
    });
    elSimilar.hidden = false;
  } else if (elSimilar) {
    elSimilar.hidden = true;
  }

  if (elNodeOverlay) elNodeOverlay.hidden = true;
}

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
  const sev   = a.severity || 'info';
  const mins  = Math.round((new Date(a.created_at.replace(' ', 'T')) - startTs) / 60000);
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

  const taggable = !alert.is_cross_incident;

  if (elNodeBody) {
    elNodeBody.innerHTML = renderAlertTable(alert)
      + (taggable ? renderTagsShell() : '');
  }
  if (taggable) await populateTagsSection(alert.id);
}

function renderAlertTable(alert) {
  const pct = x => `${((x || 0) * 100).toFixed(0)}%`;
  const ts  = s => (s || '').replace('T', ' ').slice(0, 19);

  const rows = [
    ['ID',         `#${alert.id}`],
    ['Time',       ts(alert.created_at)],
    ['Type',       alert.event_type || ''],
    ['Severity',   alert.severity   || ''],
    ['Method',     alert.detection_method || ''],
    ['Confidence', pct(alert.confidence)],
  ];
  if (alert.description) rows.push(['Detail', alert.description]);

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

function renderTagsShell() {
  return html`
    <div class="inc-tags-section" id="inc-tags-section">
      <div class="inc-tags-header">
        Tags <span class="inc-tags-help"
          title="Tags record what caused this event. Feedback trains the attribution engine.">ⓘ</span>
      </div>
      <div class="inc-tags-list" id="inc-tags-list">
        <span class="inc-tags-loading">Loading tags…</span>
      </div>
      <div class="inc-tag-controls">
        <select id="inc-tag-select" class="inc-tag-select">
          <option value="">Select a tag…</option>
        </select>
        <button type="button" id="inc-tag-add" class="inc-tag-add-btn">Add Tag</button>
      </div>
      <div class="inc-tag-status" id="inc-tag-status"></div>
    </div>
  `;
}

async function populateTagsSection(alertId) {
  const myToken = ++lastTagFetchToken;
  const vocab = await fetchTagVocab();
  if (myToken !== lastTagFetchToken) return;

  let current = [];
  try {
    const resp = await fetch(`/api/inferences/${alertId}/tags`);
    if (resp.ok) current = await resp.json();
  } catch (_) { /* leave empty */ }
  if (myToken !== lastTagFetchToken) return;

  const listEl   = document.getElementById('inc-tags-list');
  const selectEl = document.getElementById('inc-tag-select');
  const addBtn   = document.getElementById('inc-tag-add');
  const statusEl = document.getElementById('inc-tag-status');
  if (!listEl || !selectEl || !addBtn) return;

  if (current.length === 0) {
    listEl.innerHTML = html`<span class="inc-tags-empty">No tags yet.</span>`;
  } else {
    listEl.innerHTML = html`${current.map(t => {
      const label = (vocab.find(v => v.id === t.tag) || {}).label || t.tag;
      const emoji = TAG_EMOJI[t.tag] || '';
      return html`<span class="inc-tag-pill" data-tag="${t.tag}">
        ${emoji} ${label}
        <button type="button" class="inc-tag-pill-remove"
                data-tag="${t.tag}"
                aria-label="Remove tag ${label}"
                title="Remove tag">×</button>
      </span>`;
    })}`;

    listEl.querySelectorAll('.inc-tag-pill-remove').forEach(btn => {
      btn.onclick = async (ev) => {
        ev.stopPropagation();
        const tag = btn.dataset.tag;
        if (!tag) return;
        statusEl.textContent = 'Removing…';
        try {
          const resp = await fetch(`/api/inferences/${alertId}/tags`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tag }),
          });
          if (!resp.ok) {
            statusEl.textContent = `Remove failed (${resp.status})`;
            return;
          }
          statusEl.textContent = 'Removed';
          await populateTagsSection(alertId);
        } catch (e) {
          statusEl.textContent = 'Network error — try again.';
        }
      };
    });
  }

  selectEl.innerHTML = '<option value="">Select a tag…</option>';
  const appliedIds = new Set(current.map(t => t.tag));
  vocab.forEach(({ id, label }) => {
    if (appliedIds.has(id)) return;
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = `${TAG_EMOJI[id] || ''} ${label}`.trim();
    selectEl.appendChild(opt);
  });

  addBtn.onclick = async () => {
    const chosen = selectEl.value;
    if (!chosen) return;
    statusEl.textContent = 'Saving…';
    try {
      const resp = await fetch(`/api/inferences/${alertId}/tags`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag: chosen, confidence: 1.0 }),
      });
      if (!resp.ok) {
        statusEl.textContent = `Save failed (${resp.status})`;
        return;
      }
      statusEl.textContent = 'Saved';
      await populateTagsSection(alertId);
    } catch (e) {
      statusEl.textContent = 'Network error — try again.';
    }
  };
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

// ── Ghost hull label helper (kept for detail panel use) ───────────────────────

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

// ── Minimal connected-components (for commit-split, no separate import) ───────

function _connectedComponents(nodeIds, edges, floor) {
  const parent = new Map(nodeIds.map(id => [id, id]));
  function find(x) {
    while (parent.get(x) !== x) {
      parent.set(x, parent.get(parent.get(x)));
      x = parent.get(x);
    }
    return x;
  }
  function union(a, b) { parent.set(find(a), find(b)); }
  for (const e of edges) {
    if (e.p >= floor && parent.has(e.from) && parent.has(e.to)) {
      union(e.from, e.to);
    }
  }
  const groups = new Map();
  for (const id of nodeIds) {
    const root = find(id);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(id);
  }
  return [...groups.values()];
}

// ── Utility: html tagged template ─────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

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
