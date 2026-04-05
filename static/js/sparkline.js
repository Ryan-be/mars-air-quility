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
    loading.style.display = 'none';
    if (!data.triggering_channels || data.triggering_channels.length === 0) {
      error.style.display = 'block';
      return;
    }
    chartDiv.style.display = 'block';
    var inferenceTime = new Date(data.inference_at).getTime();
    var traces = data.triggering_channels.map(function (ch) {
      return {
        x: data.timestamps.map(function (ts) { return (new Date(ts).getTime() - inferenceTime) / 60000; }),
        y: data.channels[ch] || [],
        mode: 'lines', name: ch,
        line: { color: _CHANNEL_COLOURS[ch] || '#6b7280', width: 1.5 },
        hoverinfo: 'none',
      };
    });
    var layout = {
      height: 180,
      margin: { l:10, r:10, t:5, b:30 },
      xaxis: { title: { text:'minutes', font:{size:10} }, tickfont:{size:9}, zeroline:false },
      yaxis: { showticklabels:false, zeroline:false, autorange:true },
      showlegend: false,
      shapes: [{
        type:'line', x0:0, x1:0, y0:0, y1:1,
        xref:'x', yref:'paper',
        layer:'below',
        line:{ color:'#ef4444', width:1.5, dash:'dash' }
      }],
      annotations: [{
        x:0, y:1, xref:'x', yref:'paper',
        text:'Event', showarrow:false,
        font:{ size:9, color:'#ef4444' }, yanchor:'bottom'
      }],
      paper_bgcolor:'transparent', plot_bgcolor:'transparent',
    };
    Plotly.newPlot(chartDiv, traces, layout, { displayModeBar:false, responsive:true });
  } catch (e) {
    loading.style.display = 'none';
    error.style.display = 'block';
  }
}
