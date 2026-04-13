/**
 * timeline_detail_panel.js — Slide-out detail panel for inference timeline events.
 *
 * Usage:
 *   const panel = createTimelineDetailPanel('diDetailPanel', 'diDetailTitle', 'diDetailBody');
 *   panel.show(inf);
 *   panel.hide();
 */
'use strict';

window.createTimelineDetailPanel = function createTimelineDetailPanel(panelId, titleId, bodyId) {
  const SEV_STATUS = { critical: 'critical', warning: 'serious', info: 'caution' };
  const SEV_LABEL  = { critical: 'Critical', warning: 'Warning', info: 'Info' };
  const METHOD_LBL = { rule: 'Rule-based', statistical: 'Statistical', ml: 'ML Model' };

  function _panel()  { return document.getElementById(panelId); }
  function _titleEl(){ return document.getElementById(titleId); }
  function _bodyEl() { return document.getElementById(bodyId); }

  function _fmt(isoStr) {
    if (!isoStr) return '—';
    try {
      return new Date(isoStr).toLocaleString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        timeZoneName: 'short'
      });
    } catch (_) { return isoStr; }
  }

  function _duration(inf) {
    const s = new Date(inf.created_at);
    let mins;
    if (inf.category === 'summary' || inf.category === 'report') {
      mins = 60;
    } else if (inf.detection_method === 'ml' && inf.event_type && inf.event_type.includes('occupancy')) {
      mins = 8;
    } else if (inf.detection_method === 'ml') {
      mins = 2;
    } else if (inf.detection_method === 'statistical') {
      mins = 20;
    } else {
      mins = 10;
    }
    if (mins >= 60) return `${mins / 60}h`;
    return `~${mins} min`;
  }

  function _confBar(confidence) {
    const pct = Math.round((confidence || 0) * 100);
    const cls = pct >= 80 ? 'conf-high' : pct >= 50 ? 'conf-mid' : 'conf-low';
    return `<div class="tl-conf-bar-wrap">
      <div class="tl-conf-bar ${cls}" style="width:${pct}%"></div>
    </div>`;
  }

  function _evidenceHtml(inf) {
    // Try sensor_snapshot first
    let evidence = null;
    try {
      if (inf.evidence && typeof inf.evidence === 'string') {
        evidence = JSON.parse(inf.evidence);
      } else if (inf.evidence && typeof inf.evidence === 'object') {
        evidence = inf.evidence;
      }
    } catch (_) { /* ignore */ }

    if (evidence && evidence.sensor_snapshot && typeof evidence.sensor_snapshot === 'object') {
      const rows = Object.entries(evidence.sensor_snapshot).slice(0, 8).map(([k, v]) =>
        `<div class="tl-ev-row"><span class="tl-ev-key">${k.replace(/_/g, ' ')}</span><span class="tl-ev-val">${v}</span></div>`
      ).join('');
      return rows || '<span class="tl-ev-empty">No snapshot data.</span>';
    }

    // Fallback: key fields from inf
    const fields = [
      ['Category',   inf.category],
      ['Event type', inf.event_type ? inf.event_type.replace(/_/g, ' ') : null],
      ['Confidence', inf.confidence != null ? `${Math.round(inf.confidence * 100)}%` : null],
    ].filter(([, v]) => v != null);

    if (evidence && typeof evidence === 'object') {
      Object.entries(evidence).slice(0, 5).forEach(([k, v]) => {
        if (k !== 'sensor_snapshot') fields.push([k.replace(/_/g, ' '), String(v)]);
      });
    }

    if (!fields.length) return '<span class="tl-ev-empty">No evidence available.</span>';
    return fields.map(([k, v]) =>
      `<div class="tl-ev-row"><span class="tl-ev-key">${k}</span><span class="tl-ev-val">${v}</span></div>`
    ).join('');
  }

  function show(inf) {
    const panelEl = _panel();
    const titleEl = _titleEl();
    const bodyEl  = _bodyEl();
    if (!panelEl || !bodyEl) return;

    if (titleEl) titleEl.textContent = inf.title || 'Event Detail';

    const sevStatus = SEV_STATUS[inf.severity] || 'caution';
    const sevLabel  = SEV_LABEL[inf.severity]  || (inf.severity || 'Info');
    const methLabel = METHOD_LBL[inf.detection_method] || (inf.detection_method || '—');
    const pct       = Math.round((inf.confidence || 0) * 100);

    bodyEl.innerHTML = `
      <div class="tl-dp-badges">
        <span class="tl-dp-badge tl-dp-badge--${sevStatus}">${sevLabel}</span>
        <span class="tl-dp-chip">${methLabel}</span>
      </div>

      <div class="tl-dp-section">
        <div class="tl-dp-section-title">What was detected</div>
        <div class="tl-dp-desc">${inf.description || '—'}</div>
      </div>

      <div class="tl-dp-section">
        <div class="tl-dp-section-title">Temporal</div>
        <div class="tl-dp-grid">
          <div class="tl-dp-grid-item">
            <div class="tl-dp-grid-label">Start</div>
            <div class="tl-dp-grid-value">${_fmt(inf.created_at)}</div>
          </div>
          <div class="tl-dp-grid-item">
            <div class="tl-dp-grid-label">Duration</div>
            <div class="tl-dp-grid-value">${_duration(inf)}</div>
          </div>
          <div class="tl-dp-grid-item">
            <div class="tl-dp-grid-label">Source</div>
            <div class="tl-dp-grid-value">${inf.event_type ? inf.event_type.replace(/_/g, ' ') : '—'}</div>
          </div>
          <div class="tl-dp-grid-item">
            <div class="tl-dp-grid-label">Confidence</div>
            <div class="tl-dp-grid-value">${pct}%</div>
          </div>
        </div>
        <div class="tl-dp-conf-label">${pct}% confidence</div>
        ${_confBar(inf.confidence)}
      </div>

      <div class="tl-dp-section">
        <div class="tl-dp-section-title">Evidence</div>
        <div class="tl-dp-evidence">${_evidenceHtml(inf)}</div>
      </div>

      <div class="tl-dp-section">
        <div class="tl-dp-section-title">Recommended action</div>
        <div class="tl-dp-action">${inf.action || '—'}</div>
      </div>
    `;

    panelEl.classList.add('open');
  }

  function hide() {
    const panelEl = _panel();
    if (panelEl) panelEl.classList.remove('open');
  }

  return { show, hide };
};
