/**
 * sparkline.js — Inference sparkline chart.
 * Loaded as a plain (non-module) script on both the dashboard and history pages
 * so that loadSparkline() is available as a global function.
 */
'use strict';

var _CHANNEL_COLOURS = {
  tvoc_ppb:'#8b5cf6', eco2_ppm:'#06b6d4', temperature_c:'#f97316', humidity_pct:'#3b82f6',
  pm1_ug_m3:'#84cc16', pm25_ug_m3:'#22c55e', pm10_ug_m3:'#a3e635',
  co_ppb:'#ef4444', no2_ppb:'#f59e0b', nh3_ppb:'#ec4899',
};

async function loadSparkline(inferenceId, inferenceAt) {
  var container = document.getElementById('infSparkline');
  var chartDiv  = document.getElementById('infSparklineChart');
  var loading   = document.getElementById('infSparklineLoading');
  var error     = document.getElementById('infSparklineError');
  if (!container) return;
  container.style.display = 'block';
  loading.style.display = 'block';
  chartDiv.style.display = 'none';
  error.style.display = 'none';
  try {
    var resp = await fetch('/api/inferences/' + inferenceId + '/sparkline');
    if (!resp.ok) throw new Error('fetch failed');
    var data = await resp.json();
    console.log('sparkline data:', data);
    loading.style.display = 'none';
    if (!data.triggering_channels || data.triggering_channels.length === 0) {
      container.style.display = 'none';
      return;
    }
    // Guard: if every value across all channels is null, the sensor was offline —
    // show the error panel rather than an empty plot.
    var allNull = data.triggering_channels.every(function (ch) {
      var vals = data.channels[ch] || [];
      return vals.every(function (v) { return v === null || v === undefined; });
    });
    if (allNull) {
      error.style.display = 'block';
      return;
    }
    chartDiv.style.display = 'block';
    var hasRange = data.range_start && data.range_end;
    var traces = data.triggering_channels.map(function (ch) {
      var raw = data.channels[ch] || [];
      // Per-channel normalization to [0, 1] so that high-resistance channels
      // (co_ppb, no2_ppb, nh3_ppb stored as ~350 kΩ) don't dwarf small-scale
      // channels like tvoc_ppb (~12 k ppb) on the shared Y axis.
      var nonNull = raw.filter(function (v) { return v !== null && v !== undefined; });
      var yMin = nonNull.length ? Math.min.apply(null, nonNull) : 0;
      var yMax = nonNull.length ? Math.max.apply(null, nonNull) : 1;
      var range = yMax - yMin;
      var yNorm = raw.map(function (v) {
        if (v === null || v === undefined) return null;
        return range === 0 ? 0.5 : (v - yMin) / range;
      });
      return {
        x: data.timestamps,
        y: yNorm,
        mode: 'lines', name: ch,
        line: { color: _CHANNEL_COLOURS[ch] || '#6b7280', width: 1.5 },
        hoverinfo: 'x+name',
      };
    });
    var shapes = [];
    var annotations = [];
    if (hasRange) {
      // Shaded region for the tagged event range
      shapes.push({
        type: 'rect',
        x0: data.range_start, x1: data.range_end, y0: 0, y1: 1,
        xref: 'x', yref: 'paper',
        fillcolor: 'rgba(239,68,68,0.12)', line: { color: 'rgba(239,68,68,0.4)', width: 1 },
        layer: 'below',
      });
      annotations.push({
        x: data.range_start, y: 1, xref: 'x', yref: 'paper',
        text: 'Tagged range', showarrow: false,
        font: { size: 9, color: '#ef4444' }, yanchor: 'bottom', xanchor: 'left',
      });
    } else {
      // Single point marker
      shapes.push({
        type: 'line', x0: data.inference_at, x1: data.inference_at, y0: 0, y1: 1,
        xref: 'x', yref: 'paper', layer: 'below',
        line: { color: '#ef4444', width: 1.5, dash: 'dash' }
      });
      annotations.push({
        x: data.inference_at, y: 1, xref: 'x', yref: 'paper',
        text: 'Event', showarrow: false,
        font: { size: 9, color: '#ef4444' }, yanchor: 'bottom'
      });
    }
    // For ML anomaly events draw a semi-transparent detection window around inference_at.
    if (data.is_ml_anomaly && !hasRange) {
      var infMs = new Date(data.inference_at).getTime();
      var m2ms = 2 * 60000;
      shapes.push({
        type: 'rect',
        x0: new Date(infMs - m2ms).toISOString(),
        x1: new Date(infMs + m2ms).toISOString(),
        y0: 0, y1: 1,
        xref: 'x', yref: 'paper',
        fillcolor: 'rgba(239,68,68,0.10)', line: { width: 0 },
        layer: 'below',
      });
    }
    var layout = {
      height: 180,
      margin: { l: 10, r: 10, t: 5, b: 30 },
      xaxis: { type: 'date', tickfont: { size: 9 }, zeroline: false },
      yaxis: { title: { text: 'normalised', font: { size: 10 } }, showticklabels: false, zeroline: false, autorange: true },
      showlegend: true,
      legend: { font: { size: 8 }, x: 0, y: 1, bgcolor: 'rgba(0,0,0,0)' },
      shapes: shapes,
      annotations: annotations,
      paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
    };
    Plotly.newPlot(chartDiv, traces, layout, { displayModeBar: false, responsive: true });
    // Resize immediately and after animation completes (panel transition is 280ms)
    Plotly.Plots.resize(chartDiv);
    setTimeout(function() { Plotly.Plots.resize(chartDiv); }, 320);
  } catch (e) {
    loading.style.display = 'none';
    error.style.display = 'block';
  }
}

// Expose as an explicit window property so that ES-module scripts (dashboard.js,
// history.js) and dynamically-loaded plain scripts (detections_insights.js) can
// all reach it regardless of how the browser resolves bare-name globals from
// module scope.
window.loadSparkline = loadSparkline;
