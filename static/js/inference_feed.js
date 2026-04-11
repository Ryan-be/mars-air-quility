/**
 * inference_feed.js — Shared inference feed renderer.
 *
 * Both dashboard.js (live feed) and detections_insights.js (history tab) render
 * inference cards with the same layout, filtering, and chip logic. This module
 * provides a factory that can be instantiated for each page with page-specific
 * DOM element IDs and API endpoints.
 *
 * Usage:
 *   import { createInferenceFeed } from './inference_feed.js';
 *
 *   const feed = createInferenceFeed({
 *     feedId:        'inferenceFeed',       // DOM id of the feed container
 *     countId:       'inferenceCount',     // DOM id of the count element
 *     filtersId:     'inferenceFilters',  // DOM id of the filter bar
 *     cardDataAttr:  'data-inf-id',        // data attribute on each card for the ID
 *     openDialog:    myOpenDialog,          // function(id) called when a card is clicked
 *   });
 *   feed.load();
 */
'use strict';

export const SEVERITY_LABEL = { info: 'Info', warning: 'Warning', critical: 'Critical' };
export const SEVERITY_CLS   = { info: 'inf-info', warning: 'inf-warning', critical: 'inf-critical' };

const METHOD_CLS  = { rule: 'chip--rule', statistical: 'chip--statistical', ml: 'chip--ml' };
const METHOD_LBL  = { rule: 'Rule', statistical: 'Statistical', ml: 'ML' };

const CHIP_TOOLTIP =
  'Rule = a fixed threshold was crossed. ' +
  'Statistical = an unusual reading compared to this sensor\u2019s learned normal. ' +
  'ML = an unusual pattern across multiple sensors simultaneously.';

export function renderDetectionChip(detectionMethod) {
  const cls  = METHOD_CLS[detectionMethod]  || 'chip--rule';
  const lbl  = METHOD_LBL[detectionMethod] || 'Rule';
  return `<span class="chip ${cls}" title="${CHIP_TOOLTIP}">${lbl} <span class="chip-info">\u24d8</span></span>`;
}

