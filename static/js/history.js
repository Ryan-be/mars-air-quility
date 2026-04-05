import { toggleTheme, isLight, themeLayout } from './theme.js';
import { renderClimateCharts, renderGasCharts } from './charts.js';
import { renderEnvCharts } from './charts_env.js';
import { renderCorrelationCharts, updateCorrelationData, getSelectedAnalysisRange, getSelectedAnalysisRange } from './charts_correlation.js';


window.toggleTheme = () => toggleTheme(() => { _rendered = {}; renderActiveTab(); });
window.downloadCSV = () => {
  window.open(`/api/download?range=${document.getElementById("range").value}`, "_blank");
};

const TABS = ["climate", "air-quality", "particulate", "environment", "correlation", "detections"];
let _rendered    = {};
let _sensorData  = [];
let _weatherData = [];

document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("tab-active"));
    btn.classList.add("tab-active");
    TABS.forEach(t => {
      document.getElementById(`tab-${t}`).classList.toggle("tab-hidden", t !== btn.dataset.tab);
    });
    renderActiveTab();
  });
});

function activeTab() {
  return document.querySelector(".tab-btn.tab-active")?.dataset.tab ?? "climate";
}

function renderActiveTab() {
  const tab = activeTab();
  if (_rendered[tab]) return;
  _rendered[tab] = true;
  if (tab === "climate")     renderClimateCharts(_sensorData);
  if (tab === "air-quality") renderAirQualityTab(_sensorData);
  if (tab === "particulate") renderPmTable(_sensorData);
  if (tab === "environment") renderEnvCharts(_sensorData, _weatherData);
  if (tab === "correlation") renderCorrelationCharts(_sensorData);
  if (tab === "detections")  _initDetectionsTab();
}

document.getElementById("range").addEventListener("change", fetchData);
document.addEventListener("DOMContentLoaded", () => {
  const tagSelect = document.getElementById('corrRangeTagSelect');
  const saveBtn = document.getElementById('corrCreateRangeInferenceBtn');
  const status = document.getElementById('corrRangeInferenceStatus');

  if (tagSelect && saveBtn) {
    tagSelect.addEventListener('change', () => {
      saveBtn.disabled = !tagSelect.value;
      status.textContent = '';
      status.className = 'corr-range-status';
    });

    saveBtn.addEventListener('click', async () => {
      const selected = getSelectedAnalysisRange();
      const tag = tagSelect.value;
      if (!selected?.start || !selected?.end || !tag) {
        status.textContent = 'Select a range and tag before saving.';
        status.className = 'corr-range-status error';
        return;
      }
      saveBtn.disabled = true;
      saveBtn.textContent = 'Saving…';
      status.textContent = 'Saving tagged event…';
      status.className = 'corr-range-status';

      try {
        const resp = await fetch('/api/history/range-tag', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ start: selected.start, end: selected.end, tag }),
        });
        const result = await resp.json();
        if (!resp.ok) {
          throw new Error(result.error || 'Failed to save tagged event');
        }
        status.textContent = 'Tagged range saved successfully.';
        status.className = 'corr-range-status success';
        tagSelect.value = '';
        saveBtn.textContent = 'Saved';
        setTimeout(() => {
          saveBtn.textContent = 'Save tagged event';
          saveBtn.disabled = true;
        }, 1800);
      } catch (err) {
        status.textContent = err.message || 'Save failed.';
        status.className = 'corr-range-status error';
        saveBtn.textContent = 'Save tagged event';
      } finally {
        if (tagSelect.value) {
          saveBtn.disabled = false;
        }
      }
    });
  }
});
async function fetchData() {
  const range = document.getElementById("range").value;
  try {
    const [sRes, wRes] = await Promise.all([
      fetch(`/api/data?range=${range}`),
      fetch(`/api/weather/history?range=${range}`),
    ]);
    const rawSensor  = await sRes.json();
    const rawWeather = await wRes.json();

    if (!Array.isArray(rawSensor) || rawSensor.length === 0) {
      document.getElementById("last-updated").textContent = "No data for selected range.";
      return;
    }
    const corrWasRendered = !!_rendered["correlation"];
    _sensorData  = rawSensor.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    _weatherData = Array.isArray(rawWeather) ? rawWeather : [];
    _rendered    = {};

    // If the correlation tab was already rendered, update its data without
    // destroying the user's zoom — mark it rendered so renderActiveTab skips it.
    if (corrWasRendered) {
      updateCorrelationData(_sensorData);
      _rendered["correlation"] = true;
    }

    renderActiveTab();
    document.getElementById("last-updated").textContent =
      "Last updated: " + new Date().toLocaleString();
  } catch (e) {
    document.getElementById("last-updated").textContent = "Fetch error: " + e.message;
  }
}

// ── Detections & Insights tab ───────────────────────────────────────────────
function _initDetectionsTab() {
  if (!window._diJsLoaded) {
    window._diJsLoaded = true;
    const s = document.createElement('script');
    s.src = '/static/js/detections_insights.js';
    s.onload = function () { if (typeof DI !== 'undefined') DI.init(); };
    document.head.appendChild(s);
  } else {
    if (typeof DI !== 'undefined') DI.init();
  }
}

