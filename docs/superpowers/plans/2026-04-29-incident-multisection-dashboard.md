# Incident Multi-Section Dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the grid-of-hulls centre canvas on the Incidents page with four time-windowed sub-sections (Galaxy, Rose, Storyline, Co-occurrence), each with an AstroUX `rux-tooltip` explaining what it shows. Every section respects the active time window and severity filter — no all-time data.

**Architecture:** Two small additive backend changes (extend list-summary, add storyline endpoint). Two pure JS modules (PCA, sensor-map) with Node fixture tests. Four section modules each owning one visualization. One orchestrator file replaces the centroid/Cytoscape grid plumbing. AstroUX tooltips via the existing `info-icon[title]` auto-wrap pattern in `base.html`.

**Tech Stack:** Flask/SQLite backend, vanilla ES modules, Cytoscape.js (already loaded — used by Co-occurrence only), AstroUXDS web components (rux-tooltip).

**Spec:** `docs/superpowers/specs/2026-04-29-incident-multisection-dashboard-design.md`

---

## File Structure

**Created:**
- `static/js/pca.mjs` — pure 2-D PCA helper
- `static/js/sections/sensor_map.mjs` — `event_type → sensor_channel` mapping
- `static/js/sections/galaxy.mjs`
- `static/js/sections/rose.mjs`
- `static/js/sections/storyline.mjs`
- `static/js/sections/cooccurrence.mjs`
- `tests/js/test_pca.mjs`
- `tests/js/test_sensor_map.mjs`

**Modified:**
- `mlss_monitor/routes/api_incidents.py` (extend list summary, add storyline endpoint)
- `tests/test_api_incidents.py`
- `templates/incidents.html` (replace centre-pane HTML)
- `static/js/incident_graph.js` (gut centroid/Cytoscape grid plumbing, become orchestrator)
- `static/css/incident_graph.css` (new section classes, retire grid classes)

**Deleted:** `static/js/compute_centroids.mjs`, `tests/js/test_compute_centroids.mjs`. (Connected_components.mjs stays — harmless and small.)

---

## Task 1: Backend — extend list-summary + retain signature

**Files:**
- Modify: `mlss_monitor/routes/api_incidents.py:120-214` (`list_incidents`)
- Modify: `tests/test_api_incidents.py`

**Goal:** stop dropping `signature` from per-incident response and add `severity_by_hour` to the summary block.

- [ ] **Step 1: Write failing test for retained signature**

Append to `tests/test_api_incidents.py`:

```python
def test_get_incidents_retains_signature_field(client, db):
    """list_incidents must return the signature vector so the Galaxy
    section can run PCA over the active window."""
    _seed_incident(db, "INC-20260429-1100")
    rv = client.get("/api/incidents")
    data = rv.get_json()
    inc = next(i for i in data["incidents"] if i["id"] == "INC-20260429-1100")
    assert "signature" in inc
    assert isinstance(inc["signature"], str)  # JSON-encoded list


def test_get_incidents_summary_includes_severity_by_hour(client, db):
    """summary.severity_by_hour is a 24-int array of severity ranks
    (0 info, 1 warning, 2 critical, -1 if no incidents that hour)."""
    _seed_incident(db, "INC-20260429-1500", max_severity="warning",
                   started_at="2026-04-29 15:00:00",
                   ended_at="2026-04-29 15:05:00")
    _seed_incident(db, "INC-20260429-1530", max_severity="critical",
                   started_at="2026-04-29 15:30:00",
                   ended_at="2026-04-29 15:35:00")
    rv = client.get("/api/incidents")
    data = rv.get_json()
    sbh = data["summary"]["severity_by_hour"]
    assert len(sbh) == 24
    assert sbh[15] == 2  # critical wins
    assert sbh[3] == -1  # no incidents
```

- [ ] **Step 2: Run test — expect RED**

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pytest tests/test_api_incidents.py -k "signature or severity_by_hour" -v`
Expected: both tests FAIL.

- [ ] **Step 3: Modify `list_incidents` to retain signature**

In `mlss_monitor/routes/api_incidents.py`, find the loop near line 158-163:

```python
    incidents: list[dict] = []
    for row in rows:
        d = dict(row)
        d.pop("signature", None)  # don't expose raw vector over the wire
        if q and q not in d.get("title", "").lower() and q not in d["id"].lower():
            continue
        incidents.append(d)
```

Remove the `d.pop("signature", None)` line. Signature is small (32 floats ≈ 280 bytes) and the Galaxy section needs it.

- [ ] **Step 4: Compute and add `severity_by_hour` to summary**

After the existing hour_histogram block (around line 196-203), add:

```python
    # Per-hour MAX severity rank (0 info, 1 warning, 2 critical, -1 empty).
    # Storyline + Rose use this to colour wedges by severity.
    severity_by_hour = [-1] * 24
    for inc in incidents:
        started = inc.get("started_at", "")
        if len(started) >= 13:
            try:
                hour = int(started[11:13])
                rank = _SEVERITY_ORDER.get(inc.get("max_severity", "info"), 0)
                if rank > severity_by_hour[hour]:
                    severity_by_hour[hour] = rank
            except (ValueError, KeyError):
                pass
```

Then in the response `summary` block, add `"severity_by_hour": severity_by_hour`.

- [ ] **Step 5: Run tests — expect GREEN**

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pytest tests/test_api_incidents.py -v`
Expected: all green, including the two new tests.

- [ ] **Step 6: Commit**

```bash
git add mlss_monitor/routes/api_incidents.py tests/test_api_incidents.py
git commit -m "feat(api): retain signature + add severity_by_hour to list summary

Galaxy section runs PCA on signature vectors. Rose section colours
wedges by severity_by_hour. Both are pure additions — no existing
caller relies on signature being absent."
```

---

## Task 2: Backend — `/api/incidents/storyline` endpoint

**Files:**
- Modify: `mlss_monitor/routes/api_incidents.py` (new route below `list_incidents`)
- Modify: `tests/test_api_incidents.py`

- [ ] **Step 1: Write failing test for endpoint shape**

Append to `tests/test_api_incidents.py`:

