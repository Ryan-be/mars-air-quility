# UI Redesign Phase 2 — Dashboard & History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle `dashboard.css`, `dashboard.html`, `history.css`, and `history.html` to AstroUXDS design language using the CSS tokens defined in Phase 1's `base.css` rewrite.

**Architecture:** CSS files are fully rewritten to consume Phase 1 tokens (e.g. `--color-background-surface-default`, `--color-interactive-default`, `--color-status-normal`). HTML files get AstroUXDS structural classes added while preserving every existing element ID and JS hook verbatim. Tests assert on CSS class presence in the rendered HTML; they never assert on visual pixel values.

**Tech Stack:** Python/Flask (Jinja2 templates), pytest, BeautifulSoup4, plain CSS (no preprocessor).

---

## Files Touched

| Action   | Path |
|----------|------|
| Create   | `tests/test_ui_dashboard.py` |
| Modify   | `static/css/dashboard.css` |
| Modify   | `templates/dashboard.html` |
| Create   | `tests/test_ui_history.py` |
| Modify   | `static/css/history.css` |
| Modify   | `templates/history.html` |

---

### Task 1: Write failing tests for dashboard HTML structure

**Files:**
- Create: `tests/test_ui_dashboard.py`

These tests confirm the AstroUXDS structural classes exist in the rendered HTML. They will fail until Task 3 adds those classes.

- [ ] **Step 1: Write the failing tests**

Create `/c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility/.claude/worktrees/gifted-bhabha/tests/test_ui_dashboard.py`:

```python
"""UI structure tests for the dashboard page.

These tests assert that AstroUXDS CSS classes have been applied to the
dashboard HTML. They fail until Task 3 (HTML update) is complete.
"""
from bs4 import BeautifulSoup


def test_stat_grid_has_astro_class(app_client):
    client, _ = app_client
    resp = client.get("/")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.data, "html.parser")
    assert soup.find(class_="stat-grid") is not None, "stat-grid wrapper missing"


def test_stat_cards_have_astro_status_bar_class(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    cards = soup.find_all(class_="stat-card")
    assert len(cards) >= 6, f"Expected at least 6 stat cards, got {len(cards)}"
    for card in cards:
        assert "astro-card" in card.get("class", []), (
            f"stat-card missing 'astro-card' class: {card.get('class')}"
        )


def test_insight_cards_have_astro_class(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    cards = soup.find_all(class_="insight-card")
    assert len(cards) >= 6, f"Expected at least 6 insight cards, got {len(cards)}"
    for card in cards:
        assert "astro-card" in card.get("class", []), (
            f"insight-card missing 'astro-card' class: {card.get('class')}"
        )


def test_health_card_has_astro_class(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    health = soup.find(class_="health-card")
    assert health is not None, "health-card div missing"
    assert "astro-card" in health.get("class", []), "health-card missing 'astro-card' class"


def test_inference_feed_exists(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    assert soup.find(id="inferenceFeed") is not None, "#inferenceFeed missing"


def test_inference_dialog_preserved(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    dlg = soup.find("dialog", {"id": "inferenceDialog"})
    assert dlg is not None, "inferenceDialog missing"
    assert soup.find(id="infTitle") is not None, "#infTitle missing"
    assert soup.find(id="infEvidence") is not None, "#infEvidence missing"
    assert soup.find(id="infSparklineChart") is not None, "#infSparklineChart missing"


def test_sensor_dialog_preserved(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    assert soup.find("dialog", {"id": "sensorDialog"}) is not None, "sensorDialog missing"
    assert soup.find(id="sdSensor") is not None, "#sdSensor missing"
    assert soup.find(id="sdCurrent") is not None, "#sdCurrent missing"


def test_forecast_dialogs_preserved(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    assert soup.find("dialog", {"id": "forecastDialog"}) is not None
    assert soup.find("dialog", {"id": "forecastDailyDialog"}) is not None
    assert soup.find(id="fdTemp") is not None, "#fdTemp missing"
    assert soup.find(id="fddHigh") is not None, "#fddHigh missing"


def test_all_sensor_value_ids_preserved(app_client):
    """JS reads these IDs; they must survive the HTML rewrite."""
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    required_ids = [
        "tempValue", "humValue", "eco2Value", "tvocValue",
        "gasCoValue", "gasNo2SubValue", "gasNh3SubValue", "gasTrend",
        "pm25Value", "pm1SubValue", "pm10SubValue", "pmStaleHint",
        "aqValue", "aqSub", "dewValue", "hiValue",
        "co2AlertValue", "co2AlertSub", "vpdValue", "tttValue",
        "aht20Status", "sgp30Status", "pmStatus", "mics6814Status",
        "plugStatus", "cpuUsage", "memoryUsage", "diskUsage",
        "dbSize", "uptime", "serviceUptime",
        "inferenceCount", "inferenceFeed", "inferenceFilters",
        "infSeverity", "infTime", "infConfidence",
        "infDescription", "infEvidence", "infAction",
        "infNotes", "infSaveNote", "infThresholds",
    ]
    for el_id in required_ids:
        assert soup.find(id=el_id) is not None, f"#{el_id} missing from dashboard HTML"


def test_section_headings_use_astro_class(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    headings = soup.find_all(class_="section-heading")
    assert len(headings) >= 3, "Expected at least 3 section headings"


def test_inference_filter_buttons_present(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    filters = soup.find_all(class_="inf-filter")
    categories = [b.get("data-category") for b in filters]
    for cat in ["all", "alert", "warning", "pattern", "anomaly", "attribution", "summary"]:
        assert cat in categories, f"inf-filter for category '{cat}' missing"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility/.claude/worktrees/gifted-bhabha
pytest tests/test_ui_dashboard.py -v 2>&1 | tail -30
```

Expected output: multiple `FAILED` lines. `test_all_sensor_value_ids_preserved` and `test_inference_filter_buttons_present` will pass (IDs already exist); `test_stat_cards_have_astro_status_bar_class` and `test_insight_cards_have_astro_class` will fail (class not yet added).

---