// ── PM table rendering ──────────────────────────────────────────────────────
function _pm25Class(v) {
  if (v == null) return "";
  if (v <= 12) return "pm-good";
  if (v <= 35) return "pm-moderate";
  return "pm-unhealthy";
}

function renderPmTable(data) {
  const pmRows = data.filter(d => d.pm2_5 != null || d.pm1_0 != null || d.pm10 != null);

  // Summary averages
  const avg = (arr) => arr.length ? (arr.reduce((a, b) => a + b, 0) / arr.length).toFixed(1) : "--";
  const pm1Vals  = pmRows.map(d => d.pm1_0).filter(v => v != null);
  const pm25Vals = pmRows.map(d => d.pm2_5).filter(v => v != null);
  const pm10Vals = pmRows.map(d => d.pm10).filter(v => v != null);

  const sumPm1  = document.getElementById("pmSumPm1");
  const sumPm25 = document.getElementById("pmSumPm25");
  const sumPm10 = document.getElementById("pmSumPm10");
  if (sumPm1)  sumPm1.textContent  = avg(pm1Vals);
  if (sumPm25) sumPm25.textContent = avg(pm25Vals);
  if (sumPm10) sumPm10.textContent = avg(pm10Vals);

  // Color the PM2.5 summary
  const avgPm25 = pm25Vals.length ? pm25Vals.reduce((a, b) => a + b, 0) / pm25Vals.length : null;
  if (sumPm25) sumPm25.className = `value ${_pm25Class(avgPm25)}`;

  // ── Plotly time-series chart ─────────────────────────────────────────────
  const plotEl = document.getElementById("pmTimeSeriesPlot");
  if (plotEl) {
    if (pmRows.length < 2) {
      plotEl.innerHTML = '<p style="color:#888;padding:1em;text-align:center">No particulate data for this range.</p>';
    } else {
      const ts   = pmRows.map(d => new Date(d.timestamp));
      const pm1  = pmRows.map(d => d.pm1_0);
      const pm25 = pmRows.map(d => d.pm2_5);
      const pm10 = pmRows.map(d => d.pm10);
      const titleFont = { color: isLight ? "#111" : "#ccc" };

      const traces = [
        {
          x: ts, y: pm1, mode: "lines", name: "PM1.0",
          line: { color: "#818cf8", width: 1.5 },
          hovertemplate: "PM1.0: %{y} µg/m³<extra></extra>",
        },
        {
          x: ts, y: pm25, mode: "lines", name: "PM2.5",
          line: { color: "#a78bfa", width: 2 },
          hovertemplate: "PM2.5: %{y} µg/m³<extra></extra>",
        },
        {
          x: ts, y: pm10, mode: "lines", name: "PM10",
          line: { color: "#6ee7b7", width: 1.5 },
          hovertemplate: "PM10: %{y} µg/m³<extra></extra>",
        },
        // WHO guideline reference lines — excluded from legend, labelled via annotations
        {
          x: [ts[0], ts[ts.length - 1]], y: [15, 15],
          mode: "lines", showlegend: false,
          line: { color: "#f59e0b", width: 1, dash: "dot" },
          hoverinfo: "skip",
        },
        {
          x: [ts[0], ts[ts.length - 1]], y: [45, 45],
          mode: "lines", showlegend: false,
          line: { color: "#ef4444", width: 1, dash: "dot" },
          hoverinfo: "skip",
        },
      ];

      Plotly.newPlot("pmTimeSeriesPlot", traces, themeLayout({
        title: { text: "🌫️ Particulate Matter over time", font: titleFont },
        xaxis: { type: "date" },
        yaxis: { title: "µg/m³", rangemode: "tozero" },
        legend: { orientation: "h", x: 0.5, xanchor: "center", y: 1.04, bgcolor: "rgba(0,0,0,0)" },
        margin: { t: 60 },
        annotations: [
          {
            xref: "paper", yref: "y", x: 1.01, y: 15,
            text: "WHO PM2.5", showarrow: false, xanchor: "left",
            font: { size: 10, color: "#f59e0b" },
          },
          {
            xref: "paper", yref: "y", x: 1.01, y: 45,
            text: "WHO PM10", showarrow: false, xanchor: "left",
            font: { size: 10, color: "#ef4444" },
          },
        ],
      }), { responsive: true });
    }
  }

  // ── Data table ──────────────────────────────────────────────────────────
  const tbody = document.getElementById("pmTableBody");
  if (!tbody) return;

  if (!pmRows.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="pm-empty">No particulate data for this range.</td></tr>';
    return;
  }

  // Show most recent first, limit to 200 rows for performance
  const display = pmRows.slice().reverse().slice(0, 200);
  tbody.innerHTML = display.map(d => {
    const ts = new Date(d.timestamp).toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit"
    });
    const cls = _pm25Class(d.pm2_5);
    return `<tr class="${cls}">
      <td>${ts}</td>
      <td>${d.pm1_0 ?? "--"}</td>
      <td>${d.pm2_5 ?? "--"}</td>
      <td>${d.pm10 ?? "--"}</td>
    </tr>`;
  }).join("");
}