```python
def test_storyline_endpoint_returns_alerts_and_edges(client, db):
    """Batched-detail endpoint returns lightweight alert+edge data per
    incident in the active window so Storyline can render in one fetch."""
    import sqlite3
    _seed_incident(db, "INC-20260429-1500")
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO inferences (id, created_at, event_type, severity, "
        "title, confidence) VALUES (?, ?, ?, ?, ?, ?)",
        (701, "2026-04-29 15:00:01", "tvoc_spike", "warning", "t", 0.8),
    )
    conn.execute(
        "INSERT INTO inferences (id, created_at, event_type, severity, "
        "title, confidence) VALUES (?, ?, ?, ?, ?, ?)",
        (702, "2026-04-29 15:00:05", "eco2_elevated", "warning", "e", 0.8),
    )
    conn.execute("INSERT INTO incident_alerts (incident_id, alert_id, is_primary) "
                 "VALUES (?, ?, ?)", ("INC-20260429-1500", 701, 1))
    conn.execute("INSERT INTO incident_alerts (incident_id, alert_id, is_primary) "
                 "VALUES (?, ?, ?)", ("INC-20260429-1500", 702, 1))
    conn.commit()
    conn.close()

    rv = client.get("/api/incidents/storyline?window=14d")
    assert rv.status_code == 200
    data = rv.get_json()
    inc = next(i for i in data["incidents"] if i["id"] == "INC-20260429-1500")
    assert len(inc["alerts"]) == 2
    assert {a["event_type"] for a in inc["alerts"]} == {"tvoc_spike", "eco2_elevated"}
    # 2 alerts within 30 min → temporal_edge_probability = 1.0
    assert len(inc["edges"]) == 1
    assert inc["edges"][0]["p"] == 1.0


def test_storyline_endpoint_respects_window(client, db):
    _seed_incident(db, "INC-20260101-0900",
                   started_at="2026-01-01 09:00:00",
                   ended_at="2026-01-01 09:01:00")
    rv = client.get("/api/incidents/storyline?window=24h")
    data = rv.get_json()
    assert all(i["id"] != "INC-20260101-0900" for i in data["incidents"])
```

- [ ] **Step 2: Run test — expect RED**

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pytest tests/test_api_incidents.py -k storyline -v`
Expected: 404 (route doesn't exist).

- [ ] **Step 3: Add the storyline route**

In `mlss_monitor/routes/api_incidents.py`, after `list_incidents` (around line 215), add:

```python
@api_incidents_bp.route("/api/incidents/storyline")
def storyline_data():
    """Lightweight batched-detail endpoint for the Storyline sub-section.

    Returns one entry per incident in the active window, with primary alerts
    and their temporal edges only. No narrative, no signal_deps, no signature
    — those live in the per-incident detail endpoint when an incident is
    selected.
    """
    window = request.args.get("window", "24h")
    severity = request.args.get("severity", "all")
    if window not in _WINDOW_MAP:
        return jsonify({"error": f"Unknown window: {window!r}"}), 400

    conn = _get_conn()
    since = _parse_window(window)
    conditions: list[str] = []
    params: list = []
    if since:
        conditions.append("started_at >= ?")
        params.append(since.isoformat(sep=" "))
    if severity and severity != "all":
        conditions.append("max_severity = ?")
        params.append(severity)
    query = "SELECT id, started_at, max_severity FROM incidents"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY started_at DESC LIMIT 500"
    inc_rows = conn.execute(query, params).fetchall()

    incidents_out: list[dict] = []
    for inc_row in inc_rows:
        inc = dict(inc_row)
        alert_rows = conn.execute(
            "SELECT i.id, i.created_at, i.event_type, i.severity, ia.is_primary "
            "FROM inferences i JOIN incident_alerts ia ON ia.alert_id = i.id "
            "WHERE ia.incident_id = ? AND ia.is_primary = 1 ORDER BY i.created_at",
            (inc["id"],),
        ).fetchall()
        alerts = [dict(a) for a in alert_rows]
        edges_out: list[dict] = []
        for i, a1 in enumerate(alerts):
            for a2 in alerts[i + 1:]:
                p = temporal_edge_probability(a1, a2)
                if p <= 0.0:
                    continue
                edges_out.append({
                    "from": a1["id"], "to": a2["id"],
                    "p": round(p, 3), "causal": False,
                })
        incidents_out.append({
            "id": inc["id"],
            "started_at": inc["started_at"],
            "max_severity": inc["max_severity"],
            "alerts": alerts,
            "edges": edges_out,
        })

    conn.close()
    return jsonify({"incidents": incidents_out})
```

The `LIMIT 500` is a safety cap — at 14 d the window can hold many incidents; Storyline lag past 500 isn't worth optimising for v1.

- [ ] **Step 4: Run tests — expect GREEN**

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pytest tests/test_api_incidents.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/routes/api_incidents.py tests/test_api_incidents.py
git commit -m "feat(api): /api/incidents/storyline batched-detail endpoint

Returns primary alerts + temporal edges per incident in one fetch
so Storyline section can render without N+1 detail calls."
```

---

## Task 3: Pure PCA module

**Files:**
- Create: `static/js/pca.mjs`
- Create: `tests/js/test_pca.mjs`

- [ ] **Step 1: Write failing fixture test**

Create `tests/js/test_pca.mjs`:

```javascript
// Run: node tests/js/test_pca.mjs — exit 0 on pass, 1 on fail.
import { pca2d } from '../../static/js/pca.mjs';

let failures = 0;
function expect(label, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a === e) console.log(`  ok  ${label}`);
  else { console.log(`  FAIL ${label}\n    expected: ${e}\n    actual:   ${a}`); failures++; }
}

// Empty input → empty output
expect('empty', pca2d([]), []);

// Single point → single (0,0)
expect('singleton',
  pca2d([[1, 2, 3, 4]]).map(p => [Math.round(p[0]), Math.round(p[1])]),
  [[0, 0]]);

// Two identical points → both at origin
expect('duplicates',
  pca2d([[1, 2, 3], [1, 2, 3]]).map(p => [Math.round(p[0]), Math.round(p[1])]),
  [[0, 0], [0, 0]]);

// Three points along a line in N-d should land on a 1-D x-axis (y ~ 0)
const linePts = [[0, 0, 0, 0], [1, 1, 1, 1], [2, 2, 2, 2]];
const lineProj = pca2d(linePts);
expect('collinear: y values cluster near 0',
  lineProj.every(p => Math.abs(p[1]) < 0.5),
  true);
expect('collinear: x values are spread',
  lineProj[0][0] !== lineProj[2][0],
  true);

// Synthetic 2-cluster: should produce visible separation along x
const clusters = [
  [0, 0, 0, 0], [0.1, 0, 0, 0], [0, 0.1, 0, 0],
  [10, 10, 10, 10], [10.1, 10, 10, 10], [10, 10.1, 10, 10],
];
const cProj = pca2d(clusters);
const meanX1 = (cProj[0][0] + cProj[1][0] + cProj[2][0]) / 3;
const meanX2 = (cProj[3][0] + cProj[4][0] + cProj[5][0]) / 3;
expect('two clusters: mean x differs by > 5',
  Math.abs(meanX1 - meanX2) > 5,
  true);

if (failures) { console.error(`\n${failures} test(s) failed`); process.exit(1); }
console.log('\nAll PCA tests passed');
```

- [ ] **Step 2: Run RED**

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_pca.mjs`
Expected: ERR_MODULE_NOT_FOUND.

- [ ] **Step 3: Implement `static/js/pca.mjs`**

```javascript
// Pure 2-D PCA via power iteration. No DOM, no dependencies — Node-testable.
//
// pca2d(rows): given an N×D matrix (array of arrays), return an N×2 matrix
// of (x, y) coordinates projecting each row onto its first two principal
// components. Centred to mean zero; not scaled.