export function createInferenceFeed({
  feedId,
  countId,
  filtersId,
  cardDataAttr,
  openDialog,
}) {
  let _inferences    = [];
  let _activeCategory = 'all';
  let _catsLoaded     = false;

  function _buildCardHtml(inf) {
    const sevCls = SEVERITY_CLS[inf.severity]  || 'inf-info';
    const sevLbl = SEVERITY_LABEL[inf.severity] || inf.severity;
    const time   = new Date(inf.created_at).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    });
    const chip   = renderDetectionChip(inf.detection_method || 'rule');
    const catCls = 'inf-cat-' + (inf.category || 'other');
    const dis    = inf.dismissed ? ' dismissed' : '';
    const idVal  = inf[cardDataAttr === 'data-inf-id' ? 'id' : 'id'];
    return `<button class="inference-card ${sevCls} ${catCls}${dis}"
              ${cardDataAttr}="${idVal}" title="Tap for details">
      <div class="inf-card-left">
        <div class="inf-card-badges">${chip} <span class="inf-badge ${sevCls} inf-badge-sm">${sevLbl}</span></div>
        <span class="inf-card-type">${inf.event_type.replace(/_/g, ' ')}</span>
        <span class="inf-card-summary">${inf.title}</span>
      </div>
      <div class="inf-card-right">
        <span class="inf-card-time">${time}</span>
        <span class="inf-card-conf">${Math.round(inf.confidence * 100)}%</span>
      </div>
    </button>`;
  }

  function _renderFeed() {
    const feed    = document.getElementById(feedId);
    const countEl = document.getElementById(countId);
    if (!feed) {
      console.warn('[inference_feed] feed container not found:', feedId);
      return;
    }
    console.log('[inference_feed] _renderFeed, _inferences=', _inferences.length, 'activeCategory=', _activeCategory);
    if (!_catsLoaded) _loadCategories();

    let filtered = _inferences;
    if (_activeCategory && _activeCategory !== 'all') {
      if (_activeCategory === 'ml') {
        filtered = _inferences.filter(i => i.detection_method === 'ml');
      } else {
        filtered = _inferences.filter(i => i.category === _activeCategory);
      }
    }

    if (!filtered.length) {
      const msg = _inferences.length
        ? 'No inferences in this category.'
        : 'No inferences yet \u2014 data is being analysed.';
      feed.innerHTML = `<div class="inference-empty">${msg}</div>`;
      if (countEl) countEl.textContent = '';
      return;
    }

    const active = filtered.filter(i => !i.dismissed);
    if (countEl) countEl.textContent = active.length ? `(${active.length})` : '';

    feed.innerHTML = filtered.slice(0, 30).map(_buildCardHtml).join('');

    feed.onclick = (e) => {
      const card = e.target.closest('.inference-card');
      if (!card) return;
      const idAttr = cardDataAttr === 'data-inf-id' ? 'infId' :
                     cardDataAttr === 'data-di-inf-id' ? 'diInfId' : 'infId';
      const id = parseInt(card.dataset[idAttr] || card.id, 10);
      openDialog(id);
    };
  }

  async function _loadCategories() {
    if (_catsLoaded) return;
    const bar = document.getElementById(filtersId);
    if (!bar) {
      console.warn('[inference_feed] filter bar not found:', filtersId);
      return;
    }
    try {
      const res = await window.fetch('/api/inferences/categories');
      console.log('[inference_feed] categories status:', res.status);
      if (!res.ok) {
        console.error('[inference_feed] categories fetch failed with status', res.status);
        return;
      }
      const cats = await res.json();
      console.log('[inference_feed] categories loaded:', Object.keys(cats));
      for (const [key, label] of Object.entries(cats)) {
        if (bar.querySelector(`[data-category="${key}"]`)) continue;
        const btn = document.createElement('button');
        btn.className = 'inf-filter';
        btn.dataset.category = key;
        btn.textContent = label;
        bar.appendChild(btn);
      }
      if (!bar.querySelector('[data-category="ml"]')) {
        const mlBtn = document.createElement('button');
        mlBtn.className = 'inf-filter';
        mlBtn.dataset.category = 'ml';
        mlBtn.textContent = '\uD83E\uDDE0 ML';
        bar.appendChild(mlBtn);
      }
      bar.addEventListener('click', (e) => {
        const btn = e.target.closest('.inf-filter');
        if (!btn) return;
        bar.querySelectorAll('.inf-filter').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        _activeCategory = btn.dataset.category;
        _renderFeed();
      });
      _catsLoaded = true;
    } catch (err) {
      console.error('[inference_feed] _loadCategories failed:', err);
    }
  }

  async function load(url = '/api/inferences?limit=50') {
    try {
      await _loadCategories();
      const res = await window.fetch(url);
      if (!res.ok) {
        console.error('[inference_feed] inferences fetch failed:', res.status, url);
        return;
      }
      _inferences = await res.json();
      console.log('[inference_feed] load() got', _inferences.length, 'inferences');
      _renderFeed();
    } catch (err) {
      console.error('[inference_feed] load() failed:', err);
    }
  }

  function setInferences(rows) {
    _inferences = rows;
    console.log('[inference_feed] setInferences called with', rows.length, 'rows');
    if (!_catsLoaded) {
      console.log('[inference_feed] categories not yet loaded, triggering _loadCategories');
      _loadCategories();
    }
    _renderFeed();
  }

  function getInferences() {
    return _inferences;
  }

  return { load, setInferences, getInferences, _loadCategories };
}

// Expose for plain-script usage (detections_insights.js is loaded as a plain script
// via dynamic injection, not as an ES module).
window._createInferenceFeed = createInferenceFeed;
window._renderDetectionChip = renderDetectionChip;
