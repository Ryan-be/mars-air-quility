/**
 * detections_insights.js — Detections & Insights tab logic.
 */
'use strict';

// ── DI inference table state ─────────────────────────────────────────────────
let _diInferences    = [];
let _diActiveCategory = 'all';
let _diCatsLoaded    = false;

const DI = (function () {
  let _window = '24h';
  let _narratives = null;
  let _baselines  = null;
  let _sseSource  = null;
  let _initialised = false;

  const _DAY_NAMES = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const _HOURS     = Array.from({length:24}, (_,i) => i);

  const _SOURCE_META = {
    biological_offgas:   { emoji: '🧬', label: 'Biological Off-gassing',  colour: '#10b981' },
    chemical_offgassing: { emoji: '🧪', label: 'Chemical Off-gassing',    colour: '#8b5cf6' },
    cooking:             { emoji: '🍳', label: 'Cooking',                  colour: '#f97316' },
    combustion:          { emoji: '🔥', label: 'Combustion',               colour: '#ef4444' },
    external_pollution:  { emoji: '🌫️', label: 'External Pollution',       colour: '#6b7280' },
    cleaning_products:   { emoji: '🧹', label: 'Cleaning Products',        colour: '#06b6d4' },
    human_activity:      { emoji: '👤', label: 'Human Activity',           colour: '#a78bfa' },
    vehicle_exhaust:     { emoji: '🚗', label: 'Vehicle Exhaust',          colour: '#78716c' },
    mould_voc:           { emoji: '🍄', label: 'Mould / Fungal VOC',       colour: '#84cc16' },
    personal_care:       { emoji: '🧴', label: 'Personal Care Products',   colour: '#ec4899' },
  };
  // Backwards-compat alias used by fingerprint cards which reference _SOURCE_COLOURS
  const _SOURCE_COLOURS = Object.fromEntries(
    Object.entries(_SOURCE_META).map(([k, v]) => [k, v.colour])
  );

  const _METHOD_COLOURS = { rule:'#6366f1', statistical:'#f59e0b', ml:'#10b981' };
  const _METHOD_LABELS  = { rule:'Rule-based', statistical:'Statistical', ml:'ML Model' };

  // CORR_CHANNELS etc. are defined in charts_correlation.js which is loaded first
  // If not available, define fallbacks
  function _corrChannels() {
    return (typeof CORR_CHANNELS !== 'undefined') ? CORR_CHANNELS :
      ['tvoc_ppb','eco2_ppm','temperature_c','humidity_pct','pm1_ug_m3','pm25_ug_m3','pm10_ug_m3','co_ppb','no2_ppb','nh3_ppb'];
  }
  function _corrColours() { return (typeof CORR_COLOURS !== 'undefined') ? CORR_COLOURS : {}; }
  function _corrLabels()  { return (typeof CORR_LABELS  !== 'undefined') ? CORR_LABELS  : {}; }

  function _windowMs() { return {  '6h':6, '24h':24, '7d':168 }[_window] * 3600000; }
  function _range() {
    const end = new Date(); const start = new Date(end.getTime() - _windowMs());
    return { start: start.toISOString(), end: end.toISOString() };
  }

  function init() {
    if (_initialised) return;
    _initialised = true;
    load();
    _subscribeSSE();
  }

  function setWindow(w) {
    _window = w;
    document.querySelectorAll('.window-btn').forEach(b => b.classList.toggle('active', b.dataset.window === w));
    load();
  }

  function _showLoadingSkeletons() {
    const _skel = (id) => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = '<p class="di-loading">Loading\u2026</p>';
    };
    _skel('diPeriodSummary');
    _skel('diTrendIndicators');
    _skel('diLongestClean');
    _skel('diFingerprintCards');
    _skel('diModelCards');
    _skel('diDriftFlags');
  }

  async function load() {
    const { start, end } = _range();
    _showLoadingSkeletons();
    try {
      const [nResp, bResp] = await Promise.all([
        fetch(`/api/history/narratives?start=${start}&end=${end}`),
        fetch('/api/history/baselines'),
      ]);
      [_narratives, _baselines] = await Promise.all([nResp.json(), bResp.json()]);
      _render();
    } catch (e) { console.error('DI load error', e); }
  }

  function _render() {
    if (!_narratives) return;
    // Render priority sections first (fast, synchronous DOM writes)
    _renderPeriodSummary();
    _renderTrendIndicators();
    _renderLongestClean();
    _renderAttributionBreakdown();
    _renderFingerprintNarratives();
    _renderAnomalyModelNarratives();
    _renderPatternHeatmap();
    _renderDriftFlags();
    _renderInferenceTable();
    // Defer the normal bands chart — it makes its own fetch() for sensor history,
    // so schedule it after the main sections have painted.
    requestAnimationFrame(function () { _renderNormalBandsChart(); });
  }

  function _renderPeriodSummary() {
    const el = document.getElementById('diPeriodSummary');
    if (!el) return;
    const total = _narratives.total_events || 0;
    let summary = _narratives.period_summary || '';
    if (total > 0) {
      summary = summary.replace(/(\d+)\s*(detection\s+)?event(s?)/gi, function(match) {
        return '<a href="#diEventsList" class="di-events-link" onclick="document.getElementById(\'diEventsList\').scrollIntoView({behavior:\'smooth\'});return false;">' + match + '</a>';
      });
    }
    el.innerHTML = '<p>' + summary + '</p>';
  }
    function _renderTrendIndicators() {
    const el = document.getElementById('diTrendIndicators');
    if (!el) return;
    el.innerHTML = (_narratives.trend_indicators || []).map(function (t) {
      const arrow = t.direction === 'up' ? '↑' : '↓';
      const cc = { green:'trend-green', amber:'trend-amber', red:'trend-red' }[t.colour] || '';
      return `<div class="trend-tile ${cc}">
        <div class="trend-label">${t.label}</div>
        <div class="trend-value">${t.current_baseline != null ? t.current_baseline.toFixed(1) : '—'} ${t.unit}</div>
        <div class="trend-change">${arrow} ${t.pct_change.toFixed(1)}%</div>
        <div class="trend-sentence">${t.sentence}</div>
      </div>`;
    }).join('');
  }

  function _renderLongestClean() {
    const el = document.getElementById('diLongestClean');
    if (!el || _narratives.longest_clean_hours == null) return;
    const h = _narratives.longest_clean_hours;
    const full = h >= (_windowMs() / 3600000 - 0.1);
    if (full) {
      el.textContent = 'No events detected — the entire period was clean.';
    } else {
      const fmt = iso => new Date(iso).toLocaleString(undefined, { weekday:'short', hour:'2-digit', minute:'2-digit' });
      el.textContent = `Longest clean period: ${h.toFixed(1)}h (${fmt(_narratives.longest_clean_start)} → ${fmt(_narratives.longest_clean_end)}).`;
    }
  }

  function _sourceFriendlyLabel(id) {
    const m = _SOURCE_META[id];
    return m ? `${m.emoji} ${m.label}` : id;
  }

  function _renderAttributionBreakdown() {
    const breakdown = _narratives.attribution_breakdown || {};
    const sources = Object.keys(breakdown);
    const sentEl = document.getElementById('diDominantSentence');
    if (sentEl) sentEl.textContent = _narratives.dominant_source_sentence || '';
    const donutDiv = document.getElementById('diDonutChart');
    if (!donutDiv) return;

    // Ensure the wrapper has the flex layout; inject legend container once
    let wrapper = document.getElementById('diDonutWrapper');
    if (!wrapper) {
      // Wrap donutDiv in a flex row
      wrapper = document.createElement('div');
      wrapper.id = 'diDonutWrapper';
      wrapper.className = 'donut-wrapper';
      donutDiv.parentNode.insertBefore(wrapper, donutDiv);
      wrapper.appendChild(donutDiv);
      const legendDiv = document.createElement('div');
      legendDiv.id = 'diDonutLegend';
      legendDiv.className = 'donut-legend';
      wrapper.appendChild(legendDiv);
    }
    const legendDiv = document.getElementById('diDonutLegend');

    const totalEvents = _narratives.total_events || 0;

    if (!sources.length) {
      if (legendDiv) legendDiv.innerHTML = '';
      const methodBreakdown = _narratives.detection_method_breakdown || {};
      const methods = Object.keys(methodBreakdown);
      if (totalEvents > 0 && methods.length) {
        const subtitleEl = document.getElementById('diAttributionSubtitle');
        if (subtitleEl) subtitleEl.textContent = 'Breakdown by detection method (no source attribution yet)';
        Plotly.newPlot(
          donutDiv,
          [{ values: methods.map(m => methodBreakdown[m]),
             labels: methods.map(m => _METHOD_LABELS[m] || m),
             type: 'pie', hole: 0.5,
             marker: { colors: methods.map(m => _METHOD_COLOURS[m] || '#6b7280') },
             hovertemplate: '%{label}: %{value} events<extra></extra>',
             textinfo: 'percent',
             textposition: 'inside',
             insidetextorientation: 'horizontal' }],
          { showlegend: false, margin: {t:0,b:0,l:0,r:0}, paper_bgcolor: 'transparent', plot_bgcolor: 'transparent' },
          { displayModeBar: false, responsive: true }
        );
        if (legendDiv) {
          const total = methods.reduce((s, m) => s + methodBreakdown[m], 0) || 1;
          legendDiv.innerHTML = methods.map(m => {
            const col = _METHOD_COLOURS[m] || '#6b7280';
            const pct = Math.round(methodBreakdown[m] / total * 100);
            return `<div class="donut-legend-item">
              <span class="donut-legend-dot" style="background:${col}"></span>
              <span class="donut-legend-name">${_METHOD_LABELS[m] || m}</span>
              <span class="donut-legend-pct">${pct}%</span>
            </div>`;
          }).join('');
        }
      } else {
        const subtitleEl = document.getElementById('diAttributionSubtitle');
        if (subtitleEl) subtitleEl.textContent = '';
        Plotly.newPlot(donutDiv, [{ values:[1], labels:['No events'], type:'pie', hole:0.5, marker:{colors:['#d1d5db']}, hoverinfo:'none', textinfo:'label', textposition:'inside', insidetextorientation:'horizontal' }], { showlegend:false, margin:{t:0,b:0,l:0,r:0}, paper_bgcolor:'transparent', plot_bgcolor:'transparent' }, { displayModeBar:false });
      }
      return;
    }

    const subtitleEl = document.getElementById('diAttributionSubtitle');
    if (subtitleEl) subtitleEl.textContent = '';

    const colours  = sources.map(s => (_SOURCE_META[s] || {}).colour || '#6b7280');
    const labels   = sources.map(s => _sourceFriendlyLabel(s));
    const values   = sources.map(s => breakdown[s]);
    const total    = values.reduce((a, b) => a + b, 0) || 1;

    Plotly.newPlot(
      donutDiv,
      [{ values, labels, type: 'pie', hole: 0.5,
         marker: { colors: colours },
         hovertemplate: '%{label}: %{value} events (%{percent})<extra></extra>',
         textinfo: 'percent',
         textposition: 'inside',
         insidetextorientation: 'horizontal',
         domain: { x: [0, 1], y: [0, 1] } }],
      { showlegend: false,
        height: 390,
        margin: {t:4, b:4, l:4, r:4},
        paper_bgcolor: 'transparent',
        plot_bgcolor:  'transparent' },
      { displayModeBar: false, responsive: true }
    );

    if (legendDiv) {
      legendDiv.innerHTML = sources.map((s, i) => {
        const pct = Math.round(values[i] / total * 100);
        return `<div class="donut-legend-item">
          <span class="donut-legend-dot" style="background:${colours[i]}"></span>
          <span class="donut-legend-name">${labels[i]}</span>
          <span class="donut-legend-pct">${pct}%</span>
        </div>`;
      }).join('');
    }
  }
    function _renderFingerprintNarratives() {
    const el = document.getElementById('diFingerprintCards');
    if (!el) return;
    const fps = (_narratives.fingerprint_narratives || []).slice().sort((a,b) => b.event_count - a.event_count);
    el.innerHTML = fps.map(function (fp) {
      const colour = _SOURCE_COLOURS[fp.source_id] || '#6b7280';
      const badge = fp.event_count > 0 ? `<span class="badge-count">${fp.event_count} event${fp.event_count !== 1 ? 's' : ''}</span>` : '';
      const conf  = fp.event_count > 0 ? `<span class="fp-meta">Avg. confidence: ${Math.round(fp.avg_confidence*100)}%</span>` : '';
      return `<div class="fp-card" style="border-left:3px solid ${colour}">
        <div class="fp-header">${fp.emoji} <strong>${fp.label}</strong> ${badge}</div>
        ${conf}
        <p class="fp-narrative">${fp.narrative}</p>
      </div>`;
    }).join('');
  }

  function _renderAnomalyModelNarratives() {
    const models = _narratives.anomaly_model_narratives || [];
    const section = document.getElementById('diAnomalyModels');
    const el = document.getElementById('diModelCards');
    if (!section || !el) return;
    if (!models.length) { section.style.display = 'none'; return; }
    section.style.display = 'block';
    el.innerHTML = models.map(m => `<div class="model-card">
      <div class="model-header"><strong>${m.label}</strong> <span class="badge-count">${m.event_count} event${m.event_count !== 1 ? 's' : ''}</span></div>
      <p class="model-desc">${m.description}</p>
      <p class="model-narrative">${m.narrative}</p>
    </div>`).join('');
  }

  function _renderPatternHeatmap() {
    const heatDiv = document.getElementById('diHeatmap');
    const sentEl  = document.getElementById('diPatternSentence');
    if (!heatDiv) return;
    if (sentEl) sentEl.textContent = _narratives.pattern_sentence || '';
    const hm = _narratives.pattern_heatmap || {};
    const maxVal = Math.max(1, ...Object.values(hm));
    const z = _DAY_NAMES.map((_, d) => _HOURS.map(h => hm[`${d}_${h}`] || 0));
    const isLight = document.body.classList.contains('light');
    const heatLow = isLight ? '#f0f9ff' : '#1e293b';
    Plotly.newPlot(heatDiv, [{ z, x:_HOURS, y:_DAY_NAMES, type:'heatmap', colorscale:[[0,heatLow],[1,'#1e40af']], zmin:0, zmax:maxVal, showscale:false, hovertemplate:'%{y} %{x}:00 — %{z} event(s)<extra></extra>' }], { margin:{l:40,r:10,t:5,b:30}, xaxis:{tickvals:[0,3,6,9,12,15,18,21],ticktext:['0h','3h','6h','9h','12h','15h','18h','21h'],tickfont:{size:10},color:'var(--text-muted,#9ca3af)'}, yaxis:{tickfont:{size:10},color:'var(--text-muted,#9ca3af)'}, paper_bgcolor:'transparent', plot_bgcolor:'transparent' }, { displayModeBar:false, responsive:true });
  }

  async function _renderNormalBandsChart() {
    const chartDiv = document.getElementById('diBandsChart');
    if (!chartDiv || !_baselines) return;
    const { start, end } = _range();
    let sensorData;
    try {
      const resp = await fetch(`/api/history/sensor?start=${start}&end=${end}`);
      sensorData = await resp.json();
    } catch (e) { return; }

    const factor = _baselines.anomaly_threshold_factor || 0.25;
    const channels = _corrChannels();
    const colours = _corrColours();
    const labels = _corrLabels();
    const channelsToDraw = channels.filter(ch => _baselines[ch] != null);
    const traces = [];
    channelsToDraw.forEach(function (ch) {
      const baseline = _baselines[ch];
      const xs = sensorData.timestamps;
      const ys = sensorData.channels[ch] || [];
      const colour = colours[ch] || '#6b7280';
      const upper = baseline * (1 + factor);
      const lower = baseline * (1 - factor);
      traces.push({ x:xs, y:xs.map(()=>upper), mode:'lines', line:{width:0}, showlegend:false, hoverinfo:'none', name:ch+'_upper' });
      traces.push({ x:xs, y:xs.map(()=>lower), mode:'lines', fill:'tonexty', fillcolor:colour+'26', line:{width:0}, showlegend:false, hoverinfo:'none', name:ch+'_lower' });
      traces.push({ x:xs, y:ys, mode:'lines', name:labels[ch]||ch, line:{color:colour,width:1.5}, showlegend:false });
    });

    Plotly.newPlot(chartDiv, traces, { showlegend:false, margin:{l:40,r:20,t:10,b:40}, xaxis:{type:'date'}, yaxis:{zeroline:false}, paper_bgcolor:'transparent', plot_bgcolor:'transparent' }, { displayModeBar:false, responsive:true });

    // Build channel toggle chips
    _buildBandsToggles(channelsToDraw, colours, labels);
  }

  function _buildBandsToggles(channelsToDraw, colours, labels) {
    const container = document.getElementById('diToggles');
    if (!container) return;
    container.innerHTML = channelsToDraw.map(ch =>
      `<button class="channel-chip active" data-channel="${ch}" data-context="di" onclick="diToggleChip(this)">
        <span class="chip-dot" style="background:${colours[ch]||'#6b7280'}"></span>${labels[ch]||ch}
      </button>`
    ).join('');
  }

  async function _renderInferenceTable() {
    const section = document.getElementById('diEventsList');
    const feed    = document.getElementById('diInferenceFeed');
    const countEl = document.getElementById('diEventsCount');
    if (!section || !feed) return;

    section.style.display = 'block';
    feed.innerHTML = '<div class="inference-empty">Loading…</div>';

    const { start, end } = _range();
    try {
      await _diLoadCategories();
      const res = await fetch(`/api/inferences?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&limit=200`);
      if (!res.ok) throw new Error('fetch failed');
      const rows = await res.json();
      _diInferences = rows;
      _diRenderFeed(countEl, feed);
    } catch (e) {
      feed.innerHTML = '<div class="inference-empty">Could not load inferences.</div>';
    }
  }

  function _diRenderFeed(countEl, feed) {
    const SEV_CLS   = { info:'inf-info', warning:'inf-warning', critical:'inf-critical' };
    const SEV_LABEL = { info:'Info', warning:'Warning', critical:'Critical' };

    let filtered = _diInferences;
    if (_diActiveCategory && _diActiveCategory !== 'all') {
      filtered = _diInferences.filter(function (i) { return i.category === _diActiveCategory; });
    }

    if (!filtered.length) {
      const msg = _diInferences.length
        ? 'No inferences in this category.'
        : 'No inferences detected in this time window.';
      feed.innerHTML = '<div class="inference-empty">' + msg + '</div>';
      if (countEl) countEl.textContent = '';
      return;
    }

    const active = filtered.filter(function (i) { return !i.dismissed; });
    if (countEl) countEl.textContent = active.length ? '(' + active.length + ')' : '';

    const _chipTooltip =
      'Rule = a fixed threshold was crossed. ' +
      'Statistical = an unusual reading compared to this sensor\u2019s learned normal. ' +
      'ML = an unusual pattern across multiple sensors simultaneously.';

    feed.innerHTML = filtered.slice(0, 30).map(function (inf) {
      const sevCls = SEV_CLS[inf.severity] || 'inf-info';
      const sevLbl = SEV_LABEL[inf.severity] || inf.severity;
      const time = new Date(inf.created_at).toLocaleString(undefined, {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
      });
      const dm = inf.detection_method || 'rule';
      const chipCls = { rule: 'chip--rule', statistical: 'chip--statistical', ml: 'chip--ml' }[dm] || 'chip--rule';
      const chipLbl = { rule: 'Rule', statistical: 'Statistical', ml: 'ML' }[dm] || 'Rule';
      const chip = '<span class="chip ' + chipCls + '" title="' + _chipTooltip + '">' + chipLbl + ' <span class="chip-info">\u24d8</span></span>';
      const catCls = 'inf-cat-' + (inf.category || 'other');
      const dismissed = inf.dismissed ? ' dismissed' : '';
      return '<button class="inference-card ' + sevCls + ' ' + catCls + dismissed + '" data-di-inf-id="' + inf.id + '" title="Tap for details">' +
        '<div class="inf-card-left">' +
          '<div class="inf-card-badges">' + chip + ' <span class="inf-badge ' + sevCls + ' inf-badge-sm">' + sevLbl + '</span></div>' +
          '<span class="inf-card-type">' + inf.event_type.replace(/_/g, ' ') + '</span>' +
          '<span class="inf-card-summary">' + inf.title + '</span>' +
        '</div>' +
        '<div class="inf-card-right">' +
          '<span class="inf-card-time">' + time + '</span>' +
          '<span class="inf-card-conf">' + Math.round(inf.confidence * 100) + '%</span>' +
        '</div>' +
      '</button>';
    }).join('');

    feed.onclick = function (e) {
      const card = e.target.closest('.inference-card');
      if (!card) return;
      const id = parseInt(card.dataset.diInfId, 10);
      openInferenceDialog(id);
    };
  }

  async function _diLoadCategories() {
    if (_diCatsLoaded) return;
    try {
      const res = await fetch('/api/inferences/categories');
      if (!res.ok) return;
      const cats = await res.json();
      const bar = document.getElementById('diInferenceFilters');
      if (!bar) return;
      // Remove any previously added category buttons (keep "All")
      bar.querySelectorAll('.inf-filter:not([data-category="all"])').forEach(function (b) { b.remove(); });
      for (const [key, label] of Object.entries(cats)) {
        const btn = document.createElement('button');
        btn.className = 'inf-filter';
        btn.dataset.category = key;
        btn.textContent = label;
        bar.appendChild(btn);
      }
      bar.addEventListener('click', function (e) {
        const btn = e.target.closest('.inf-filter');
        if (!btn) return;
        bar.querySelectorAll('.inf-filter').forEach(function (b) { b.classList.remove('active'); });
        btn.classList.add('active');
        _diActiveCategory = btn.dataset.category;
        const feed    = document.getElementById('diInferenceFeed');
        const countEl = document.getElementById('diEventsCount');
        if (feed && countEl !== undefined) _diRenderFeed(countEl, feed);
      });
      _diCatsLoaded = true;
    } catch (e) { /* categories not available */ }
  }

    function _renderDriftFlags() {
    const el = document.getElementById('diDriftFlags');
    if (!el) return;
    const flags = _narratives.drift_flags || [];
    if (!flags.length) { el.style.display = 'none'; return; }
    el.style.display = 'block';
    el.innerHTML = `<div class="di-card drift-section">
      <h3>Sensor Drift Flags <span class="info-icon" title="Baseline shift vs 7 days ago.">ⓘ</span></h3>
      ${flags.map(f => `<div class="drift-card">⚠ <strong>${f.channel}</strong> — ${f.message} <span class="drift-shift">${f.direction==='up'?'↑':'↓'} ${f.shift_pct}%</span></div>`).join('')}
    </div>`;
  }

  function _subscribeSSE() {
    if (_sseSource) return;
    _sseSource = new EventSource('/api/stream');
    _sseSource.addEventListener('inference_fired', function () { load(); });
  }

  return { init, setWindow, load };
})();