function meanVec(rows) {
  const d = rows[0].length;
  const m = new Array(d).fill(0);
  for (const r of rows) for (let i = 0; i < d; i++) m[i] += r[i];
  for (let i = 0; i < d; i++) m[i] /= rows.length;
  return m;
}

function dot(a, b) { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * b[i]; return s; }
function normalise(v) {
  const n = Math.sqrt(dot(v, v)) || 1;
  return v.map(x => x / n);
}

function powerIter(centred, deflate) {
  // 30 iterations is more than enough for a 32-d covariance matrix.
  const d = centred[0].length;
  let v = new Array(d).fill(0).map(() => Math.random() - 0.5);
  v = normalise(v);
  for (let it = 0; it < 30; it++) {
    // multiply by C = Xᵀ X (we never form C explicitly).
    const Xv = centred.map(row => dot(row, v));
    const next = new Array(d).fill(0);
    for (let i = 0; i < centred.length; i++)
      for (let j = 0; j < d; j++)
        next[j] += centred[i][j] * Xv[i];
    if (deflate) {
      // remove the deflate-direction component
      const c = dot(next, deflate);
      for (let j = 0; j < d; j++) next[j] -= c * deflate[j];
    }
    v = normalise(next);
  }
  return v;
}

export function pca2d(rows) {
  if (!rows || rows.length === 0) return [];
  if (rows.length === 1) return [[0, 0]];
  const mean = meanVec(rows);
  const centred = rows.map(r => r.map((x, i) => x - mean[i]));
  const v1 = powerIter(centred, null);
  const v2 = powerIter(centred, v1);
  return centred.map(r => [dot(r, v1), dot(r, v2)]);
}
```

- [ ] **Step 4: Run tests — expect GREEN**

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_pca.mjs`
Expected: all 6 `ok` lines.

- [ ] **Step 5: Commit**

```bash
git add static/js/pca.mjs tests/js/test_pca.mjs
git commit -m "feat(incidents): pure 2-D PCA module via power iteration

For Galaxy section's incident-similarity projection. ~50 lines, no
dependencies, Node-fixture-tested. Handles empty / singleton / dup /
collinear / multi-cluster cases."
```

---

## Task 4: Sensor-channel map module

**Files:**
- Create: `static/js/sections/sensor_map.mjs`
- Create: `tests/js/test_sensor_map.mjs`

The backend has a `_RULE_CHANNEL_MAP` in `mlss_monitor/routes/api_inferences.py`. Mirror it in JS as a single source of truth for "which sensor lane does this event_type belong to".

- [ ] **Step 1: Write failing test**

Create `tests/js/test_sensor_map.mjs`:

```javascript
import { primaryChannel, ALL_CHANNELS } from '../../static/js/sections/sensor_map.mjs';

let failures = 0;
function expect(label, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) console.log(`  ok  ${label}`);
  else { console.log(`  FAIL ${label}\n    exp: ${e}\n    got: ${a}`); failures++; }
}

expect('high_tvoc → tvoc_ppb',     primaryChannel('high_tvoc'),     'tvoc_ppb');
expect('eco2_elevated → eco2_ppm', primaryChannel('eco2_elevated'), 'eco2_ppm');
expect('humidity_low → humidity_pct', primaryChannel('humidity_low'), 'humidity_pct');
expect('high_pm25 → pm25_ug_m3',   primaryChannel('high_pm25'),     'pm25_ug_m3');
expect('anomaly_tvoc_ppb → tvoc_ppb', primaryChannel('anomaly_tvoc_ppb'), 'tvoc_ppb');
expect('unknown → null',           primaryChannel('mystery_event'),  null);
expect('ALL_CHANNELS has six',     ALL_CHANNELS.length >= 6, true);

if (failures) { console.error(`${failures} failed`); process.exit(1); }
console.log('All sensor_map tests passed');
```

- [ ] **Step 2: Run RED**

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_sensor_map.mjs`
Expected: ERR_MODULE_NOT_FOUND.

- [ ] **Step 3: Implement `static/js/sections/sensor_map.mjs`**

```javascript
// Mirror of mlss_monitor/routes/api_inferences.py::_RULE_CHANNEL_MAP — used
// by Storyline (which sensor lane?) and Co-occurrence (which node?).
//
// Keep this file in sync with the backend map — when a new event_type is
// added, both files must list it.

const _RULE_CHANNEL_MAP = {
  high_tvoc:         'tvoc_ppb',
  tvoc_spike:        'tvoc_ppb',
  high_eco2:         'eco2_ppm',
  eco2_elevated:     'eco2_ppm',
  eco2_danger:       'eco2_ppm',
  high_temperature:  'temperature_c',
  low_temperature:   'temperature_c',
  high_humidity:     'humidity_pct',
  low_humidity:      'humidity_pct',
  humidity_low:      'humidity_pct',
  rapid_humidity_change: 'humidity_pct',
  vpd_high:          'humidity_pct',  // VPD is humidity-derived
  high_pm25:         'pm25_ug_m3',
  high_pm10:         'pm10_ug_m3',
  high_co:           'co_ppb',
  high_no2:          'no2_ppb',
  high_nh3:          'nh3_ppb',
};

const _ANOMALY_PREFIX = 'anomaly_';

// Display order top-to-bottom in Storyline lanes.
export const ALL_CHANNELS = [
  'tvoc_ppb',
  'eco2_ppm',
  'co_ppb',
  'pm25_ug_m3',
  'humidity_pct',
  'temperature_c',
];

export function primaryChannel(eventType) {
  if (!eventType) return null;
  if (_RULE_CHANNEL_MAP[eventType]) return _RULE_CHANNEL_MAP[eventType];
  if (eventType.startsWith(_ANOMALY_PREFIX)) {
    const stem = eventType.slice(_ANOMALY_PREFIX.length);
    if (ALL_CHANNELS.includes(stem)) return stem;
  }
  return null;
}

export const CHANNEL_LABEL = {
  tvoc_ppb:       'TVOC',
  eco2_ppm:       'eCO₂',
  co_ppb:         'CO',
  pm25_ug_m3:     'PM₂.₅',
  pm10_ug_m3:     'PM₁₀',
  humidity_pct:   'Humid',
  temperature_c:  'Temp',
  no2_ppb:        'NO₂',
  nh3_ppb:        'NH₃',
};
```

- [ ] **Step 4: Run GREEN**

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_sensor_map.mjs`
Expected: all 7 `ok` lines.

- [ ] **Step 5: Commit**

```bash
git add static/js/sections/sensor_map.mjs tests/js/test_sensor_map.mjs
git commit -m "feat(incidents): JS sensor-channel map mirrors backend rules

Single source of truth for 'which sensor lane does this event_type
sit in'. Used by Storyline and Co-occurrence."
```