### Task 2: Rewrite `static/css/dashboard.css` with AstroUXDS tokens

**Files:**
- Modify: `static/css/dashboard.css`

This is a full rewrite. Every hardcoded hex colour becomes a token reference. The visual structure is preserved; only the colour/spacing values change to consume Phase 1 tokens.

- [ ] **Step 1: Replace the full contents of `static/css/dashboard.css`**

Replace the entire file with:

```css
/* ══════════════════════════════════════════════════════════════════════════
   dashboard.css  —  AstroUXDS-styled dashboard page
   Depends on base.css for all --color-* and --spacing-* tokens.
   ══════════════════════════════════════════════════════════════════════════ */

/* ── Top bar extras ── */
.top-bar .controls { display: flex; align-items: center; gap: 0.5em; flex-wrap: wrap; }
.top-bar select {
  background: var(--color-background-surface-default, #1b2d3e);
  color: var(--color-text-primary, #ffffff);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 4px;
  padding: 0.35em 0.6em;
  font-size: 0.9em;
  font-family: inherit;
}
#last-updated { margin: 0; font-size: 0.8em; color: var(--color-text-secondary, #85a5c1); font-style: italic; }

/* ══ AstroUXDS base card ════════════════════════════════════════════════ */
.astro-card {
  background: var(--color-background-surface-default, #1b2d3e);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;               /* AstroUXDS uses sharp corners */
  padding: 0.9em 1em;
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
  transition: border-color 0.15s, background 0.15s;
  width: 100%;
}
.astro-card:hover,
.astro-card:focus-visible {
  border-color: var(--color-interactive-default, #4dacff);
  background: var(--color-background-surface-hover, #223f5a);
  outline: none;
}

/* ══ Sensor stat grid ═══════════════════════════════════════════════════ */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 0.75em;
  margin-bottom: 0.75em;
}

/* Status accent bar: 4px left border in AstroUXDS status colour */
.stat-card {
  border-left-width: 4px;
  border-left-style: solid;
  border-left-color: var(--color-status-standby, #2dccff);
  border-top: none;
  padding-left: 0.85em;
}
.stat-card.temp  { border-left-color: var(--color-status-serious, #ffb302); }
.stat-card.hum   { border-left-color: var(--color-interactive-default, #4dacff); }
.stat-card.eco2  { border-left-color: var(--color-status-normal, #56f000); }
.stat-card.tvoc  { border-left-color: var(--color-status-caution, #fce83a); }
.stat-card.gas   { border-left-color: var(--color-status-standby, #2dccff); }
.stat-card.gas.gas-good { border-left-color: var(--color-status-normal, #56f000); }
.stat-card.gas.gas-warn { border-left-color: var(--color-status-caution, #fce83a); }
.stat-card.gas.gas-bad  { border-left-color: var(--color-status-critical, #ff3838); }
.stat-card.pm    { border-left-color: #a78bfa; }
.stat-card.pm.pm-good      { border-left-color: var(--color-status-normal, #56f000); }
.stat-card.pm.pm-moderate  { border-left-color: var(--color-status-caution, #fce83a); }
.stat-card.pm.pm-unhealthy { border-left-color: var(--color-status-critical, #ff3838); }

.stat-card .label {
  font-size: 0.7em;
  color: var(--color-text-secondary, #85a5c1);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 0.3em;
}
.stat-card .current {
  font-size: 1.9em;
  font-weight: 600;
  line-height: 1.1;
  margin-bottom: 0.35em;
  color: var(--color-text-primary, #ffffff);
}

/* Gas sub-row */
.gas-trend { font-size: 0.8em; color: var(--color-text-secondary, #85a5c1); margin-bottom: 0.2em; }
.gas-trend.gas-good { color: var(--color-status-normal, #56f000); }
.gas-trend.gas-warn { color: var(--color-status-caution, #fce83a); }
.gas-trend.gas-bad  { color: var(--color-status-critical, #ff3838); }

/* PM sub-row */
.pm-sub-row { display: flex; gap: 0.75em; font-size: 0.75em; color: var(--color-text-secondary, #85a5c1); margin-top: 0.15em; }
.pm-sub { white-space: nowrap; }
.pm-stale-hint { font-size: 0.7em; color: var(--color-status-caution, #fce83a); margin-top: 0.15em; display: none; }
.pm-stale-hint.visible { display: block; }

/* ══ Insight grid ═══════════════════════════════════════════════════════ */
.insight-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 0.75em;
  margin-bottom: 1.25em;
}
.insight-card {
  border-left: 4px solid var(--color-border-interactive-default, #2b659b);
  border-top: none;
  padding-left: 0.85em;
}
.insight-card .label {
  font-size: 0.7em;
  color: var(--color-text-secondary, #85a5c1);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 0.3em;
}
.insight-card .value {
  font-size: 1.6em;
  font-weight: 600;
  line-height: 1.1;
  margin-bottom: 0.35em;
  color: var(--color-text-primary, #ffffff);
}
.insight-card .value.ok,
.insight-card .value.neutral { color: var(--color-text-primary, #ffffff); }
.insight-card .value.good    { color: var(--color-status-normal, #56f000); }
.insight-card .value.caution { color: var(--color-status-caution, #fce83a); }
.insight-card .value.warning { color: var(--color-status-serious, #ffb302); }
.insight-card .value.critical { color: var(--color-status-critical, #ff3838); }
.insight-card .sub { font-size: 0.78em; color: var(--color-text-secondary, #85a5c1); }

/* ══ Section headings ═══════════════════════════════════════════════════ */
.section-heading {
  font-size: 0.72em;
  color: var(--color-text-secondary, #85a5c1);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin: 1em 0 0.5em;
  padding-left: 0;
  border-bottom: 1px solid var(--color-border-interactive-muted, #182f45);
  padding-bottom: 0.35em;
}

/* ══ Outdoor weather section ════════════════════════════════════════════ */
.weather-section { margin-bottom: 1.25em; }
.weather-updated { color: var(--color-text-secondary, #85a5c1); font-size: 0.9em; margin-left: auto; }
.weather-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 0.75em;
}

/* ══ Forecast strip ═════════════════════════════════════════════════════ */
.forecast-heading {
  font-size: 0.72em;
  color: var(--color-text-secondary, #85a5c1);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin: 1em 0 0.4em;
}
.forecast-strip {
  display: flex;
  gap: 0.5em;
  overflow-x: auto;
  padding-bottom: 0.4em;
  scrollbar-width: thin;
  scrollbar-color: var(--color-border-interactive-default, #2b659b) transparent;
}
.forecast-strip::-webkit-scrollbar       { height: 4px; }
.forecast-strip::-webkit-scrollbar-thumb { background: var(--color-border-interactive-default, #2b659b); border-radius: 2px; }

.forecast-slot {
  flex: 0 0 auto;
  background: var(--color-background-surface-default, #1b2d3e);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  padding: 0.55em 0.8em;
  text-align: center;
  min-width: 62px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.forecast-slot:hover,
.forecast-slot:focus-visible {
  border-color: var(--color-interactive-default, #4dacff);
  background: var(--color-background-surface-hover, #223f5a);
  outline: none;
}
.forecast-slot .fc-time { font-size: 0.68em; color: var(--color-text-secondary, #85a5c1); margin-bottom: 0.15em; }
.forecast-slot .fc-icon { font-size: 1.35em; line-height: 1.3; }
.forecast-slot .fc-temp { font-size: 0.9em; font-weight: 600; color: var(--color-text-primary, #ffffff); margin-top: 0.1em; }
.forecast-slot .fc-rain { font-size: 0.68em; color: var(--color-interactive-default, #4dacff); margin-top: 0.1em; }
.fc-lo { color: var(--color-text-secondary, #85a5c1); font-weight: normal; }

/* ══ Dialogs (forecast, sensor, insight, inference) ════════════════════ */
dialog {
  background: var(--color-background-surface-default, #1b2d3e);
  color: var(--color-text-primary, #ffffff);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  padding: 1.2em;
  max-width: 400px;
  width: 90vw;
  box-shadow: 0 8px 32px rgba(0,0,0,0.6);
}
dialog::backdrop { background: rgba(0,0,0,0.7); }
dialog textarea {
  width: 100%;
  background: var(--color-background-base-default, #101923);
  color: var(--color-text-primary, #ffffff);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  padding: 0.5em;
  font-size: 0.9em;
  font-family: inherit;
  resize: vertical;
}
dialog textarea:focus {
  outline: none;
  border-color: var(--color-interactive-default, #4dacff);
}

#forecastDialog,
#forecastDailyDialog { max-width: 340px; padding: 1.4em 1.6em; }
#sensorDialog        { max-width: 360px; }
#insightDialog       { max-width: 360px; }
#inferenceDialog     { max-width: 520px; max-height: 85vh; overflow-y: auto; }

.fd-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 0.75em;
  margin-bottom: 1em;
}
.fd-header > div { display: flex; align-items: center; gap: 0.5em; }
.fd-icon  { font-size: 2em; line-height: 1; }
.fd-header h3 { margin: 0; font-size: 1em; color: var(--color-text-primary, #ffffff); }
.fd-close {
  padding: 0.25em 0.6em;
  font-size: 0.95em;
  flex-shrink: 0;
  background: transparent;
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  color: var(--color-text-secondary, #85a5c1);
  border-radius: 3px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.fd-close:hover { border-color: var(--color-interactive-default, #4dacff); color: var(--color-text-primary, #ffffff); }

.fd-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75em; }
.fd-item {
  background: var(--color-background-base-default, #101923);
  border-radius: 3px;
  padding: 0.6em 0.75em;
}
.fd-label { font-size: 0.7em; color: var(--color-text-secondary, #85a5c1); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.2em; }
.fd-value { font-size: 1.1em; font-weight: 600; color: var(--color-text-primary, #ffffff); }

/* ── Sensor detail dialog ── */
.sd-body { display: flex; flex-direction: column; gap: 0.6em; }
.sd-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--color-background-base-default, #101923);
  border-radius: 3px;
  padding: 0.5em 0.75em;
}
.sd-row .fd-label { font-size: 0.75em; }
.sd-desc {
  font-size: 0.85em;
  color: var(--color-text-secondary, #85a5c1);
  line-height: 1.5;
  padding: 0.4em 0;
}

/* ── Inference detail dialog ── */
.inf-body { display: flex; flex-direction: column; gap: 0.9em; }
.inf-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5em;
  padding-bottom: 0.75em;
  border-bottom: 1px solid var(--color-border-interactive-muted, #182f45);
}
.inf-badge {
  font-size: 0.72em;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  padding: 2px 8px;
  border-radius: 2px;
  background: var(--color-status-standby, #2dccff);
  color: #000;
}
.inf-badge.sev-critical { background: var(--color-status-critical, #ff3838); color: #fff; }
.inf-badge.sev-warning,
.inf-badge.sev-serious  { background: var(--color-status-serious, #ffb302); color: #000; }
.inf-badge.sev-caution  { background: var(--color-status-caution, #fce83a); color: #000; }
.inf-badge.sev-info,
.inf-badge.sev-normal   { background: var(--color-status-normal, #56f000); color: #000; }

.inf-detection-chip { font-size: 0.75em; color: var(--color-text-secondary, #85a5c1); }
.inf-time           { font-size: 0.75em; color: var(--color-text-secondary, #85a5c1); margin-left: auto; }
.inf-confidence     { font-size: 0.75em; color: var(--color-text-secondary, #85a5c1); }

.inf-section { display: flex; flex-direction: column; gap: 0.4em; }
.inf-section-title {
  font-size: 0.72em;
  color: var(--color-text-secondary, #85a5c1);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  display: flex;
  align-items: center;
  gap: 0.4em;
}
.inf-evidence {
  font-size: 0.88em;
  color: var(--color-text-secondary, #85a5c1);
  line-height: 1.6;
}
.inf-thresholds-section { border: none; padding: 0; }
.inf-thresholds-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 0.4em;
  font-size: 0.82em;
}
.inf-save-note {
  margin-top: 0.5em;
  align-self: flex-start;
  background: var(--color-interactive-default, #4dacff);
  color: #000;
  border: none;
  border-radius: 3px;
  padding: 0.35em 0.9em;
  font-size: 0.85em;
  cursor: pointer;
  font-family: inherit;
  font-weight: 600;
  transition: opacity 0.15s;
}
.inf-save-note:hover { opacity: 0.85; }

/* Tags section in inference dialog */
.inf-tags-list { display: flex; flex-wrap: wrap; gap: 0.4em; margin-bottom: 0.5em; min-height: 1.5em; }
.inf-tag-controls { display: flex; gap: 0.5em; align-items: center; flex-wrap: wrap; }
.inf-tag-select {
  background: var(--color-background-base-default, #101923);
  color: var(--color-text-primary, #ffffff);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  padding: 0.35em 0.5em;
  font-size: 0.85em;
  font-family: inherit;
}
.inf-add-tag {
  background: transparent;
  border: 1px solid var(--color-interactive-default, #4dacff);
  color: var(--color-interactive-default, #4dacff);
  border-radius: 3px;
  padding: 0.35em 0.75em;
  font-size: 0.85em;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.15s;
}
.inf-add-tag:hover { background: var(--color-background-surface-hover, #223f5a); }
.inf-fv-body { font-size: 0.82em; color: var(--color-text-secondary, #85a5c1); }

/* Sparkline container */
.sparkline-container {
  background: var(--color-background-base-default, #101923);
  border: 1px solid var(--color-border-interactive-muted, #182f45);
  border-radius: 3px;
  padding: 0.75em;
}
.sparkline-header {
  font-size: 0.78em;
  color: var(--color-text-secondary, #85a5c1);
  margin-bottom: 0.5em;
  display: flex;
  align-items: center;
  gap: 0.4em;
}

/* ══ Inference feed ══════════════════════════════════════════════════════ */
.inference-count {
  font-size: 0.85em;
  background: var(--color-interactive-default, #4dacff);
  color: #000;
  border-radius: 10px;
  padding: 1px 7px;
  margin-left: 0.5em;
  font-weight: 600;
}
.inference-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35em;
  margin-bottom: 0.75em;
}
.inf-filter {
  background: transparent;
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  color: var(--color-text-secondary, #85a5c1);
  border-radius: 2px;
  padding: 0.3em 0.75em;
  font-size: 0.8em;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.inf-filter:hover { background: var(--color-background-surface-hover, #223f5a); color: var(--color-text-primary, #ffffff); }
.inf-filter.active {
  background: var(--color-interactive-default, #4dacff);
  border-color: var(--color-interactive-default, #4dacff);
  color: #000;
  font-weight: 600;
}
.inference-feed { display: flex; flex-direction: column; gap: 0.4em; margin-bottom: 1.25em; }
.inference-empty { font-size: 0.88em; color: var(--color-text-secondary, #85a5c1); padding: 1em 0; }

/* ══ System health card ════════════════════════════════════════════════= */
.health-card {
  padding: 1em 1.2em;
  margin-bottom: 1em;
}
.health-card h3 {
  margin: 0 0 0.8em;
  font-size: 0.85em;
  color: var(--color-text-secondary, #85a5c1);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  border-bottom: 1px solid var(--color-border-interactive-muted, #182f45);
  padding-bottom: 0.5em;
}
.health-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 0.6em;
}
.health-item {
  background: var(--color-background-base-default, #101923);
  border: 1px solid var(--color-border-interactive-muted, #182f45);
  border-radius: 3px;
  padding: 0.5em 0.7em;
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
  transition: border-color 0.15s;
}
.health-item:hover,
.health-item:focus-visible { border-color: var(--color-interactive-default, #4dacff); outline: none; }
.health-item .h-label { font-size: 0.7em; color: var(--color-text-secondary, #85a5c1); text-transform: uppercase; letter-spacing: 0.05em; }
.health-item .h-value { font-size: 0.95em; color: var(--color-text-primary, #ffffff); margin-top: 0.15em; }

/* ══ Utility: info icon ══════════════════════════════════════════════════ */
.info-icon {
  cursor: help;
  color: var(--color-interactive-default, #4dacff);
  font-size: 0.85em;
  opacity: 0.8;
}
.info-icon:hover { opacity: 1; }

/* ══ Dialog buttons ══════════════════════════════════════════════════════ */
.dialog-buttons { display: flex; justify-content: flex-end; gap: 0.5em; margin-top: 0.8em; }
.dialog-buttons button {
  padding: 0.35em 0.9em;
  border-radius: 3px;
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  cursor: pointer;
  font-size: 0.9em;
  font-family: inherit;
  transition: opacity 0.15s;
}
.dialog-buttons .btn-save   { background: var(--color-interactive-default, #4dacff); color: #000; font-weight: 600; border-color: var(--color-interactive-default, #4dacff); }
.dialog-buttons .btn-delete { background: var(--color-status-critical, #ff3838); color: #fff; border-color: var(--color-status-critical, #ff3838); }
.dialog-buttons .btn-cancel { background: transparent; color: var(--color-text-secondary, #85a5c1); }
.dialog-buttons button:hover { opacity: 0.85; }

/* ══ Plots ═══════════════════════════════════════════════════════════════ */
.plot-container { display: flex; flex-direction: column; gap: 0.75em; margin-bottom: 0.75em; }
.plot-container > div { width: 100%; min-height: 300px; }

/* ══ TVOC key ════════════════════════════════════════════════════════════ */
.tvoc-key {
  background: var(--color-background-surface-default, #1b2d3e);
  border: 1px solid var(--color-border-interactive-muted, #182f45);
  border-radius: 3px;
  padding: 0.5em 1em;
  display: flex;
  align-items: center;
  gap: 1.5em;
  flex-wrap: wrap;
  margin-bottom: 1.25em;
  font-size: 0.8em;
  color: var(--color-text-secondary, #85a5c1);
}
.tvoc-key-title {
  color: var(--color-text-secondary, #85a5c1);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-size: 0.9em;
  white-space: nowrap;
}
.tvoc-levels { display: flex; gap: 1.2em; flex-wrap: wrap; }
.tvoc-level  { display: flex; align-items: center; gap: 0.35em; }
.color-dot   { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.color-dot.green  { background: var(--color-status-normal, #56f000); }
.color-dot.orange { background: var(--color-status-caution, #fce83a); }
.color-dot.red    { background: var(--color-status-critical, #ff3838); }

/* ══ Fan card (legacy — controls page also uses this) ════════════════════ */
.fan-card {
  background: var(--color-background-surface-default, #1b2d3e);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  padding: 1em 1.2em;
  margin-bottom: 1.25em;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.75em;
}
.fan-card h3 { margin: 0; font-size: 1em; }
.fan-buttons { display: flex; gap: 0.5em; }
.fan-buttons button {
  padding: 0.4em 1em;
  border-radius: 3px;
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  cursor: pointer;
  font-size: 0.9em;
  font-family: inherit;
  transition: opacity 0.15s;
}
.fan-buttons .btn-on   { background: var(--color-status-normal, #56f000); color: #000; font-weight: 600; }
.fan-buttons .btn-off  { background: var(--color-status-critical, #ff3838); color: #fff; }
.fan-buttons .btn-auto { background: var(--color-interactive-default, #4dacff); color: #000; font-weight: 600; }
.fan-buttons button:hover { opacity: 0.85; }
.fan-meta { display: flex; gap: 1.5em; font-size: 0.85em; color: var(--color-text-secondary, #85a5c1); }
.fan-meta span { display: flex; flex-direction: column; }
.fan-meta span strong { color: var(--color-text-primary, #ffffff); font-size: 1.1em; }
```