// ── Global inference dialog opener (used by DI tab cards) ────────────────────
// On the dashboard page dashboard.js defines its own _openInferenceDialog.
// On the history page this function is the sole dialog opener.
function openInferenceDialog(id) {
  const inf = _diInferences.find(function (i) { return i.id === id; });
  if (!inf) return;
  const dialog = document.getElementById('inferenceDialog');
  if (!dialog) return;

  const SEV_CLS   = { info:'inf-info', warning:'inf-warning', critical:'inf-critical' };
  const SEV_LABEL = { info:'Info', warning:'Warning', critical:'Critical' };
  const _chipTooltip =
    'Rule = a fixed threshold was crossed. ' +
    'Statistical = an unusual reading compared to this sensor\u2019s learned normal. ' +
    'ML = an unusual pattern across multiple sensors simultaneously.';

  document.getElementById('infTitle').textContent = inf.title;

  const badge = document.getElementById('infSeverity');
  badge.textContent = SEV_LABEL[inf.severity] || inf.severity;
  badge.className = 'inf-badge ' + (SEV_CLS[inf.severity] || '');

  const metaEl = document.getElementById('infMeta');
  if (metaEl) {
    const chipEl = metaEl.querySelector('.inf-detection-chip');
    if (chipEl) {
      const dm = inf.detection_method || 'rule';
      const chipCls = { rule:'chip--rule', statistical:'chip--statistical', ml:'chip--ml' }[dm] || 'chip--rule';
      const chipLbl = { rule:'Rule', statistical:'Statistical', ml:'ML' }[dm] || 'Rule';
      chipEl.innerHTML = '<span class="chip ' + chipCls + '" title="' + _chipTooltip + '">' + chipLbl + ' <span class="chip-info">\u24d8</span></span>';
    }
  }

  document.getElementById('infTime').textContent = new Date(inf.created_at).toLocaleString();
  document.getElementById('infConfidence').textContent = Math.round(inf.confidence * 100) + '% confidence';
  document.getElementById('infDescription').textContent = inf.description;

  // Attribution badge
  const attrEl = document.getElementById('infAttribution');
  if (attrEl) {
    const src  = (inf.evidence && inf.evidence.attribution_source)     || inf.attribution_source;
    const conf = (inf.evidence && inf.evidence.attribution_confidence) || inf.attribution_confidence;
    if (src && conf != null) {
      attrEl.innerHTML = '<span class="attr-badge" title="Attribution engine matched this event to a known source fingerprint.">' +
        src.replace(/_/g, ' ') + ' &mdash; ' + Math.round(conf * 100) + '% match</span>';
      attrEl.style.display = '';
    } else {
      attrEl.innerHTML = '';
      attrEl.style.display = 'none';
    }
  }

  document.getElementById('infAction').textContent = inf.action || 'No specific action needed.';

  function _renderFeatureVectorEvidence(featureVector) {
    const mapping = [
      ['tvoc_current', 'TVOC', 'ppb'],
      ['tvoc_baseline', 'TVOC baseline', 'ppb'],
      ['tvoc_slope_1m', 'TVOC slope 1m', 'ppb/min'],
      ['tvoc_slope_5m', 'TVOC slope 5m', 'ppb/min'],
      ['tvoc_slope_30m', 'TVOC slope 30m', 'ppb/min'],
      ['tvoc_elevated_minutes', 'TVOC elevated minutes', 'min'],
      ['tvoc_peak_ratio', 'TVOC peak ratio', '×'],
      ['tvoc_is_declining', 'TVOC declining', ''],
      ['tvoc_decay_rate', 'TVOC decay rate', 'ppb/min'],
      ['tvoc_pulse_detected', 'TVOC pulse detected', ''],
      ['eco2_current', 'eCO₂', 'ppm'],
      ['eco2_baseline', 'eCO₂ baseline', 'ppm'],
      ['eco2_slope_1m', 'eCO₂ slope 1m', 'ppm/min'],
      ['eco2_slope_5m', 'eCO₂ slope 5m', 'ppm/min'],
      ['eco2_slope_30m', 'eCO₂ slope 30m', 'ppm/min'],
      ['eco2_elevated_minutes', 'eCO₂ elevated minutes', 'min'],
      ['eco2_peak_ratio', 'eCO₂ peak ratio', '×'],
      ['eco2_is_declining', 'eCO₂ declining', ''],
      ['eco2_decay_rate', 'eCO₂ decay rate', 'ppm/min'],
      ['eco2_pulse_detected', 'eCO₂ pulse detected', ''],
      ['temperature_current', 'Temperature', '°C'],
      ['temperature_baseline', 'Temperature baseline', '°C'],
      ['temperature_slope_1m', 'Temperature slope 1m', '°C/min'],
      ['temperature_slope_5m', 'Temperature slope 5m', '°C/min'],
      ['temperature_slope_30m', 'Temperature slope 30m', '°C/min'],
      ['temperature_elevated_minutes', 'Temperature elevated minutes', 'min'],
      ['temperature_peak_ratio', 'Temperature peak ratio', '×'],
      ['temperature_is_declining', 'Temperature declining', ''],
      ['temperature_decay_rate', 'Temperature decay rate', '°C/min'],
      ['temperature_pulse_detected', 'Temperature pulse detected', ''],
      ['humidity_current', 'Humidity', '%'],
      ['humidity_baseline', 'Humidity baseline', '%'],
      ['humidity_slope_1m', 'Humidity slope 1m', '%/min'],
      ['humidity_slope_5m', 'Humidity slope 5m', '%/min'],
      ['humidity_slope_30m', 'Humidity slope 30m', '%/min'],
      ['humidity_elevated_minutes', 'Humidity elevated minutes', 'min'],
      ['humidity_peak_ratio', 'Humidity peak ratio', '×'],
      ['humidity_is_declining', 'Humidity declining', ''],
      ['humidity_decay_rate', 'Humidity decay rate', '%/min'],
      ['humidity_pulse_detected', 'Humidity pulse detected', ''],
      ['pm1_current', 'PM1', 'µg/m³'],
      ['pm1_baseline', 'PM1 baseline', 'µg/m³'],
      ['pm1_slope_1m', 'PM1 slope 1m', 'µg/m³/min'],
      ['pm1_slope_5m', 'PM1 slope 5m', 'µg/m³/min'],
      ['pm1_slope_30m', 'PM1 slope 30m', 'µg/m³/min'],
      ['pm1_elevated_minutes', 'PM1 elevated minutes', 'min'],
      ['pm1_peak_ratio', 'PM1 peak ratio', '×'],
      ['pm1_is_declining', 'PM1 declining', ''],
      ['pm1_decay_rate', 'PM1 decay rate', 'µg/m³/min'],
      ['pm1_pulse_detected', 'PM1 pulse detected', ''],
      ['pm25_current', 'PM2.5', 'µg/m³'],
      ['pm25_baseline', 'PM2.5 baseline', 'µg/m³'],
      ['pm25_slope_1m', 'PM2.5 slope 1m', 'µg/m³/min'],
      ['pm25_slope_5m', 'PM2.5 slope 5m', 'µg/m³/min'],
      ['pm25_slope_30m', 'PM2.5 slope 30m', 'µg/m³/min'],
      ['pm25_elevated_minutes', 'PM2.5 elevated minutes', 'min'],
      ['pm25_peak_ratio', 'PM2.5 peak ratio', '×'],
      ['pm25_is_declining', 'PM2.5 declining', ''],
      ['pm25_decay_rate', 'PM2.5 decay rate', 'µg/m³/min'],
      ['pm25_pulse_detected', 'PM2.5 pulse detected', ''],
      ['pm10_current', 'PM10', 'µg/m³'],
      ['pm10_baseline', 'PM10 baseline', 'µg/m³'],
      ['pm10_slope_1m', 'PM10 slope 1m', 'µg/m³/min'],
      ['pm10_slope_5m', 'PM10 slope 5m', 'µg/m³/min'],
      ['pm10_slope_30m', 'PM10 slope 30m', 'µg/m³/min'],
      ['pm10_elevated_minutes', 'PM10 elevated minutes', 'min'],
      ['pm10_peak_ratio', 'PM10 peak ratio', '×'],
      ['pm10_is_declining', 'PM10 declining', ''],
      ['pm10_decay_rate', 'PM10 decay rate', 'µg/m³/min'],
      ['pm10_pulse_detected', 'PM10 pulse detected', ''],
      ['co_current', 'CO (resistance)', 'Ω'],
      ['co_baseline', 'CO baseline', 'Ω'],
      ['co_slope_1m', 'CO slope 1m', 'Ω/min'],
      ['co_slope_5m', 'CO slope 5m', 'Ω/min'],
      ['co_slope_30m', 'CO slope 30m', 'Ω/min'],
      ['co_elevated_minutes', 'CO elevated minutes', 'min'],
      ['co_peak_ratio', 'CO peak ratio', '×'],
      ['co_is_declining', 'CO declining', ''],
      ['co_decay_rate', 'CO decay rate', 'Ω/min'],
      ['co_pulse_detected', 'CO pulse detected', ''],
      ['no2_current', 'NO₂ (resistance)', 'Ω'],
      ['no2_baseline', 'NO₂ baseline', 'Ω'],
      ['no2_slope_1m', 'NO₂ slope 1m', 'Ω/min'],
      ['no2_slope_5m', 'NO₂ slope 5m', 'Ω/min'],
      ['no2_slope_30m', 'NO₂ slope 30m', 'Ω/min'],
      ['no2_elevated_minutes', 'NO₂ elevated minutes', 'min'],
      ['no2_peak_ratio', 'NO₂ peak ratio', '×'],
      ['no2_is_declining', 'NO₂ declining', ''],
      ['no2_decay_rate', 'NO₂ decay rate', 'Ω/min'],
      ['no2_pulse_detected', 'NO₂ pulse detected', ''],
      ['nh3_current', 'NH₃ (resistance)', 'Ω'],
      ['nh3_baseline', 'NH₃ baseline', 'Ω'],
      ['nh3_slope_1m', 'NH₃ slope 1m', 'Ω/min'],
      ['nh3_slope_5m', 'NH₃ slope 5m', 'Ω/min'],
      ['nh3_slope_30m', 'NH₃ slope 30m', 'Ω/min'],
      ['nh3_elevated_minutes', 'NH₃ elevated minutes', 'min'],
      ['nh3_peak_ratio', 'NH₃ peak ratio', '×'],
      ['nh3_is_declining', 'NH₃ declining', ''],
      ['nh3_decay_rate', 'NH₃ decay rate', 'Ω/min'],
      ['nh3_pulse_detected', 'NH₃ pulse detected', ''],
      ['nh3_lag_behind_tvoc_seconds', 'NH₃ lag behind TVOC', 's'],
      ['pm25_correlated_with_tvoc', 'PM2.5 correlated with TVOC', ''],
      ['co_correlated_with_tvoc', 'CO correlated with TVOC', ''],
      ['vpd_kpa', 'VPD', 'kPa'],
    ];

    const rows = mapping.map(([key, label, unit]) => {
      const value = featureVector[key];
      if (value == null) return null;
      const formatted = typeof value === 'boolean' ? (value ? 'yes' : 'no') : value;
      const suffix = unit ? ` ${unit}` : '';
      return `<div class="inf-ev-row"><span class="fd-label">${label}</span><span class="fd-value">${formatted}${suffix}</span></div>`;
    }).filter(Boolean);

    return rows.join('');
  }

  function _renderRangeReadingsEvidence(evidence) {
    const readings = Array.isArray(evidence.readings) ? evidence.readings : [];
    if (!readings.length) return '';

    const latest = readings[readings.length - 1];
    const mapping = [
      ['tvoc_ppb', 'TVOC', 'ppb', 'tvoc_baseline'],
      ['eco2_ppm', 'eCO₂', 'ppm', 'eco2_baseline'],
      ['temperature_c', 'Temperature', '°C', 'temperature_baseline'],
      ['humidity_pct', 'Humidity', '%', 'humidity_baseline'],
      ['pm1_ug_m3', 'PM1', 'µg/m³', 'pm1_baseline'],
      ['pm25_ug_m3', 'PM2.5', 'µg/m³', 'pm25_baseline'],
      ['pm10_ug_m3', 'PM10', 'µg/m³', 'pm10_baseline'],
      ['co_ppb', 'CO (resistance)', 'Ω', 'co_baseline'],
      ['no2_ppb', 'NO₂ (resistance)', 'Ω', 'no2_baseline'],
      ['nh3_ppb', 'NH₃ (resistance)', 'Ω', 'nh3_baseline'],
    ];

    const summary = `<div class="inf-ev-row"><span class="fd-label">Selected range</span><span class="fd-value">${readings.length} readings from ${new Date(readings[0].timestamp).toLocaleString()} to ${new Date(latest.timestamp).toLocaleString()}</span></div>`;
    const rows = mapping.map(([key, label, unit, baselineKey]) => {
      const value = latest[key];
      if (value == null) return null;
      const baseline = evidence.feature_vector ? evidence.feature_vector[baselineKey] : null;
      const status = baseline != null ? (value > baseline ? 'above baseline' : value < baseline ? 'below baseline' : 'at baseline') : '';
      const statusText = status ? ` (${status})` : '';
      const baselineText = baseline != null ? ` / baseline ${baseline} ${unit}` : '';
      return `<div class="inf-ev-row"><span class="fd-label">${label}</span><span class="fd-value">${value} ${unit}${baselineText}${statusText}</span></div>`;
    }).filter(Boolean);

    return summary + rows.join('');
  }

  // Evidence section
  const evEl  = document.getElementById('infEvidence');
  const thSec = document.getElementById('infThresholdsSection');
  const thGrid = document.getElementById('infThresholds');
  if (inf.evidence && typeof inf.evidence === 'object') {
    const snapshot   = inf.evidence.sensor_snapshot;
    const thresholds = inf.evidence._thresholds;
    if (Array.isArray(snapshot) && snapshot.length > 0) {
      const TREND_ARROW = { rising:'↑', falling:'↓', stable:'→' };
      const BAND_CLS    = { high:'ev-bad', elevated:'ev-warn', normal:'ev-good', unknown:'' };
      evEl.innerHTML = snapshot.map(function (s) {
        const arrow = TREND_ARROW[s.trend] || '→';
        const cls   = BAND_CLS[s.ratio_band] || '';
        const ratio = s.ratio != null ? '<span class="ev-ratio">' + s.ratio + '\u00d7 normal</span>' : '';
        return '<div class="inf-ev-row ' + cls + '">' +
          '<span class="fd-label">' + s.label + '</span>' +
          '<span class="fd-value">' + s.value + ' ' + s.unit + ' <span class="ev-trend">' + arrow + '</span></span>' +
          ratio + '</div>';
      }).join('');
    } else if (inf.evidence.readings && Array.isArray(inf.evidence.readings) && inf.evidence.readings.length > 0) {
      evEl.innerHTML = _renderRangeReadingsEvidence(inf.evidence);
      if (inf.evidence.feature_vector && typeof inf.evidence.feature_vector === 'object') {
        evEl.innerHTML += '<div class="inf-ev-subtitle">Feature vector</div>' +
          _renderFeatureVectorEvidence(inf.evidence.feature_vector);
      }
    } else if (inf.evidence.feature_vector && typeof inf.evidence.feature_vector === 'object') {
      const featureHtml = _renderFeatureVectorEvidence(inf.evidence.feature_vector);
      evEl.innerHTML = featureHtml || 'No detailed evidence available.';
    } else {
      const entries = Object.entries(inf.evidence).filter(function ([k]) {
        return k !== '_thresholds' && k !== 'sensor_snapshot' && k !== 'model_id';
      });
      evEl.innerHTML = entries.map(function ([k, v]) {
        return '<div class="inf-ev-row"><span class="fd-label">' + k.replace(/_/g, ' ') + '</span><span class="fd-value">' + v + '</span></div>';
      }).join('') || 'No detailed evidence available.';
    }
    if (thresholds && typeof thresholds === 'object' && Object.keys(thresholds).length) {
      thSec.style.display = '';
      thSec.removeAttribute('open');
      thGrid.innerHTML = Object.entries(thresholds).map(function ([k, th]) {
        const tag = th.is_custom
          ? '<span class="inf-th-custom">custom</span>'
          : '<span class="inf-th-default">default</span>';
        return '<div class="inf-th-row"><span class="inf-th-label">' + (th.label || k.replace(/_/g, ' ')) + '</span>' +
          '<span class="inf-th-val">' + th.value + ' ' + (th.unit || '') + ' ' + tag + '</span></div>';
      }).join('');
    } else {
      thSec.style.display = 'none';
    }
  } else {
    evEl.textContent = 'No detailed evidence available.';
    if (thSec) thSec.style.display = 'none';
  }

  // Annotation
  const annoSec = document.getElementById('infAnnotationSection');
  if (annoSec) {
    if (inf.annotation) {
      annoSec.style.display = '';
      document.getElementById('infAnnotationText').textContent = inf.annotation;
    } else {
      annoSec.style.display = 'none';
    }
  }

  // Notes
  document.getElementById('infNotes').value = inf.user_notes || '';
  document.getElementById('infSaveNote').onclick = async function () {
    const notes = document.getElementById('infNotes').value;
    try {
      await fetch('/api/inferences/' + id + '/notes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes }),
      });
      inf.user_notes = notes;
    } catch (e) { /* ignore */ }
  };

  // Tags
  const tagsList = document.getElementById('infTagsList');
  if (tagsList && inf.tags) {
    tagsList.innerHTML = inf.tags.map(t => `<span class="tag-chip">${t.tag}</span>`).join(' ');
  } else if (tagsList) {
    tagsList.innerHTML = '';
  }
  document.getElementById('infAddTag').onclick = async function () {
    const select = document.getElementById('infTagSelect');
    const tag = select.value;
    if (!tag) return;
    try {
      await fetch('/api/inferences/' + id + '/tags', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag }),
      });
      // Refresh tags
      const res = await fetch('/api/inferences/' + id + '/tags');
      const newTags = await res.json();
      if (tagsList) {
        tagsList.innerHTML = newTags.map(t => `<span class="tag-chip">${t.tag}</span>`).join(' ');
      }
      inf.tags = newTags;
      select.value = '';
    } catch (e) { /* ignore */ }
  };

  // Sparkline (suppress if loadSparkline not available on this page)
  const sparkline = document.getElementById('infSparkline');
  if (sparkline) sparkline.style.display = 'none';
  if (typeof loadSparkline === 'function') {
    loadSparkline(inf.id, inf.created_at);
  }

  dialog.showModal();
  // Resize the sparkline chart after the dialog is visible so Plotly measures
  // the correct dimensions (it renders before the dialog is fully painted).
  setTimeout(function () {
    var chartDiv = document.getElementById('infSparklineChart');
    if (chartDiv && window.Plotly) Plotly.Plots.resize(chartDiv);
  }, 50);
  dialog.onclick = function (e) { if (e.target === dialog) dialog.close(); };
}

function diSetWindow(w) { DI.setWindow(w); }
function diToggleChip(btn) {
  btn.classList.toggle('active');
  const chartDiv = document.getElementById('diBandsChart');
  if (!chartDiv || !chartDiv.data) return;
  const channels = (typeof CORR_CHANNELS !== 'undefined') ? CORR_CHANNELS :
    ['tvoc_ppb','eco2_ppm','temperature_c','humidity_pct','pm1_ug_m3','pm25_ug_m3','pm10_ug_m3','co_ppb','no2_ppb','nh3_ppb'];
  const active = new Set(Array.from(document.querySelectorAll('[data-context="di"].channel-chip.active')).map(c => c.dataset.channel));
  channels.forEach(function (ch, i) {
    const vis = active.has(ch);
    Plotly.restyle(chartDiv, { visible:[vis,vis,vis] }, [i*3, i*3+1, i*3+2]);
  });
}