---

## Task 5: Galaxy section

**Files:**
- Create: `static/js/sections/galaxy.mjs`

Pure render module. Takes the incident list (already fetched) plus selected incident id; renders an SVG dotplot into a target div; emits a `select` event when a dot is clicked.

- [ ] **Step 1: Implement `static/js/sections/galaxy.mjs`**

```javascript
// Galaxy — incident-similarity 2-D scatter via PCA over signature vectors.
//
// Public:  renderGalaxy(rootEl, { incidents, selectedId, onSelect })
// onSelect(incidentId): callback when a dot is clicked.

import { pca2d } from '../pca.mjs';

const SEV_COLOR = { critical: '#ff3838', warning: '#fc8c2f', info: '#2dccff' };

function parseSignature(s) {
  if (Array.isArray(s)) return s;
  if (typeof s !== 'string' || !s) return null;
  try { const v = JSON.parse(s); return Array.isArray(v) ? v : null; }
  catch (_) { return null; }
}

export function renderGalaxy(rootEl, { incidents, selectedId, onSelect }) {
  if (!rootEl) return;
  const W = rootEl.clientWidth || 400;
  const H = rootEl.clientHeight || 200;
  const margin = 18;

  const valid = (incidents || [])
    .map(i => ({ inc: i, sig: parseSignature(i.signature) }))
    .filter(o => o.sig && o.sig.length > 0);

  if (valid.length < 2) {
    rootEl.innerHTML = `<div class="inc-section-empty">
      Need at least 2 incidents with signatures to compute similarity.
      Try a wider window.
    </div>`;
    return;
  }

  // Detect "all signatures near zero" → fall back to (started_at, severity) layout.
  const allZero = valid.every(o => o.sig.every(x => Math.abs(x) < 1e-6));
  let coords;
  if (allZero) {
    coords = valid.map((o, i) => [i, _SEV_RANK[o.inc.max_severity] || 0]);
  } else {
    coords = pca2d(valid.map(o => o.sig));
  }

  // Normalize to viewport.
  let xs = coords.map(p => p[0]), ys = coords.map(p => p[1]);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const yMin = Math.min(...ys), yMax = Math.max(...ys);
  const xRange = xMax - xMin || 1;
  const yRange = yMax - yMin || 1;

  const scaled = coords.map(([x, y]) => ({
    x: margin + ((x - xMin) / xRange) * (W - 2 * margin),
    y: margin + ((y - yMin) / yRange) * (H - 2 * margin),
  }));

  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', '100%');

  valid.forEach((o, i) => {
    const c = document.createElementNS(svgNS, 'circle');
    const isSel = o.inc.id === selectedId;
    const r = 4 + Math.min(8, (o.inc.alert_count || 1));
    c.setAttribute('cx', scaled[i].x);
    c.setAttribute('cy', scaled[i].y);
    c.setAttribute('r', r);
    c.setAttribute('fill', SEV_COLOR[o.inc.max_severity] || '#2dccff');
    c.setAttribute('opacity', isSel ? 0.95 : 0.6);
    c.setAttribute('stroke', isSel ? '#4dacff' : 'none');
    c.setAttribute('stroke-width', isSel ? 2.5 : 0);
    c.style.cursor = 'pointer';
    c.dataset.incidentId = o.inc.id;
    c.addEventListener('click', () => onSelect && onSelect(o.inc.id));
    svg.appendChild(c);
  });

  rootEl.innerHTML = '';
  rootEl.appendChild(svg);
}

const _SEV_RANK = { info: 0, warning: 1, critical: 2 };
```

- [ ] **Step 2: Smoke-test compile (no test for DOM-bound module)**

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node -e "import('./static/js/sections/galaxy.mjs').then(m => console.log('OK', Object.keys(m)))"`
Expected: `OK [ 'renderGalaxy' ]`.

- [ ] **Step 3: Commit**

```bash
git add static/js/sections/galaxy.mjs
git commit -m "feat(incidents): Galaxy section — PCA similarity scatter"
```

---

## Task 6: Rose section

**Files:**
- Create: `static/js/sections/rose.mjs`

24-wedge polar bar chart from `summary.hour_histogram` + `summary.severity_by_hour`.

- [ ] **Step 1: Implement `static/js/sections/rose.mjs`**

```javascript
// Rose — daily rhythm polar bar chart.
//
// Public:  renderRose(rootEl, { hour_histogram, severity_by_hour, selectedHour, onSelect })

const SEV_COLOR_BY_RANK = ['#2dccff', '#fc8c2f', '#ff3838']; // 0,1,2
const RANK_TO_OPACITY = [0.45, 0.7, 0.9];

export function renderRose(rootEl, { hour_histogram, severity_by_hour, selectedHour, onSelect }) {
  if (!rootEl) return;
  const W = rootEl.clientWidth || 200;
  const H = rootEl.clientHeight || 200;
  const cx = W / 2, cy = H / 2;
  const innerR = Math.min(W, H) * 0.16;
  const outerR = Math.min(W, H) * 0.42;

  const counts = Array.isArray(hour_histogram) ? hour_histogram : new Array(24).fill(0);
  const sevs   = Array.isArray(severity_by_hour) ? severity_by_hour : new Array(24).fill(-1);
  const maxCount = Math.max(1, ...counts);

  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', '100%');

  // Axis circles.
  for (const rFrac of [0.5, 1.0]) {
    const c = document.createElementNS(svgNS, 'circle');
    c.setAttribute('cx', cx); c.setAttribute('cy', cy);
    c.setAttribute('r', innerR + (outerR - innerR) * rFrac);
    c.setAttribute('fill', 'none'); c.setAttribute('stroke', '#2a3346'); c.setAttribute('stroke-width', 0.5);
    svg.appendChild(c);
  }

  for (let h = 0; h < 24; h++) {
    const a0 = (h / 24) * Math.PI * 2 - Math.PI / 2;
    const a1 = ((h + 1) / 24) * Math.PI * 2 - Math.PI / 2;
    const r = innerR + (outerR - innerR) * (counts[h] / maxCount);
    const sevRank = sevs[h];
    const fill = sevRank >= 0 ? SEV_COLOR_BY_RANK[sevRank] : '#1a2540';
    const opacity = sevRank >= 0 ? RANK_TO_OPACITY[sevRank] : 0.25;

    const x0i = cx + innerR * Math.cos(a0), y0i = cy + innerR * Math.sin(a0);
    const x1i = cx + innerR * Math.cos(a1), y1i = cy + innerR * Math.sin(a1);
    const x0o = cx + r       * Math.cos(a0), y0o = cy + r       * Math.sin(a0);
    const x1o = cx + r       * Math.cos(a1), y1o = cy + r       * Math.sin(a1);
    const path = document.createElementNS(svgNS, 'path');
    path.setAttribute('d',
      `M ${x0i} ${y0i} L ${x0o} ${y0o} A ${r} ${r} 0 0 1 ${x1o} ${y1o} L ${x1i} ${y1i} Z`);
    path.setAttribute('fill', fill);
    path.setAttribute('opacity', selectedHour === h ? 1.0 : opacity);
    path.setAttribute('stroke', selectedHour === h ? '#4dacff' : 'none');
    path.setAttribute('stroke-width', selectedHour === h ? 1.5 : 0);
    path.style.cursor = 'pointer';
    path.dataset.hour = String(h);
    path.addEventListener('click', () => onSelect && onSelect(h));
    svg.appendChild(path);
  }

  // Hour labels at compass points.
  const labels = [['00', cx, cy - outerR - 4], ['06', cx + outerR + 8, cy], ['12', cx, cy + outerR + 12], ['18', cx - outerR - 8, cy]];
  for (const [t, x, y] of labels) {
    const tx = document.createElementNS(svgNS, 'text');
    tx.textContent = t;
    tx.setAttribute('x', x); tx.setAttribute('y', y);
    tx.setAttribute('font-size', '7'); tx.setAttribute('fill', '#7a8497');
    tx.setAttribute('text-anchor', 'middle'); tx.setAttribute('font-family', 'monospace');
    svg.appendChild(tx);
  }

  rootEl.innerHTML = '';
  rootEl.appendChild(svg);
}
```

- [ ] **Step 2: Smoke compile**

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node -e "import('./static/js/sections/rose.mjs').then(m => console.log('OK', Object.keys(m)))"`
Expected: `OK [ 'renderRose' ]`.