// ── Air Quality tab rendering ────────────────────────────────────────────────
function renderAirQualityTab(data) {
  // SGP30 charts (eCO₂ + TVOC) via shared charts module
  renderGasCharts(data);

  // MICS6814 section
  const gasRows = data.filter(d => d.gas_co != null || d.gas_no2 != null || d.gas_nh3 != null);

  // Summary averages
  const avg = (arr) => arr.length ? (arr.reduce((a, b) => a + b, 0) / arr.length).toFixed(0) : "--";
  const coVals  = gasRows.map(d => d.gas_co).filter(v => v != null);
  const no2Vals = gasRows.map(d => d.gas_no2).filter(v => v != null);
  const nh3Vals = gasRows.map(d => d.gas_nh3).filter(v => v != null);

  const sumCo  = document.getElementById("gasSumCo");
  const sumNo2 = document.getElementById("gasSumNo2");
  const sumNh3 = document.getElementById("gasSumNh3");
  if (sumCo)  sumCo.textContent  = avg(coVals);
  if (sumNo2) sumNo2.textContent = avg(no2Vals);
  if (sumNh3) sumNh3.textContent = avg(nh3Vals);

  // Plotly time-series chart
  const plotEl = document.getElementById("gasTimeSeriesPlot");
  if (plotEl) {
    if (gasRows.length < 2) {
      plotEl.innerHTML = '<p style="color:#888;padding:1em;text-align:center">No MICS6814 data for this range.</p>';
    } else {
      const ts  = gasRows.map(d => new Date(d.timestamp));
      const co  = gasRows.map(d => d.gas_co);
      const no2 = gasRows.map(d => d.gas_no2);
      const nh3 = gasRows.map(d => d.gas_nh3);
      const titleFont = { color: isLight ? "#111" : "#ccc" };

      const traces = [
        {
          x: ts, y: co, mode: "lines", name: "CO (reducing)",
          line: { color: "#ef4444", width: 2 },
          hovertemplate: "CO: %{y} Ω<extra></extra>",
        },
        {
          x: ts, y: no2, mode: "lines", name: "NO₂ (oxidising)",
          line: { color: "#f59e0b", width: 2 },
          hovertemplate: "NO₂: %{y} Ω<extra></extra>",
        },
        {
          x: ts, y: nh3, mode: "lines", name: "NH₃",
          line: { color: "#22c55e", width: 2 },
          hovertemplate: "NH₃: %{y} Ω<extra></extra>",
        },
      ];

      Plotly.newPlot("gasTimeSeriesPlot", traces, themeLayout({
        title: { text: "🔥 MICS6814 Gas Sensor — resistance over time", font: titleFont },
        xaxis: { type: "date" },
        yaxis: { title: "Resistance (Ω) — lower = higher concentration", rangemode: "tozero" },
        legend: { orientation: "h", x: 0.5, xanchor: "center", y: 1.04, bgcolor: "rgba(0,0,0,0)" },
        margin: { t: 60 },
      }), { responsive: true });
    }
  }

  // Data table
  const tbody = document.getElementById("gasTableBody");
  if (!tbody) return;

  if (!gasRows.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="pm-empty">No MICS6814 data for this range.</td></tr>';
    return;
  }

  const display = gasRows.slice().reverse().slice(0, 200);
  tbody.innerHTML = display.map(d => {
    const ts = new Date(d.timestamp).toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit"
    });
    return `<tr>
      <td>${ts}</td>
      <td>${d.gas_co != null ? d.gas_co + " Ω" : "--"}</td>
      <td>${d.gas_no2 != null ? d.gas_no2 + " Ω" : "--"}</td>
      <td>${d.gas_nh3 != null ? d.gas_nh3 + " Ω" : "--"}</td>
    </tr>`;
  }).join("");
}

// ── Chart info button toggle ─────────────────────────────────────────────────
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".chart-info-btn");
  // Close all open popups first
  document.querySelectorAll(".chart-info-popup.visible").forEach(p => {
    if (!btn || p.id !== `info-${btn.dataset.info}`) p.classList.remove("visible");
  });
  if (!btn) return;
  const popup = document.getElementById(`info-${btn.dataset.info}`);
  if (popup) popup.classList.toggle("visible");
});

fetchData();

// ── SSE: refresh charts when new sensor data arrives (throttled to ~30s) ─────
let _histSSE = null;
let _histRetryMs = 1000;
let _histPending = false;

function _throttledFetch() {
  if (_histPending) return;
  _histPending = true;
  setTimeout(() => {
    fetchData();
    _histPending = false;
  }, 30000);
}

function connectHistorySSE() {
  if (_histSSE) _histSSE.close();
  _histSSE = new EventSource("/api/stream");
  _histSSE.addEventListener("sensor_update", _throttledFetch);
  _histSSE.onopen = () => { _histRetryMs = 1000; };
  _histSSE.onerror = () => {
    _histSSE.close();
    setTimeout(connectHistorySSE, _histRetryMs);
    _histRetryMs = Math.min(_histRetryMs * 2, 30000);
  };
}

connectHistorySSE();