- [ ] **Step 2: Verify no syntax errors by loading the page in Flask dev mode**

```bash
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility/.claude/worktrees/gifted-bhabha
python -c "from app import create_app; app = create_app(); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit the CSS rewrite**

```bash
git add static/css/dashboard.css
git commit -m "style: rewrite dashboard.css with AstroUXDS tokens"
```

---

### Task 3: Update `templates/dashboard.html` — add AstroUXDS classes

**Files:**
- Modify: `templates/dashboard.html`

Preserve every `id=` attribute and every `data-*` attribute verbatim. Add `astro-card` class to all interactive card elements.

- [ ] **Step 1: Add `astro-card` to stat cards**

In `templates/dashboard.html`, change every `<button class="stat-card ...">` to include `astro-card`. There are 6 stat cards. Apply this pattern:

```html
<!-- BEFORE -->
<button class="stat-card temp" data-sensor="temp">

<!-- AFTER -->
<button class="stat-card astro-card temp" data-sensor="temp">
```

Apply to all 6: `temp`, `hum`, `eco2`, `tvoc`, `gas`, `pm`.

- [ ] **Step 2: Add `astro-card` to insight cards**

Each `<button class="insight-card"` becomes `<button class="insight-card astro-card"`. There are 6 insight cards inside `.insight-grid` and 5 inside `.weather-grid`.

- [ ] **Step 3: Add `astro-card` to the health card wrapper**

```html
<!-- BEFORE -->
<div class="health-card">