- [ ] **Step 3: Commit**

```bash
git add static/js/sections/rose.mjs
git commit -m "feat(incidents): Rose section — daily-rhythm polar bar chart"
```

---

## Task 7: Storyline section

**Files:**
- Create: `static/js/sections/storyline.mjs`

Subway-map render from the `/api/incidents/storyline` payload.

- [ ] **Step 1: Implement `static/js/sections/storyline.mjs`**

```javascript
// Storyline — subway map of alerts on (sensor lane × time) axes.
//
// Public:  renderStoryline(rootEl, { storylineData, windowStart, windowEnd,
//                                     selectedId, edgePFloor, sensorFilter,
//                                     onSelect })

import { ALL_CHANNELS, CHANNEL_LABEL, primaryChannel } from './sensor_map.mjs';

const SEV_COLOR = { critical: '#ff3838', warning: '#fc8c2f', info: '#2dccff' };

export function renderStoryline(rootEl, opts) {
  const { storylineData, windowStart, windowEnd, selectedId,
          edgePFloor = 0.20, sensorFilter = null, onSelect } = opts || {};
  if (!rootEl) return;
  const W = rootEl.clientWidth || 600;
  const H = rootEl.clientHeight || 240;
  const leftPad = 50, rightPad = 12, topPad = 14, bottomPad = 22;
  const laneCount = ALL_CHANNELS.length;
  const laneH = (H - topPad - bottomPad) / laneCount;
  const laneY = ch => topPad + (ALL_CHANNELS.indexOf(ch) + 0.5) * laneH;

  const incidents = (storylineData && storylineData.incidents) || [];
  if (incidents.length === 0) {
    rootEl.innerHTML = '<div class="inc-section-empty">No primary alerts in this window.</div>';
    return;
  }

  const tStart = windowStart instanceof Date ? windowStart.getTime() : Date.parse(windowStart);
  const tEnd   = windowEnd   instanceof Date ? windowEnd.getTime()   : Date.parse(windowEnd);
  const span   = Math.max(1, tEnd - tStart);
  const xOf = ts => leftPad + ((ts - tStart) / span) * (W - leftPad - rightPad);

  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', '100%');

  // Lane backgrounds + labels.
  ALL_CHANNELS.forEach(ch => {
    const y = laneY(ch);
    const line = document.createElementNS(svgNS, 'line');
    line.setAttribute('x1', leftPad); line.setAttribute('x2', W - rightPad);
    line.setAttribute('y1', y); line.setAttribute('y2', y);
    line.setAttribute('stroke', '#3a4558'); line.setAttribute('stroke-width', '0.5'); line.setAttribute('opacity', '0.6');
    svg.appendChild(line);
    const txt = document.createElementNS(svgNS, 'text');
    txt.textContent = CHANNEL_LABEL[ch] || ch;
    txt.setAttribute('x', leftPad - 4); txt.setAttribute('y', y + 3);
    txt.setAttribute('font-size', '8'); txt.setAttribute('text-anchor', 'end');
    txt.setAttribute('fill', sensorFilter === ch ? '#4dacff' : '#9aa5bd');
    txt.setAttribute('font-family', 'monospace');
    svg.appendChild(txt);
  });

  // For each incident: place dots, draw connecting curve.
  for (const inc of incidents) {
    const isSel = inc.id === selectedId;
    const points = [];
    for (const a of inc.alerts) {
      const ch = primaryChannel(a.event_type);
      if (!ch || !ALL_CHANNELS.includes(ch)) continue;
      const ts = Date.parse(a.created_at);
      if (!Number.isFinite(ts)) continue;
      const x = xOf(ts);
      const y = laneY(ch);
      points.push({ x, y, sev: a.severity, ch });
    }

    if (sensorFilter && !points.some(p => p.ch === sensorFilter)) continue;

    // Curve: simple polyline with quadratic smoothing through midpoints.
    if (points.length > 1) {
      const path = document.createElementNS(svgNS, 'path');
      let d = `M ${points[0].x} ${points[0].y}`;
      for (let i = 1; i < points.length; i++) {
        const p = points[i], q = points[i - 1];
        const mx = (p.x + q.x) / 2, my = (p.y + q.y) / 2;
        d += ` Q ${q.x} ${q.y} ${mx} ${my} T ${p.x} ${p.y}`;
      }
      path.setAttribute('d', d); path.setAttribute('fill', 'none');
      const minP = inc.edges && inc.edges.length
        ? Math.min(...inc.edges.map(e => e.p)) : 1.0;
      const isWeak = minP < edgePFloor;
      path.setAttribute('stroke', isSel ? '#4dacff' : '#9aa5bd');
      path.setAttribute('stroke-width', isSel ? 2.5 : 1.2);
      path.setAttribute('stroke-dasharray', (isSel || !isWeak) ? '' : '3 2');
      path.setAttribute('opacity', isSel ? 0.95 : (isWeak ? 0.35 : 0.55));
      path.style.cursor = 'pointer';
      path.addEventListener('click', () => onSelect && onSelect(inc.id));
      svg.appendChild(path);
    }

    for (const p of points) {
      const c = document.createElementNS(svgNS, 'circle');
      c.setAttribute('cx', p.x); c.setAttribute('cy', p.y);
      c.setAttribute('r', isSel ? 5 : 3.5);
      c.setAttribute('fill', SEV_COLOR[p.sev] || '#2dccff');
      c.setAttribute('opacity', isSel ? 1.0 : 0.7);
      c.setAttribute('stroke', isSel ? '#4dacff' : 'none');
      c.setAttribute('stroke-width', isSel ? 1.5 : 0);
      c.style.cursor = 'pointer';
      c.addEventListener('click', () => onSelect && onSelect(inc.id));
      svg.appendChild(c);
    }
  }

  // Time axis ticks.
  const tickFmt = ms => {
    const d = new Date(ms);
    return `${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;
  };
  for (const frac of [0, 0.5, 1]) {
    const ts = tStart + frac * span;
    const t = document.createElementNS(svgNS, 'text');
    t.textContent = tickFmt(ts);
    t.setAttribute('x', leftPad + frac * (W - leftPad - rightPad));
    t.setAttribute('y', H - 6);
    t.setAttribute('font-size', '7'); t.setAttribute('fill', '#9aa5bd');
    t.setAttribute('text-anchor', frac === 1 ? 'end' : (frac === 0 ? 'start' : 'middle'));
    svg.appendChild(t);
  }

  rootEl.innerHTML = '';
  rootEl.appendChild(svg);
}
```

- [ ] **Step 2: Smoke compile**

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node -e "import('./static/js/sections/storyline.mjs').then(m => console.log('OK', Object.keys(m)))"`

