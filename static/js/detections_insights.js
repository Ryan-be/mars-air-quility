/**
 * detections_insights.js — Detections & Insights tab logic.
 */
'use strict';

const DI = (function () {
  let _window = '24h';
  let _narratives = null;
  let _baselines  = null;
  let _sseSource  = null;
  let _initialised = false;

  const _DAY_NAMES = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const _HOURS     = Array.from({length:24}, (_,i) => i);

  const _SOURCE_COLOURS = {
    biological_offgas:'#22c55e', chemical_offgassing:'#a855f7',
    cooking:'#f97316', combustion:'#ef4444', external_pollution:'#6b7280',
  };

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
    _renderEventsList();
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

  function _renderAttributionBreakdown() {
    const breakdown = _narratives.attribution_breakdown || {};
    const sources = Object.keys(breakdown);
    const sentEl = document.getElementById('diDominantSentence');
    if (sentEl) sentEl.textContent = _narratives.dominant_source_sentence || '';
    const donutDiv = document.getElementById('diDonutChart');
    if (!donutDiv) return;

    const totalEvents = _narratives.total_events || 0;

    if (!sources.length) {
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
             textinfo: 'label' }],
          { showlegend: false, margin: {t:0,b:0,l:0,r:0}, paper_bgcolor: 'transparent', plot_bgcolor: 'transparent' },
          { displayModeBar: false, responsive: true }
        );
      } else {
        const subtitleEl = document.getElementById('diAttributionSubtitle');
        if (subtitleEl) subtitleEl.textContent = '';
        Plotly.newPlot(donutDiv, [{ values:[1], labels:["No events"], type:"pie", hole:0.5, marker:{colors:["#d1d5db"]}, hoverinfo:"none", textinfo:"label" }], { showlegend:false, margin:{t:0,b:0,l:0,r:0}, paper_bgcolor:"transparent", plot_bgcolor:"transparent" }, { displayModeBar:false });
      }
      return;
    }
    const subtitleEl = document.getElementById('diAttributionSubtitle');
    if (subtitleEl) subtitleEl.textContent = '';
    Plotly.newPlot(donutDiv, [{ values:sources.map(s=>breakdown[s]), labels:sources, type:"pie", hole:0.5, marker:{colors:sources.map(s=>_SOURCE_COLOURS[s]||"#6b7280")}, hovertemplate:"%{label}: %{value} events<extra></extra>", textinfo:"label" }], { showlegend:false, margin:{t:0,b:0,l:0,r:0}, paper_bgcolor:"transparent", plot_bgcolor:"transparent" }, { displayModeBar:false, responsive:true });
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

  function _renderEventsList() {
    const section = document.getElementById('diEventsList');
    const countEl = document.getElementById('diEventsCount');
    const scrollEl = document.getElementById('diEventsScroll');
    if (!section || !scrollEl) return;
    const inferences = _narratives.inferences || [];
    if (!inferences.length) { section.style.display = 'none'; return; }
    section.style.display = 'block';
    if (countEl) countEl.textContent = inferences.length;

    const SEV_COLOUR = { critical:'#ef4444', high:'#f97316', medium:'#f59e0b', low:'#22c55e', info:'#6b7280' };
    const SEV_BG     = { critical:'#fef2f2', high:'#fff7ed', medium:'#fffbeb', low:'#f0fdf4', info:'#f9fafb' };

    scrollEl.innerHTML = inferences.map(function (inf) {
      const sev = (inf.severity || 'info').toLowerCase();
      const method = inf.detection_method || 'rule';
      const methodLabel = _METHOD_LABELS[method] || method;
      const methodColour = _METHOD_COLOURS[method] || '#6b7280';
      const sevColour = SEV_COLOUR[sev] || '#6b7280';
      const sevBg = SEV_BG[sev] || '#f9fafb';
      const ts = inf.created_at ? new Date(inf.created_at).toLocaleString(undefined, {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
      const conf = inf.confidence != null ? '<span class="ev-conf">' + Math.round(inf.confidence * 100) + '%</span>' : '';
      const canOpen = typeof openInferenceDialog === 'function' && inf.id;
      const clickAttr = canOpen ? 'onclick="openInferenceDialog(' + inf.id + ')" style="cursor:pointer;"' : '';
      return '<div class="ev-card" ' + clickAttr + '>' +
        '<span class="ev-sev-badge" style="background:' + sevBg + ';color:' + sevColour + ';border:1px solid ' + sevColour + ';">' + sev + '</span>' +
        '<span class="ev-method-chip" style="background:' + methodColour + '20;color:' + methodColour + ';border:1px solid ' + methodColour + '40;">' + methodLabel + '</span>' +
        '<span class="ev-title">' + (inf.title || inf.event_type || 'Event') + '</span>' +
        '<span class="ev-ts">' + ts + '</span>' +
        conf + '</div>';
    }).join('');
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