<!-- AFTER -->
<div class="health-card astro-card">
```

- [ ] **Step 4: Run the dashboard tests — all should pass**

```bash
pytest tests/test_ui_dashboard.py -v
```

Expected: all tests `PASSED`.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

```bash
pytest --ignore=tests/test_pi_resilience.py -q 2>&1 | tail -20
```

Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
git add templates/dashboard.html
git commit -m "style: add AstroUXDS classes to dashboard.html"
```

---

### Task 4: Write failing tests for history HTML structure

**Files:**
- Create: `tests/test_ui_history.py`

- [ ] **Step 1: Write the failing tests**

Create `/c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility/.claude/worktrees/gifted-bhabha/tests/test_ui_history.py`:

```python
"""UI structure tests for the history page.

These tests assert that AstroUXDS structural classes have been applied and
that all JS-critical IDs and tab panel IDs are preserved.
"""
from bs4 import BeautifulSoup


def test_history_page_loads(app_client):
    client, _ = app_client
    resp = client.get("/history")
    assert resp.status_code == 200


def test_tab_bar_has_six_tabs(app_client):
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    tabs = soup.find_all(class_="tab-btn")
    assert len(tabs) == 6, f"Expected 6 tab buttons, got {len(tabs)}"


def test_tab_panels_all_present(app_client):
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    panel_ids = ["tab-climate", "tab-air-quality", "tab-particulate",
                 "tab-environment", "tab-correlation", "tab-detections"]
    for pid in panel_ids:
        assert soup.find(id=pid) is not None, f"#{pid} panel missing"


def test_plotly_div_ids_preserved(app_client):
    """Plotly renders into these divs — IDs must never change."""
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    plot_ids = [
        "tempPlot", "humPlot",
        "eco2Plot", "tvocPlot", "gasTimeSeriesPlot",
        "pmTimeSeriesPlot",
        "tempOverlayPlot", "humOverlayPlot", "absHumPlot",
        "dewPointPlot", "fanStatePlot", "vpdPlot",
        "corrBrushPlot",
        "tvocEco2ScatterPlot", "tempHumScatterPlot",
        "pm25TvocScatterPlot", "pm25Eco2ScatterPlot",
        "diHeatmap", "diBandsChart",
    ]
    for pid in plot_ids:
        assert soup.find(id=pid) is not None, f"Plotly div #{pid} missing"


def test_range_tag_ml_elements_preserved(app_client):
    """These IDs drive the ML tagging workflow — must be preserved."""
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    ml_ids = [
        "corrRangeTagSelect",
        "corrCreateRangeInferenceBtn",
        "corrRangeInferenceStatus",
        "corrRangeTagSection",
        "corrAnalysisPanel",
        "corrInferenceGrid",
        "corrBrushPlot",
        "corrResetBtn",
        "corrRangeLabel",
        "corrShowDetections",
    ]
    for el_id in ml_ids:
        assert soup.find(id=el_id) is not None, f"ML element #{el_id} missing"


def test_inference_dialog_in_history(app_client):
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    dlg = soup.find("dialog", {"id": "inferenceDialog"})
    assert dlg is not None, "inferenceDialog missing from history page"
    # Tags section (added in event-tagging feature)
    assert soup.find(id="infTagsList") is not None, "#infTagsList missing"
    assert soup.find(id="infTagSelect") is not None, "#infTagSelect missing"
    assert soup.find(id="infAddTag") is not None, "#infAddTag missing"


def test_detection_tab_di_ids_preserved(app_client):
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    di_ids = [
        "diPeriodSummary", "diTrendIndicators", "diAttributionBreakdown",
        "diDonutChart", "diFingerprints", "diFingerprintCards",
        "diHeatmapSection", "diPatternSentence",
        "diBandsSection", "diToggles",
        "diInferenceFeed", "diInferenceFilters",
    ]
    for el_id in di_ids:
        assert soup.find(id=el_id) is not None, f"DI element #{el_id} missing"


def test_tab_buttons_have_astro_pill_class(app_client):
    """Fails until Task 6 adds the astro-tab class."""
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    tabs = soup.find_all(class_="tab-btn")
    for tab in tabs:
        assert "astro-tab" in tab.get("class", []), (
            f"tab-btn missing 'astro-tab' class: {tab.get('class')}"
        )


def test_channel_chips_present(app_client):
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    chips = soup.find_all(class_="channel-chip")
    assert len(chips) >= 8, f"Expected at least 8 channel chips, got {len(chips)}"


def test_range_select_in_topbar(app_client):
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    assert soup.find(id="range") is not None, "#range select missing"
```