- [ ] **Step 3: Commit**

```bash
git add static/js/sections/storyline.mjs
git commit -m "feat(incidents): Storyline section — sensor-lane subway map"
```

---

## Task 8: Co-occurrence section

**Files:**
- Create: `static/js/sections/cooccurrence.mjs`

Cytoscape force-directed graph from the same Storyline payload.

- [ ] **Step 1: Implement `static/js/sections/cooccurrence.mjs`**

```javascript
// Co-occurrence — sensors as nodes, edges between sensors that fire alerts
// within ±5 minutes of each other in the active window.
//
// Public:  renderCooccurrence(rootEl, { storylineData, edgePFloor,
//                                        sensorFilter, onSensorClick })

import { primaryChannel, CHANNEL_LABEL } from './sensor_map.mjs';

const COFIRE_WINDOW_MS = 5 * 60 * 1000;

function buildCounts(storylineData) {
  const sensorCounts = new Map();    // ch -> alert count
  const pairCounts   = new Map();    // 'a|b' -> count of co-fire occurrences
  const incidents = (storylineData && storylineData.incidents) || [];

  for (const inc of incidents) {
    const taggedAlerts = inc.alerts
      .map(a => ({ ts: Date.parse(a.created_at), ch: primaryChannel(a.event_type) }))
      .filter(a => Number.isFinite(a.ts) && a.ch);
    for (const a of taggedAlerts) sensorCounts.set(a.ch, (sensorCounts.get(a.ch) || 0) + 1);
    for (let i = 0; i < taggedAlerts.length; i++) {
      for (let j = i + 1; j < taggedAlerts.length; j++) {
        const a = taggedAlerts[i], b = taggedAlerts[j];
        if (Math.abs(a.ts - b.ts) > COFIRE_WINDOW_MS) continue;
        if (a.ch === b.ch) continue;
        const key = a.ch < b.ch ? `${a.ch}|${b.ch}` : `${b.ch}|${a.ch}`;
        pairCounts.set(key, (pairCounts.get(key) || 0) + 1);
      }
    }
  }
  return { sensorCounts, pairCounts };
}

export function renderCooccurrence(rootEl, opts) {
  const { storylineData, edgePFloor = 0.20, sensorFilter, onSensorClick } = opts || {};
  if (!rootEl || typeof cytoscape === 'undefined') return;

  const { sensorCounts, pairCounts } = buildCounts(storylineData);
  if (sensorCounts.size === 0) {
    rootEl.innerHTML = '<div class="inc-section-empty">No primary alerts in this window.</div>';
    return;
  }

  // Convert pair counts to a normalised P (count / max(count)) so the slider
  // shares semantics with Storyline's edge probabilities.
  const maxPair = Math.max(1, ...pairCounts.values());

  const elements = [];
  for (const [ch, count] of sensorCounts.entries()) {
    elements.push({
      data: {
        id: `co-${ch}`, label: CHANNEL_LABEL[ch] || ch, count, ch,
      },
      classes: sensorFilter === ch ? 'co-node selected' : 'co-node',
    });
  }
  for (const [key, count] of pairCounts.entries()) {
    const [a, b] = key.split('|');
    const p = count / maxPair;
    if (p < 0.01) continue;
    elements.push({
      data: { id: `coe-${key}`, source: `co-${a}`, target: `co-${b}`, p, count },
      classes: p < edgePFloor ? 'co-edge weak' : 'co-edge',
    });
  }

  rootEl.innerHTML = '';
  const container = document.createElement('div');
  container.style.width = '100%'; container.style.height = '100%';
  rootEl.appendChild(container);

  const cy = cytoscape({
    container,
    elements,
    layout: { name: 'cose', fit: true, padding: 24, animate: false },
    style: [
      { selector: 'node.co-node', style: {
        'background-color': '#1a3a66', 'border-width': 2, 'border-color': '#4dacff',
        'label': 'data(label)', 'color': '#cfe8ff', 'font-size': 9,
        'font-weight': 700, 'text-valign': 'center', 'text-halign': 'center',
        'width': ele => 14 + Math.min(20, (ele.data('count') || 1) * 2),
        'height': ele => 14 + Math.min(20, (ele.data('count') || 1) * 2),
      }},
      { selector: 'node.co-node.selected', style: {
        'border-width': 3, 'border-color': '#ffd23f',
      }},
      { selector: 'edge.co-edge', style: {
        'line-color': '#4dacff', 'curve-style': 'bezier',
        'width': ele => 0.8 + Math.min(4, (ele.data('count') || 1) * 0.5),
        'opacity': ele => Math.max(0.25, ele.data('p') || 0.5),
      }},
      { selector: 'edge.co-edge.weak', style: {
        'line-color': '#9aa5bd', 'line-style': 'dashed', 'opacity': 0.25,
      }},
    ],
    userZoomingEnabled: false,
    userPanningEnabled: false,
    boxSelectionEnabled: false,
  });

  cy.on('tap', 'node.co-node', evt => {
    const ch = evt.target.data('ch');
    if (onSensorClick) onSensorClick(ch);
  });
}
```

- [ ] **Step 2: Smoke compile**

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node -e "import('./static/js/sections/cooccurrence.mjs').then(m => console.log('OK', Object.keys(m)))"`

- [ ] **Step 3: Commit**

```bash
git add static/js/sections/cooccurrence.mjs
git commit -m "feat(incidents): Co-occurrence section — sensor force-directed graph"
```

---

## Task 9: Toolbar trim + filter chips + tooltips on section headers

**Files:**
- Modify: `templates/incidents.html`
- Modify: `static/css/incident_graph.css`

- [ ] **Step 1: Replace centre-pane HTML in `templates/incidents.html`**

The current `<div class="inc-graph-panel">` block (around lines 67-120 of `templates/incidents.html`) plus the existing summary strip (`#inc-graph-summary`), key (`#inc-graph-key`), minimap (`#cy-minimap`), and the `Manual / Compact / Chronological` buttons (`.inc-graph-controls`) — all retire.

Replace the whole `<div class="inc-graph-panel">` block with:

```html
    <!-- Centre: 4 stacked sub-sections -->
    <div class="inc-dashboard">

      <!-- Filter chips bar (empty by default) -->
      <div id="inc-filter-chips" class="inc-filter-chips" hidden></div>

      <!-- ① Galaxy + Rose side by side -->
      <div class="inc-section">
        <div class="inc-section-header">
          <span class="inc-section-title">Incident similarity &nbsp;·&nbsp; Daily rhythm</span>
          <span class="inc-section-meta" id="inc-band1-meta"></span>
        </div>
        <div class="inc-section-body inc-section-split">
          <div class="inc-section-pane">
            <div class="inc-section-subtitle">
              Galaxy <span class="info-icon" title="Each dot is one incident in the current window. Distance between dots = how similar their sensor signatures are. Clusters reveal recurring incident shapes (cooking, ventilation, humid drift). Dot colour = max severity. Outliers sit alone — click any dot to focus that incident.">ⓘ</span>
            </div>
            <div id="inc-galaxy" class="inc-section-canvas"></div>
          </div>
          <div class="inc-section-pane">
            <div class="inc-section-subtitle">
              Daily rhythm (UTC) <span class="info-icon" title="Incidents binned by the hour-of-day they started. Wedge length = count, colour = max severity in that hour. Long red wedges flag recurring critical-severity peaks (cooking ~18:00, etc). Click a wedge to filter every section to that hour.">ⓘ</span>
            </div>
            <div id="inc-rose" class="inc-section-canvas"></div>
          </div>
        </div>
      </div>

      <!-- ② Storyline -->
      <div class="inc-section">
        <div class="inc-section-header">
          <span class="inc-section-title">
            Storyline · sensor lanes × time
            <span class="info-icon" title="Time runs left to right across the active window. Each row is a sensor; each dot is an alert when that sensor crossed a threshold or anomaly. Curves connect alerts in the same incident — the curve's shape IS the incident's signature. Selected incident is solid blue; others are dashed grey. The 'Hide weak links' slider fades curves whose minimum edge probability falls below the threshold.">ⓘ</span>
          </span>
          <span class="inc-section-meta" id="inc-storyline-meta"></span>
        </div>
        <div class="inc-section-body">
          <div id="inc-storyline" class="inc-section-canvas"></div>
        </div>
      </div>

      <!-- ③ Co-occurrence -->
      <div class="inc-section">
        <div class="inc-section-header">
          <span class="inc-section-title">
            Sensor co-occurrence
            <span class="info-icon" title="Each circle is a sensor channel — its size = how many alerts it fired in this window. Lines join sensors that fired together (within 5 minutes). Thick lines = strong co-occurrence — e.g. TVOC + eCO₂ together usually means a cooking event. Click a sensor to filter the page to incidents touching that sensor; drag the slider to fade weak links.">ⓘ</span>
          </span>
          <span class="inc-section-meta" id="inc-cooccurrence-meta"></span>
        </div>
        <div class="inc-section-body">
          <div id="inc-cooccurrence" class="inc-section-canvas"></div>
        </div>
      </div>

    </div>
```

- [ ] **Step 2: Add CSS for the new sections**

Append to `static/css/incident_graph.css`:

```css
.inc-dashboard {
  display: flex; flex-direction: column;
  flex: 1; min-height: 0; overflow-y: auto;
  background: #0d1117;
}
.inc-filter-chips {
  padding: 4px 12px; display: flex; gap: 6px; flex-wrap: wrap;
  border-bottom: 1px solid #2a3346; min-height: 28px; align-items: center;
}
.inc-filter-chips .chip {
  background: rgba(77, 172, 255, 0.15);
  border: 1px solid rgba(77, 172, 255, 0.45);
  color: #cfe8ff; font-size: 0.7rem;
  padding: 2px 8px; border-radius: 10px;
  display: inline-flex; gap: 4px; align-items: center;
}
.inc-filter-chips .chip-close { cursor: pointer; opacity: 0.6; }
.inc-filter-chips .chip-close:hover { opacity: 1; color: #ff7a7a; }
.inc-section { display: flex; flex-direction: column; border-bottom: 1px solid #2a3346; }
.inc-section:last-child { border-bottom: none; }
.inc-section-header {
  padding: 6px 12px; display: flex; justify-content: space-between; align-items: center;
  font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.05em;
  color: #9aa5bd; background: rgba(13, 17, 23, 0.6);
}
.inc-section-title { font-weight: 700; }
.inc-section-meta { color: #7a8497; }
.inc-section-body { flex: 1; min-height: 170px; }
.inc-section-split { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: #2a3346; }
.inc-section-pane { background: #0d1117; padding: 6px 8px; position: relative; min-height: 170px; }
.inc-section-subtitle {
  position: absolute; left: 8px; top: 4px;
  font-size: 0.6rem; color: #7a8497; z-index: 2;
}
.inc-section-canvas { width: 100%; height: 100%; min-height: 160px; }
#inc-storyline.inc-section-canvas { min-height: 220px; }
.inc-section-empty {
  display: flex; align-items: center; justify-content: center;
  height: 100%; color: #7a8497; font-style: italic; font-size: 0.78rem;
  padding: 20px;
}
```

Find and DELETE these existing rules tied to the retired grid layout: `.inc-graph-panel`, `.inc-graph-controls`, `.inc-graph-summary`, `.inc-graph-key`, `.inc-graph-minimap`, `.inc-ctrl-btn`, `.inc-layout-btn`, `.inc-edge-slider` (the slider stays — bare-element styling only). Some of these names may still be referenced elsewhere; let the full text-search run during cleanup find them.

- [ ] **Step 3: Run pytest to confirm no template error**

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pytest tests/ -q`
Expected: green (template syntax errors would crash on app boot in tests).

- [ ] **Step 4: Commit**

```bash
git add templates/incidents.html static/css/incident_graph.css
git commit -m "feat(incidents): replace centre-pane HTML with four-section dashboard