- [ ] **Step 2: Run tests to confirm expected failures**

```bash
pytest tests/test_ui_history.py -v 2>&1 | tail -30
```

Expected: `test_tab_buttons_have_astro_pill_class` FAILS. All ID-preservation tests PASS.

---

### Task 5: Rewrite `static/css/history.css` with AstroUXDS tokens

**Files:**
- Modify: `static/css/history.css`

- [ ] **Step 1: Replace the full contents of `static/css/history.css`**

```css
/* ══════════════════════════════════════════════════════════════════════════
   history.css  —  AstroUXDS-styled history & analysis page
   Depends on base.css for --color-* tokens.
   ══════════════════════════════════════════════════════════════════════════ */

/* ══ Tab pill bar ═══════════════════════════════════════════════════════ */
.tab-bar {
  display: flex;
  gap: 0;
  border-bottom: 2px solid var(--color-border-interactive-default, #2b659b);
  margin-bottom: 1em;
  flex-wrap: wrap;
}
.tab-btn {
  background: none;
  border: none;
  border-bottom: 3px solid transparent;
  color: var(--color-text-secondary, #85a5c1);
  padding: 0.55em 1.1em;
  font-size: 0.88em;
  font-family: inherit;
  cursor: pointer;
  margin-bottom: -2px;
  transition: color 0.15s, border-color 0.15s, background 0.15s;
  border-radius: 3px 3px 0 0;
  white-space: nowrap;
  letter-spacing: 0.03em;
}
.tab-btn:hover {
  color: var(--color-text-primary, #ffffff);
  background: var(--color-background-surface-hover, #223f5a);
}
.tab-btn.tab-active {
  color: var(--color-interactive-default, #4dacff);
  border-bottom-color: var(--color-interactive-default, #4dacff);
  font-weight: 600;
}
.tab-hidden { display: none !important; }

.patterns-note { font-size: 0.82em; color: var(--color-text-secondary, #85a5c1); font-style: italic; margin: 0 0 0.75em; }

/* ══ Chart wrapper & info button ════════════════════════════════════════ */
.chart-wrapper { position: relative; }
.chart-header {
  display: flex;
  align-items: center;
  gap: 0.5em;
  margin-bottom: 0.25em;
}
.chart-header h4 { margin: 0; font-size: 0.85em; color: var(--color-text-secondary, #85a5c1); font-weight: 400; }
.chart-info-btn {
  background: none;
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  color: var(--color-text-secondary, #85a5c1);
  width: 20px; height: 20px;
  border-radius: 50%;
  font-size: 0.7em;
  font-weight: bold;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-family: inherit;
  transition: background 0.15s, color 0.15s;
}
.chart-info-btn:hover {
  background: var(--color-background-surface-hover, #223f5a);
  color: var(--color-text-primary, #ffffff);
}
.chart-info-popup {
  position: absolute;
  top: 100%;
  left: 0;
  z-index: 100;
  background: var(--color-background-surface-default, #1b2d3e);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  padding: 0.8em 1em;
  max-width: 340px;
  font-size: 0.82em;
  color: var(--color-text-primary, #ffffff);
  line-height: 1.5;
  box-shadow: 0 4px 16px rgba(0,0,0,0.5);
  display: none;
}
.chart-info-popup.visible { display: block; }

/* ══ Air Quality section headers ════════════════════════════════════════ */
.aq-section-title {
  font-size: 0.78em;
  color: var(--color-text-secondary, #85a5c1);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin: 1.25em 0 0.6em;
  padding: 0.4em 0.75em;
  border-left: 3px solid var(--color-interactive-default, #4dacff);
  background: var(--color-background-surface-default, #1b2d3e);
  border-radius: 0 3px 3px 0;
}

/* ══ PM / Gas summary cards ═════════════════════════════════════════════ */
.pm-summary {
  display: flex;
  gap: 0.75em;
  flex-wrap: wrap;
  margin-bottom: 1em;
}
.pm-summary-card {
  background: var(--color-background-surface-default, #1b2d3e);
  border: 1px solid var(--color-border-interactive-muted, #182f45);
  border-radius: 3px;
  padding: 0.7em 1.1em;
  min-width: 100px;
  text-align: center;
}
.pm-summary-card .label  { font-size: 0.7em; color: var(--color-text-secondary, #85a5c1); text-transform: uppercase; letter-spacing: 0.06em; }
.pm-summary-card .value  { font-size: 1.5em; font-weight: 600; color: var(--color-text-primary, #ffffff); }
.pm-summary-card .sub    { font-size: 0.72em; color: var(--color-text-secondary, #85a5c1); }

/* ══ PM WHO key ══════════════════════════════════════════════════════════ */
.pm-who-key {
  background: var(--color-background-surface-default, #1b2d3e);
  border: 1px solid var(--color-border-interactive-muted, #182f45);
  border-radius: 3px;
  padding: 0.5em 1em;
  display: flex;
  align-items: center;
  gap: 1.5em;
  flex-wrap: wrap;
  margin-bottom: 1em;
  font-size: 0.8em;
  color: var(--color-text-secondary, #85a5c1);
}
.pm-key-title { font-size: 0.9em; white-space: nowrap; }
.pm-levels { display: flex; gap: 1em; flex-wrap: wrap; }
.pm-level  { display: flex; align-items: center; gap: 0.35em; }

/* color-dot reused from dashboard.css via shared base */

/* ══ PM / Gas data table ════════════════════════════════════════════════ */
.pm-table-wrap { overflow-x: auto; margin-bottom: 1em; }
.pm-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85em;
}
.pm-table th {
  background: var(--color-background-surface-default, #1b2d3e);
  color: var(--color-text-secondary, #85a5c1);
  text-align: left;
  padding: 0.4em 0.7em;
  font-size: 0.78em;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  border-bottom: 2px solid var(--color-border-interactive-default, #2b659b);
}
.pm-table td {
  padding: 0.38em 0.7em;
  border-bottom: 1px solid var(--color-border-interactive-muted, #182f45);
  color: var(--color-text-primary, #ffffff);
}
.pm-table tr:hover td { background: var(--color-background-surface-hover, #223f5a); }
.pm-empty { text-align: center; color: var(--color-text-secondary, #85a5c1); padding: 1em; }

/* ══ Correlation: channel toggle chips ══════════════════════════════════ */
.channel-toggles {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5em;
  align-items: flex-start;
  margin-bottom: 0.75em;
}
.toggle-group {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35em;
  padding: 0.3em 0.6em;
  border: 1px solid var(--color-border-interactive-muted, #182f45);
  border-radius: 3px;
  background: var(--color-background-surface-default, #1b2d3e);
}
.toggle-group-label {
  background: none;
  border: none;
  color: var(--color-text-secondary, #85a5c1);
  font-size: 0.72em;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  cursor: pointer;
  font-family: inherit;
  padding: 0.1em 0.3em;
  transition: color 0.15s;
}
.toggle-group-label:hover { color: var(--color-text-primary, #ffffff); }
.channel-chip {
  background: var(--color-background-base-default, #101923);
  border: 1px solid var(--color-border-interactive-muted, #182f45);
  color: var(--color-text-secondary, #85a5c1);
  border-radius: 2px;
  padding: 0.25em 0.6em;
  font-size: 0.78em;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 0.35em;
  font-family: inherit;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}
.channel-chip.active {
  background: var(--color-background-surface-default, #1b2d3e);
  border-color: var(--color-interactive-default, #4dacff);
  color: var(--color-text-primary, #ffffff);
}
.channel-chip:hover { border-color: var(--color-interactive-default, #4dacff); }
.chip-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.chip-info { color: var(--color-interactive-default, #4dacff); font-size: 0.9em; cursor: help; opacity: 0.7; }
.chip-info:hover { opacity: 1; }
.toggle-presets { display: flex; gap: 0.35em; align-items: center; }

/* ══ Correlation brush section ══════════════════════════════════════════ */
.corr-brush-section {
  background: var(--color-background-surface-default, #1b2d3e);
  border: 1px solid var(--color-border-interactive-muted, #182f45);
  border-radius: 3px;
  padding: 0.9em 1em;
  margin-bottom: 1em;
}
.corr-brush-header h4 {
  margin: 0 0 0.25em;
  font-size: 0.9em;
  color: var(--color-text-primary, #ffffff);
}
.corr-brush-hint {
  font-size: 0.8em;
  color: var(--color-text-secondary, #85a5c1);
  margin: 0 0 0.75em;
}
.corr-brush-controls {
  display: flex;
  align-items: center;
  gap: 1em;
  flex-wrap: wrap;
  margin-top: 0.6em;
  font-size: 0.85em;
}
.corr-range-label { color: var(--color-text-secondary, #85a5c1); }
.overlay-toggle   { display: flex; align-items: center; gap: 0.4em; color: var(--color-text-secondary, #85a5c1); cursor: pointer; }
.overlay-toggle input { accent-color: var(--color-interactive-default, #4dacff); }

/* ══ Correlation inference panel ════════════════════════════════════════ */
.corr-inference-panel {
  background: var(--color-background-surface-default, #1b2d3e);
  border: 1px solid var(--color-border-interactive-muted, #182f45);
  border-radius: 3px;
  padding: 0.9em 1em;
  margin-bottom: 1em;
}
.corr-inference-panel h4 { margin: 0 0 0.6em; font-size: 0.9em; color: var(--color-text-primary, #ffffff); }
.corr-inference-grid { display: flex; flex-direction: column; gap: 0.4em; }
.corr-inference-placeholder { font-size: 0.85em; color: var(--color-text-secondary, #85a5c1); }

/* ══ ML analysis panel ══════════════════════════════════════════════════ */
.analysis-panel {
  background: var(--color-background-surface-default, #1b2d3e);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  padding: 1em;
  margin-bottom: 1em;
}
.analysis-loading { font-size: 0.88em; color: var(--color-text-secondary, #85a5c1); padding: 0.5em 0; }
.analysis-section { margin-bottom: 1em; }
.analysis-section h4 {
  font-size: 0.82em;
  color: var(--color-text-secondary, #85a5c1);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin: 0 0 0.4em;
  display: flex;
  align-items: center;
  gap: 0.4em;
}
.analysis-section p,
.analysis-section div { font-size: 0.88em; color: var(--color-text-primary, #ffffff); }

/* Range tagging row */
.corr-range-actions {
  background: var(--color-background-base-default, #101923);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  padding: 0.7em 0.9em;
  display: flex;
  align-items: center;
  gap: 0.75em;
  flex-wrap: wrap;
}
.corr-range-actions label {
  font-size: 0.82em;
  color: var(--color-text-secondary, #85a5c1);
  white-space: nowrap;
}
.corr-range-actions select {
  background: var(--color-background-surface-default, #1b2d3e);
  color: var(--color-text-primary, #ffffff);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  padding: 0.3em 0.5em;
  font-size: 0.85em;
  font-family: inherit;
}
.corr-range-status { font-size: 0.82em; color: var(--color-status-normal, #56f000); }

/* ══ Detections & Insights (DI) tab ═════════════════════════════════════ */
.di-card {
  background: var(--color-background-surface-default, #1b2d3e);
  border: 1px solid var(--color-border-interactive-muted, #182f45);
  border-radius: 3px;
  padding: 1em 1.2em;
  margin-bottom: 1em;
}
.di-card h3 {
  margin: 0 0 0.75em;
  font-size: 0.9em;
  color: var(--color-text-primary, #ffffff);
  display: flex;
  align-items: center;
  gap: 0.4em;
}
.di-loading { font-size: 0.88em; color: var(--color-text-secondary, #85a5c1); }
.di-subtitle { font-size: 0.82em; color: var(--color-text-secondary, #85a5c1); margin-bottom: 0.5em; }
.di-stat { font-size: 0.88em; color: var(--color-text-secondary, #85a5c1); margin-bottom: 0.75em; }
.di-sentence { font-size: 0.85em; color: var(--color-text-secondary, #85a5c1); margin-top: 0.5em; font-style: italic; }
.trend-row { display: flex; gap: 0.5em; flex-wrap: wrap; margin-bottom: 0.75em; }
.badge-count { background: var(--color-interactive-default, #4dacff); color: #000; border-radius: 10px; padding: 1px 7px; font-size: 0.8em; font-weight: 600; }

/* ══ Shared small button variant ════════════════════════════════════════ */
.btn-sm {
  padding: 0.28em 0.7em;
  font-size: 0.8em;
  border-radius: 3px;
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  background: transparent;
  color: var(--color-text-primary, #ffffff);
  cursor: pointer;
  font-family: inherit;
  transition: background 0.15s, border-color 0.15s;
}
.btn-sm:hover { background: var(--color-background-surface-hover, #223f5a); border-color: var(--color-interactive-default, #4dacff); }
.btn-sm:disabled { opacity: 0.45; cursor: not-allowed; }
```

- [ ] **Step 2: Verify Flask loads cleanly**

```bash
python -c "from app import create_app; app = create_app(); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit the CSS rewrite**

```bash
git add static/css/history.css
git commit -m "style: rewrite history.css with AstroUXDS tokens"
```

---

### Task 6: Update `templates/history.html` — add AstroUXDS classes

**Files:**
- Modify: `templates/history.html`

Preserve every `id=`, `data-*`, `onclick=`, and `data-channel=` attribute.

- [ ] **Step 1: Add `astro-tab` class to all tab buttons**

In `templates/history.html`, change:

```html
<!-- BEFORE -->
<button class="tab-btn tab-active" data-tab="climate">🌡️ Climate</button>
<button class="tab-btn" data-tab="air-quality">🧪 Air Quality</button>
<button class="tab-btn" data-tab="particulate">🌫️ Particulate</button>
<button class="tab-btn" data-tab="environment">🌿 Environment</button>
<button class="tab-btn" data-tab="correlation">🔍 Correlation</button>
<button class="tab-btn" data-tab="detections">📅 Detections &amp; Insights</button>
```

to:

```html
<!-- AFTER -->
<button class="tab-btn astro-tab tab-active" data-tab="climate">🌡️ Climate</button>
<button class="tab-btn astro-tab" data-tab="air-quality">🧪 Air Quality</button>
<button class="tab-btn astro-tab" data-tab="particulate">🌫️ Particulate</button>
<button class="tab-btn astro-tab" data-tab="environment">🌿 Environment</button>
<button class="tab-btn astro-tab" data-tab="correlation">🔍 Correlation</button>
<button class="tab-btn astro-tab" data-tab="detections">📅 Detections &amp; Insights</button>
```

- [ ] **Step 2: Run history tests**

```bash
pytest tests/test_ui_history.py -v
```

Expected: all tests `PASSED`.

- [ ] **Step 3: Run full regression**

```bash
pytest --ignore=tests/test_pi_resilience.py -q 2>&1 | tail -20
```

Expected: no new failures.

- [ ] **Step 4: Commit**

```bash
git add templates/history.html
git commit -m "style: add AstroUXDS classes to history.html"
```

---

### Task 7: Full regression test run + Phase 2 commit

**Files:** none (test-only)

- [ ] **Step 1: Run the complete test suite**

```bash
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility/.claude/worktrees/gifted-bhabha
pytest --ignore=tests/test_pi_resilience.py -v 2>&1 | tee /tmp/phase2-test-results.txt | tail -40
```

Expected: all tests pass. If any fail, investigate before proceeding.

- [ ] **Step 2: Create phase summary commit**

```bash
git add tests/test_ui_dashboard.py tests/test_ui_history.py
git commit -m "test: add Phase 2 UI structure tests for dashboard and history"
```

---

## Self-Review

**Spec coverage check:**
- Task 1: failing dashboard tests — covered
- Task 2: rewrite dashboard.css — covered (full CSS with all tokens)
- Task 3: update dashboard.html — covered (astro-card on stat cards, insight cards, health card)
- Task 4: failing history tests — covered
- Task 5: rewrite history.css — covered (tabs, chart containers, correlation panel, range tag UI)
- Task 6: update history.html — covered (astro-tab class on all 6 tab buttons)
- Task 7: regression + commit — covered

**ID preservation check:** All IDs listed in `test_all_sensor_value_ids_preserved` and `test_plotly_div_ids_preserved` appear verbatim in the existing HTML and are never touched by the plan's HTML edits.

**No placeholders:** All CSS blocks contain actual rules. All test functions contain actual assertions.