AstroUX rux-tooltip via info-icon[title] auto-wrap explains each
section. Removes the grid-of-hulls block, view-mode buttons,
summary strip, symbol key, and minimap — all replaced by the
Galaxy/Rose/Storyline/Co-occurrence sub-sections."
```

---

## Task 10: Orchestrator + cross-filter wiring + dead-code cleanup

**Files:**
- Modify: `static/js/incident_graph.js` — gut centroid/Cytoscape grid plumbing, wire sub-sections
- Delete: `static/js/compute_centroids.mjs`, `tests/js/test_compute_centroids.mjs`

This is the largest task. The implementer should treat it as a refactor: keep the existing helpers that still apply (incident-list rendering, detail-panel rendering, tag controls, search/window/severity wiring), and replace the centre-canvas plumbing.

- [ ] **Step 1: Identify what stays vs what goes**

In `static/js/incident_graph.js`, KEEP:
- All imports except `compute_centroids.mjs` and `connected_components.mjs`
- Module-level state: `cy` is gone; keep `currentDetail`, `allIncidents`, `currentIncidentId`, `searchQuery`, `activeWindow`, `activeSeverity`, `edgePFloor`, `tagVocab`, `TAG_EMOJI`
- DOM element refs at top of file
- `initToolbar()` — but rip out the view-mode block + slider edge-count update path
- `loadIncidents()`, `applyClientFilter()`, `renderList()` — incident list rendering
- `loadIncidentDetail(id)` and the entire detail-panel render path (narrative, causal sequence, similar incidents, node overlay, tags)
- `html` tagged template
- `fetchTagVocab()`, `populateTagsSection()`, etc.

REMOVE:
- The Cytoscape init / minimap / `_miniTeardown` / fit-to-selected helpers
- `buildCentroids`, `buildIncidentElements`, `buildCytoscapeStyle`, `applyEdgePStyling`, `applySubdivisionPreview`, `applySelectionOpacity`, `applyZoomClasses`, `restorePositions`, `loadSavedPosition`, `saveNodePosition`
- `renderGraph(detail, incidents)` — replaced by `renderDashboard()`
- The tl2 localStorage prefix block (no per-node positions anymore)

- [ ] **Step 2: Add the new orchestrator**

Add to `static/js/incident_graph.js` (after the imports, alongside other module state):

```javascript
import { renderGalaxy }       from './sections/galaxy.mjs';
import { renderRose }         from './sections/rose.mjs';
import { renderStoryline }    from './sections/storyline.mjs';
import { renderCooccurrence } from './sections/cooccurrence.mjs';

// Active filter chips. Cleared on window change.
let filterHour = null;     // 0..23 or null
let filterSensor = null;   // channel id or null
let storylineData = null;  // last fetched payload from /storyline endpoint

async function fetchStorylineData() {
  try {
    const url = `/api/incidents/storyline?window=${encodeURIComponent(activeWindow)}`
              + `&severity=${encodeURIComponent(activeSeverity)}`;
    const r = await fetch(url);
    storylineData = r.ok ? await r.json() : { incidents: [] };
  } catch (_) { storylineData = { incidents: [] }; }
}

function renderDashboard() {
  // Galaxy + Rose + Storyline + Co-occurrence — every section is a function
  // of the cached data already fetched. Re-render is cheap.
  const galaxyEl = document.getElementById('inc-galaxy');
  const roseEl = document.getElementById('inc-rose');
  const storyEl = document.getElementById('inc-storyline');
  const coEl = document.getElementById('inc-cooccurrence');

  // Filter the visible incidents by the active filter chips before passing
  // to each section (so Galaxy fades non-matching dots etc).
  const filteredIncidents = applyChipFilters(allIncidents);

  if (galaxyEl) renderGalaxy(galaxyEl, {
    incidents: filteredIncidents,
    selectedId: currentIncidentId,
    onSelect: id => loadIncidentDetail(id),
  });

  // hour_histogram + severity_by_hour live in the list summary.
  if (roseEl && lastListSummary) renderRose(roseEl, {
    hour_histogram: lastListSummary.hour_histogram,
    severity_by_hour: lastListSummary.severity_by_hour,
    selectedHour: filterHour,
    onSelect: h => { filterHour = (filterHour === h ? null : h); renderChips(); renderDashboard(); },
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
      renderChips(); renderDashboard();
    },
  });
}
```

Add helpers (`applyChipFilters`, `renderChips`, `currentWindowRange`) as small functions; the implementer can choose exact form. Aim for ≤80 lines total.

- [ ] **Step 3: Make `loadIncidents` also fetch storyline data**

```javascript
async function loadIncidents() {
  // ... existing fetch to /api/incidents ...
  lastListSummary = data.summary || null;
  // ... existing renderList ...
  await fetchStorylineData();
  renderDashboard();
}
```

- [ ] **Step 4: Replace the slider's render hook**

Slider input handler: just call `renderDashboard()` (the section modules read `edgePFloor` from module state).

- [ ] **Step 5: Delete compute_centroids files**

```bash
git rm static/js/compute_centroids.mjs tests/js/test_compute_centroids.mjs
```

- [ ] **Step 6: Run all tests**

```
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pytest tests/ -q
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_pca.mjs
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_sensor_map.mjs
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_connected_components.mjs
```

All green. Existing pytest count holds + new endpoint tests added.

Browser smoke test on Pi after deploy (manual — DOM-bound):
- Click incidents → detail panel updates as before, Galaxy/Storyline highlight that incident
- Click Rose wedge → filter chip appears, sections rebuild
- Click sensor in Co-occurrence → second filter chip, sections rebuild
- Slider drag → Storyline curves + Co-occurrence edges fade continuously
- Switch window 24h → 14d → all four sections rebuild from new data

- [ ] **Step 7: Commit**

```bash
git add static/js/incident_graph.js
git commit -m "refactor(incidents): orchestrator wires four sub-sections

Replaces the Cytoscape grid-of-hulls plumbing with a thin
orchestrator that fetches the list summary + storyline batch
once per window/severity change and dispatches to four pure
section modules. Cross-filter chips (hour-of-day, sensor) live in
module state; clicking any section toggles a chip, page rebuilds.

Net delta: ~600 lines removed from incident_graph.js (centroid
math, Cytoscape style, render-event loops, save-position
helpers) and replaced with ~120 lines of orchestration."
```

---

## Verification (after all tasks)

```
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pytest tests/ -q
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_pca.mjs
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_sensor_map.mjs
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_connected_components.mjs
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pylint --disable=import-error,no-name-in-module \
  mlss_monitor/routes/api_incidents.py tests/test_api_incidents.py
```

Expected:
- pytest ≥ existing count + 3 new (signature retention, severity_by_hour, storyline endpoint)
- All Node fixture tests green
- pylint 10.00/10 on touched Python files

Browser smoke test (after `git pull && sudo systemctl restart mlss-monitor` on the Pi, hard-refresh):

- Centre canvas shows four sub-sections (Galaxy + Rose top, Storyline middle, Co-occurrence bottom)
- Each section's `ⓘ` icon shows the AstroUX tooltip on hover
- Clicking any section's primary element propagates to others
- "Hide weak links" slider fades Storyline curves + Co-occurrence edges in lockstep
- Window selector / severity filter rebuild every section
- Detail panel + tagging UI unchanged
