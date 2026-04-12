# UI Redesign Phase 3 — Controls, Admin & Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle `controls.css`, `admin.css`, and the Settings/Insights Engine pages (`admin.html`, `controls.html`, `insights_engine.html`, `ie_config.html`) to AstroUXDS design language, completing the full UI redesign.

**Architecture:** Same token-consumption pattern as Phase 2. `controls.css` and `admin.css` are fully rewritten. HTML files receive AstroUXDS structural classes while all JS-bound IDs and form element IDs are preserved verbatim. Inline `<style>` blocks inside templates are migrated into their respective CSS files where practical, or updated to use tokens where they must stay inline.

**Tech Stack:** Python/Flask (Jinja2 templates), pytest, BeautifulSoup4, plain CSS.

---

## Files Touched

| Action   | Path |
|----------|------|
| Create   | `tests/test_ui_controls.py` |
| Modify   | `static/css/controls.css` |
| Modify   | `templates/controls.html` |
| Create   | `tests/test_ui_admin.py` |
| Modify   | `static/css/admin.css` |
| Modify   | `templates/admin.html` |
| Modify   | `templates/insights_engine.html` |
| Modify   | `templates/ie_config.html` |

---

### Task 1: Write failing tests for controls and admin pages

**Files:**
- Create: `tests/test_ui_controls.py`
- Create: `tests/test_ui_admin.py`

- [ ] **Step 1: Write controls page tests**

Create `/c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility/.claude/worktrees/gifted-bhabha/tests/test_ui_controls.py`:

```python
"""UI structure tests for the controls page."""
from bs4 import BeautifulSoup


def test_controls_page_loads(app_client):
    client, _ = app_client
    resp = client.get("/controls")
    assert resp.status_code == 200


def test_fan_device_card_present(app_client):
    client, _ = app_client
    resp = client.get("/controls")
    soup = BeautifulSoup(resp.data, "html.parser")
    assert soup.find(id="fanDeviceCard") is not None, "#fanDeviceCard missing"


def test_all_fan_js_ids_preserved(app_client):
    """JS reads/writes these IDs — must survive the HTML rewrite."""
    client, _ = app_client
    resp = client.get("/controls")
    soup = BeautifulSoup(resp.data, "html.parser")
    required_ids = [
        "fanDeviceCard", "fanStatusDot",
        "fan-status", "fan-mode", "fan-power", "fan-today", "fan-cost",
        "autoInfoPanel", "autoInfoSummary", "autoInfoRules", "fanInfoBtn",
    ]
    for el_id in required_ids:
        assert soup.find(id=el_id) is not None, f"#{el_id} missing from controls page"


def test_device_card_has_astro_class(app_client):
    """Fails until Task 3 adds astro-device-card class."""
    client, _ = app_client
    resp = client.get("/controls")
    soup = BeautifulSoup(resp.data, "html.parser")
    card = soup.find(id="fanDeviceCard")
    assert card is not None
    assert "astro-device-card" in card.get("class", []), (
        f"fanDeviceCard missing 'astro-device-card' class: {card.get('class')}"
    )


def test_fan_control_buttons_present(app_client):
    client, _ = app_client
    resp = client.get("/controls")
    soup = BeautifulSoup(resp.data, "html.parser")
    buttons = soup.find(class_="device-controls")
    assert buttons is not None, ".device-controls missing"
    labels = [b.get_text(strip=True) for b in buttons.find_all("button")]
    assert "On" in labels, "On button missing"
    assert "Off" in labels, "Off button missing"
    assert "Auto" in labels, "Auto button missing"
```

- [ ] **Step 2: Write admin page tests**

Create `/c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility/.claude/worktrees/gifted-bhabha/tests/test_ui_admin.py`:

```python
"""UI structure tests for the admin/settings page."""
from bs4 import BeautifulSoup


def test_admin_page_loads(app_client):
    client, _ = app_client
    resp = client.get("/admin")
    assert resp.status_code == 200


def test_admin_has_three_tabs(app_client):
    client, _ = app_client
    resp = client.get("/admin")
    soup = BeautifulSoup(resp.data, "html.parser")
    tabs = soup.find_all(class_="tab-btn")
    assert len(tabs) == 3, f"Expected 3 admin tabs, got {len(tabs)}"


def test_admin_tab_panels_present(app_client):
    client, _ = app_client
    resp = client.get("/admin")
    soup = BeautifulSoup(resp.data, "html.parser")
    for panel_id in ["tab-settings", "tab-users", "tab-insights-engine"]:
        assert soup.find(id=panel_id) is not None, f"#{panel_id} missing"


def test_fan_settings_form_ids_preserved(app_client):
    """JS reads/submits these IDs."""
    client, _ = app_client
    resp = client.get("/admin")
    soup = BeautifulSoup(resp.data, "html.parser")
    form_ids = [
        "fanForm", "enabled", "tempEnabled", "tempMin", "tempMax",
        "tvocEnabled", "tvocMin", "tvocMax",
        "humidityEnabled", "humidityMax",
        "pm25Enabled", "pm25Max", "pmStaleMinutes",
        "status",
    ]
    for el_id in form_ids:
        assert soup.find(id=el_id) is not None, f"#{el_id} missing from admin page"


def test_location_form_ids_preserved(app_client):
    client, _ = app_client
    resp = client.get("/admin")
    soup = BeautifulSoup(resp.data, "html.parser")
    for el_id in ["locationSearch", "searchResults", "selectedName",
                  "locLat", "locLon", "locStatus"]:
        assert soup.find(id=el_id) is not None, f"#{el_id} missing"


def test_energy_rate_ids_preserved(app_client):
    client, _ = app_client
    resp = client.get("/admin")
    soup = BeautifulSoup(resp.data, "html.parser")
    for el_id in ["unitRate", "energyStatus"]:
        assert soup.find(id=el_id) is not None, f"#{el_id} missing"


def test_threshold_grid_present(app_client):
    client, _ = app_client
    resp = client.get("/admin")
    soup = BeautifulSoup(resp.data, "html.parser")
    assert soup.find(id="thresholdGrid") is not None, "#thresholdGrid missing"
    assert soup.find(id="thresholdStatus") is not None, "#thresholdStatus missing"


def test_users_tab_ids_preserved(app_client):
    client, _ = app_client
    resp = client.get("/admin")
    soup = BeautifulSoup(resp.data, "html.parser")
    for el_id in ["newGithubUser", "newUserRole", "userStatus", "userList"]:
        assert soup.find(id=el_id) is not None, f"#{el_id} missing from users tab"


def test_delete_modal_present(app_client):
    client, _ = app_client
    resp = client.get("/admin")
    soup = BeautifulSoup(resp.data, "html.parser")
    assert soup.find(id="deleteModal") is not None, "#deleteModal missing"
    assert soup.find(id="modalConfirmBtn") is not None, "#modalConfirmBtn missing"


def test_admin_cards_have_astro_class(app_client):
    """Fails until Task 4 adds astro-card class to .card elements."""
    client, _ = app_client
    resp = client.get("/admin")
    soup = BeautifulSoup(resp.data, "html.parser")
    cards = soup.find_all(class_="card")
    assert len(cards) >= 4, f"Expected at least 4 .card elements, got {len(cards)}"
    for card in cards:
        assert "astro-card" in card.get("class", []), (
            f".card missing 'astro-card' class: {card.get('class')}"
        )


def test_toggle_rows_have_astro_toggle_class(app_client):
    """Fails until Task 4 adds astro-toggle class to .toggle-row elements."""
    client, _ = app_client
    resp = client.get("/admin")
    soup = BeautifulSoup(resp.data, "html.parser")
    rows = soup.find_all(class_="toggle-row")
    assert len(rows) >= 1, "No .toggle-row elements found"
    for row in rows:
        assert "astro-toggle" in row.get("class", []), (
            f".toggle-row missing 'astro-toggle' class"
        )


def test_insights_engine_page_loads(app_client):
    client, _ = app_client
    resp = client.get("/settings/insights-engine")
    assert resp.status_code == 200


def test_insights_engine_key_ids(app_client):
    client, _ = app_client
    resp = client.get("/settings/insights-engine")
    soup = BeautifulSoup(resp.data, "html.parser")
    assert soup.find(id="toggle-mode") is not None, "#toggle-mode missing"
    assert soup.find(id="mode-label") is not None, "#mode-label missing"
```

- [ ] **Step 3: Run tests to confirm expected failures**

```bash
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility/.claude/worktrees/gifted-bhabha
pytest tests/test_ui_controls.py tests/test_ui_admin.py -v 2>&1 | tail -30
```

Expected: `test_device_card_has_astro_class`, `test_admin_cards_have_astro_class`, and `test_toggle_rows_have_astro_toggle_class` FAIL. All ID-preservation tests PASS.

---

### Task 2: Rewrite `static/css/controls.css` with AstroUXDS tokens

**Files:**
- Modify: `static/css/controls.css`

- [ ] **Step 1: Replace the full contents of `static/css/controls.css`**

```css
/* ══════════════════════════════════════════════════════════════════════════
   controls.css  —  AstroUXDS-styled device controls page
   Depends on base.css for all --color-* tokens.
   ══════════════════════════════════════════════════════════════════════════ */

/* ══ Devices grid ═══════════════════════════════════════════════════════ */
.devices-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 1em;
}

/* ══ AstroUXDS device card ══════════════════════════════════════════════ */
.device-card,
.astro-device-card {
  background: var(--color-background-surface-default, #1b2d3e);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-left: 4px solid var(--color-interactive-default, #4dacff);
  border-radius: 3px;
  padding: 1.2em 1.4em;
}

.device-header {
  display: flex;
  align-items: center;
  gap: 0.75em;
  margin-bottom: 1em;
}
.device-icon              { font-size: 1.8em; line-height: 1; }
.device-title h3          { margin: 0; font-size: 1em; color: var(--color-text-primary, #ffffff); }
.device-subtitle          { font-size: 0.75em; color: var(--color-text-secondary, #85a5c1); }

/* ── AstroUXDS status indicator dot ── */
.status-dot {
  width: 12px; height: 12px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-left: auto;
  background: var(--color-status-off, #a4abb6);
  transition: background 0.3s, box-shadow 0.3s;
}
.status-dot.dot-on {
  background: var(--color-status-normal, #56f000);
  box-shadow: 0 0 6px var(--color-status-normal, #56f000);
}
.status-dot.dot-off { background: var(--color-status-off, #a4abb6); }

/* ── Device control buttons ── */
.device-controls {
  display: flex;
  gap: 0.5em;
  margin-bottom: 1em;
}
.device-controls button {
  flex: 1;
  padding: 0.45em 0.5em;
  border-radius: 3px;
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  cursor: pointer;
  font-size: 0.88em;
  font-family: inherit;
  font-weight: 600;
  transition: opacity 0.15s;
  letter-spacing: 0.03em;
}
.device-controls .btn-on {
  background: var(--color-status-normal, #56f000);
  color: #000;
  border-color: var(--color-status-normal, #56f000);
}
.device-controls .btn-off {
  background: var(--color-status-critical, #ff3838);
  color: #fff;
  border-color: var(--color-status-critical, #ff3838);
}
.device-controls .btn-auto {
  background: var(--color-interactive-default, #4dacff);
  color: #000;
  border-color: var(--color-interactive-default, #4dacff);
}
.device-controls button:hover { opacity: 0.82; }
.device-controls button:active { opacity: 0.65; }

/* Info button (circle 'i') */
.device-controls .btn-info {
  flex: 0 0 34px;
  width: 34px; height: 34px;
  border-radius: 50%;
  background: transparent;
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  color: var(--color-text-secondary, #85a5c1);
  font-size: 0.85em;
  font-weight: bold;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
  font-family: inherit;
}
.device-controls .btn-info:hover,
.device-controls .btn-info.active {
  background: var(--color-background-surface-hover, #223f5a);
  border-color: var(--color-interactive-default, #4dacff);
  color: var(--color-text-primary, #ffffff);
}

/* ── Auto-info panel ── */
.auto-info-panel {
  background: var(--color-background-base-default, #101923);
  border: 1px solid var(--color-border-interactive-muted, #182f45);
  border-radius: 3px;
  padding: 0.75em 0.9em;
  margin-bottom: 0.9em;
  font-size: 0.85em;
}
.auto-info-panel.hidden { display: none; }
.auto-info-summary { margin: 0 0 0.5em; color: var(--color-text-secondary, #85a5c1); }
.auto-info-rules {
  margin: 0;
  padding-left: 1.2em;
  color: var(--color-text-primary, #ffffff);
  line-height: 1.7;
}
.auto-info-rules li { font-size: 0.92em; }

/* ── Device meta grid ── */
.device-meta {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.5em;
}
.meta-item {
  background: var(--color-background-base-default, #101923);
  border: 1px solid var(--color-border-interactive-muted, #182f45);
  border-radius: 3px;
  padding: 0.5em 0.7em;
}
.meta-label {
  display: block;
  font-size: 0.68em;
  color: var(--color-text-secondary, #85a5c1);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 0.15em;
}
.meta-item strong { font-size: 0.95em; color: var(--color-text-primary, #ffffff); }
```

- [ ] **Step 2: Verify app loads**

```bash
python -c "from app import create_app; app = create_app(); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add static/css/controls.css
git commit -m "style: rewrite controls.css with AstroUXDS tokens"
```

---

### Task 3: Update `templates/controls.html` — add AstroUXDS classes

**Files:**
- Modify: `templates/controls.html`

- [ ] **Step 1: Add `astro-device-card` to the fan device card**

In `templates/controls.html`, change:

```html
<!-- BEFORE -->
<div class="device-card" id="fanDeviceCard">
```

to:

```html
<!-- AFTER -->
<div class="device-card astro-device-card" id="fanDeviceCard">
```

- [ ] **Step 2: Run controls tests**

```bash
pytest tests/test_ui_controls.py -v
```

Expected: all tests `PASSED`.

- [ ] **Step 3: Run full regression**

```bash
pytest --ignore=tests/test_pi_resilience.py -q 2>&1 | tail -20
```

Expected: no new failures.

- [ ] **Step 4: Commit**

```bash
git add templates/controls.html
git commit -m "style: add AstroUXDS classes to controls.html"
```

---

### Task 4: Rewrite `static/css/admin.css` with AstroUXDS tokens

**Files:**
- Modify: `static/css/admin.css`

- [ ] **Step 1: Replace the full contents of `static/css/admin.css`**

```css
/* ══════════════════════════════════════════════════════════════════════════
   admin.css  —  AstroUXDS-styled admin / settings pages
   Covers: admin.html, insights_engine.html, ie_config.html
   Depends on base.css for all --color-* tokens.
   ══════════════════════════════════════════════════════════════════════════ */

/* ══ Settings / admin page layout ══════════════════════════════════════ */
.settings-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
  gap: 1.2em;
}
@media (max-width: 480px) { .settings-grid { grid-template-columns: 1fr; } }

/* ══ AstroUXDS card ═════════════════════════════════════════════════════ */
.card,
.astro-card {
  background: var(--color-background-surface-default, #1b2d3e);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  padding: 1.5em;
}
.card h3 {
  margin: 0 0 0.75em;
  font-size: 0.95em;
  color: var(--color-text-primary, #ffffff);
  display: flex;
  align-items: center;
  gap: 0.4em;
}
.card-desc {
  font-size: 0.85em;
  color: var(--color-text-secondary, #85a5c1);
  margin: 0 0 1em;
  line-height: 1.5;
}

/* ══ Form layout ════════════════════════════════════════════════════════ */
.field-group { margin-bottom: 1.2em; }
.field-group legend {
  font-size: 0.78em;
  color: var(--color-text-secondary, #85a5c1);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 0.5em;
  display: flex;
  align-items: center;
  gap: 0.5em;
}
.field-row { display: flex; gap: 1em; flex-wrap: wrap; }
.field { flex: 1; min-width: 100px; }
.field label {
  display: block;
  font-size: 0.82em;
  color: var(--color-text-secondary, #85a5c1);
  margin-bottom: 0.3em;
}
.field input[type="number"],
.field input[type="text"],
.field select {
  width: 100%;
  box-sizing: border-box;
  padding: 0.45em 0.6em;
  background: var(--color-background-base-default, #101923);
  color: var(--color-text-primary, #ffffff);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  font-size: 0.9em;
  font-family: inherit;
  transition: border-color 0.15s;
}
.field input:focus,
.field select:focus {
  outline: none;
  border-color: var(--color-interactive-default, #4dacff);
}

/* ══ AstroUXDS toggle row ═══════════════════════════════════════════════ */
.toggle-row,
.astro-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--color-background-base-default, #101923);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  padding: 0.6em 0.85em;
  margin-bottom: 1em;
}
.toggle-row span { font-size: 0.9em; color: var(--color-text-primary, #ffffff); }

/* AstroUXDS-style toggle switch */
.switch {
  position: relative;
  display: inline-block;
  width: 44px; height: 24px;
  flex-shrink: 0;
}
.switch input { display: none; }
.slider {
  position: absolute;
  inset: 0;
  background: var(--color-status-off, #a4abb6);
  border-radius: 24px;
  cursor: pointer;
  transition: background 0.2s;
}
.slider::before {
  content: "";
  position: absolute;
  width: 18px; height: 18px;
  left: 3px; top: 3px;
  background: #fff;
  border-radius: 50%;
  transition: transform 0.2s;
}
.switch input:checked + .slider { background: var(--color-status-normal, #56f000); }
.switch input:checked + .slider::before { transform: translateX(20px); }

/* Small toggle variant */
.switch-sm { width: 34px; height: 18px; }
.switch-sm .slider::before { width: 12px; height: 12px; left: 3px; top: 3px; }
.switch-sm input:checked + .slider::before { transform: translateX(16px); }

/* ══ Save / search buttons ══════════════════════════════════════════════ */
.btn-save,
.btn-search {
  background: var(--color-interactive-default, #4dacff);
  color: #000;
  border: none;
  border-radius: 3px;
  padding: 0.45em 1.1em;
  font-size: 0.88em;
  font-family: inherit;
  font-weight: 600;
  cursor: pointer;
  letter-spacing: 0.03em;
  transition: opacity 0.15s;
}
.btn-save:hover,
.btn-search:hover { opacity: 0.85; }
.btn-save:active,
.btn-search:active { opacity: 0.65; }

/* ══ Status / feedback messages ════════════════════════════════════════ */
#status, #energyStatus, #locStatus, #thresholdStatus, #userStatus,
.status-msg {
  font-size: 0.85em;
  min-height: 1.2em;
  color: var(--color-status-normal, #56f000);
  margin-top: 0.5em;
}
.status-err,
.status-msg.err { color: var(--color-status-critical, #ff3838) !important; }

/* ══ Location search ════════════════════════════════════════════════════ */
.search-row { display: flex; gap: 0.5em; }
.search-row input[type="text"] {
  flex: 1;
  padding: 0.45em 0.6em;
  background: var(--color-background-base-default, #101923);
  color: var(--color-text-primary, #ffffff);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  font-size: 0.9em;
  font-family: inherit;
}
.search-row input:focus { outline: none; border-color: var(--color-interactive-default, #4dacff); }
.search-results {
  list-style: none;
  padding: 0; margin: 0.4em 0 0;
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  background: var(--color-background-surface-default, #1b2d3e);
}
.search-results li {
  padding: 0.45em 0.75em;
  cursor: pointer;
  font-size: 0.88em;
  color: var(--color-text-primary, #ffffff);
  border-bottom: 1px solid var(--color-border-interactive-muted, #182f45);
  transition: background 0.12s;
}
.search-results li:last-child { border-bottom: none; }
.search-results li:hover { background: var(--color-background-surface-hover, #223f5a); }
.selected-name { font-size: 0.9em; color: var(--color-text-secondary, #85a5c1); margin: 0.25em 0 0.75em; }

/* ══ Threshold grid ═════════════════════════════════════════════════════ */
.threshold-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 0.75em;
  margin-bottom: 1em;
}
.threshold-field { display: flex; flex-direction: column; gap: 0.25em; }
.threshold-field label { font-size: 0.78em; color: var(--color-text-secondary, #85a5c1); }
.threshold-field input[type="number"] {
  background: var(--color-background-base-default, #101923);
  color: var(--color-text-primary, #ffffff);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  padding: 0.4em 0.5em;
  font-size: 0.9em;
  font-family: inherit;
  width: 100%;
  box-sizing: border-box;
}
.threshold-field input:focus { outline: none; border-color: var(--color-interactive-default, #4dacff); }

/* ══ Users card ══════════════════════════════════════════════════════════ */
.users-card { grid-column: 1 / -1; }
.add-user-form .field-btn { flex: 0 0 auto; align-self: flex-end; }
.user-list { margin-top: 1em; display: flex; flex-direction: column; gap: 0.4em; }
.user-row {
  display: flex;
  align-items: center;
  gap: 0.75em;
  background: var(--color-background-base-default, #101923);
  border: 1px solid var(--color-border-interactive-muted, #182f45);
  border-radius: 3px;
  padding: 0.5em 0.75em;
  font-size: 0.88em;
}
.user-row .username { flex: 1; color: var(--color-text-primary, #ffffff); font-family: monospace; }
.user-row .user-role { color: var(--color-text-secondary, #85a5c1); font-size: 0.85em; }
.user-status { font-size: 0.85em; color: var(--color-status-normal, #56f000); margin-top: 0.4em; min-height: 1.2em; }
.field-role select {
  background: var(--color-background-base-default, #101923);
  color: var(--color-text-primary, #ffffff);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  padding: 0.45em 0.5em;
  font-family: inherit;
  font-size: 0.9em;
}

/* ══ Delete modal ════════════════════════════════════════════════════════ */
.modal-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.7);
  z-index: 200;
  align-items: center;
  justify-content: center;
}
.modal-overlay.open { display: flex; }
.modal {
  background: var(--color-background-surface-default, #1b2d3e);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  padding: 1.5em;
  max-width: 400px;
  width: 90vw;
  box-shadow: 0 8px 32px rgba(0,0,0,0.6);
}
.modal h4 { margin: 0 0 0.75em; font-size: 1em; color: var(--color-text-primary, #ffffff); }
.modal-body { font-size: 0.9em; color: var(--color-text-secondary, #85a5c1); margin-bottom: 0.5em; }
.modal-warning { font-size: 0.82em; color: var(--color-status-caution, #fce83a); margin-bottom: 1em; }
.modal-actions { display: flex; gap: 0.5em; justify-content: flex-end; }
.btn-modal-cancel {
  background: transparent;
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  color: var(--color-text-secondary, #85a5c1);
  border-radius: 3px;
  padding: 0.4em 1em;
  font-size: 0.88em;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.15s;
}
.btn-modal-cancel:hover { background: var(--color-background-surface-hover, #223f5a); }
.btn-modal-confirm {
  background: var(--color-status-critical, #ff3838);
  border: none;
  color: #fff;
  border-radius: 3px;
  padding: 0.4em 1em;
  font-size: 0.88em;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: opacity 0.15s;
}
.btn-modal-confirm:hover { opacity: 0.85; }

/* ══ Insights engine: score bars ════════════════════════════════════════ */
.score-bar-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 120px;
}
.score-bar {
  height: 8px;
  border-radius: 3px;
  transition: width 0.4s ease, background-color 0.4s ease;
  min-width: 0;
}
.score-bar--green  { background: var(--color-status-normal, #56f000); }
.score-bar--amber  { background: var(--color-status-caution, #fce83a); }
.score-bar--red    { background: var(--color-status-critical, #ff3838); }
.score-bar--none   { background: var(--color-status-off, #a4abb6); width: 4px !important; }
.score-label       { font-size: 0.8rem; color: var(--color-text-secondary, #85a5c1); min-width: 36px; }
.status-elevated   { color: var(--color-status-critical, #ff3838); font-weight: 600; }

/* ══ ie_config.html: sticky section nav ════════════════════════════════ */
.config-nav {
  position: sticky;
  top: 0;
  z-index: 50;
  background: var(--color-background-base-default, #101923);
  border-bottom: 1px solid var(--color-border-interactive-default, #2b659b);
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.45rem 1rem;
  font-size: 0.85rem;
  flex-wrap: wrap;
}
.config-nav a {
  color: var(--color-text-secondary, #85a5c1);
  text-decoration: none;
  padding: 0.3em 0.65em;
  border-radius: 3px;
  transition: background 0.12s, color 0.12s;
  white-space: nowrap;
}
.config-nav a:hover { background: var(--color-background-surface-hover, #223f5a); color: var(--color-text-primary, #ffffff); }
.config-nav a.nav-active {
  background: var(--color-background-surface-hover, #223f5a);
  color: var(--color-interactive-default, #4dacff);
}
.config-nav .nav-back {
  border-right: 1px solid var(--color-border-interactive-default, #2b659b);
  margin-right: 0.35rem;
  padding-right: 0.85rem;
}
.config-nav .nav-sep { color: var(--color-border-interactive-default, #2b659b); padding: 0 0.1rem; }
.config-section { scroll-margin-top: 46px; }

/* ══ ie_config: shared table styles ════════════════════════════════════ */
.rules-table, .fp-table, .anomaly-table, .sources-table {
  width: 100%; border-collapse: collapse; font-size: 0.85rem;
}
.rules-table th, .rules-table td,
.fp-table th, .fp-table td,
.anomaly-table th, .anomaly-table td,
.sources-table th, .sources-table td {
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--color-border-interactive-muted, #182f45);
  vertical-align: top;
  color: var(--color-text-primary, #ffffff);
}
.rules-table thead th,
.fp-table thead th,
.anomaly-table thead th {
  text-align: left;
  border-bottom: 2px solid var(--color-border-interactive-default, #2b659b);
  color: var(--color-text-secondary, #85a5c1);
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.sources-table thead th {
  text-align: left;
  border-bottom: 2px solid var(--color-border-interactive-default, #2b659b);
  color: var(--color-text-secondary, #85a5c1);
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.sources-table th, .sources-table td { vertical-align: middle; }
.rule-expr { font-family: monospace; font-size: 0.8rem; color: var(--color-text-primary, #ffffff); }

/* ══ ie_config: badges ══════════════════════════════════════════════════ */
.badge-warning  { background: var(--color-status-serious, #ffb302); color: #000; border-radius: 2px; padding: 1px 6px; font-size: 0.75rem; font-weight: 600; }
.badge-critical { background: var(--color-status-critical, #ff3838); color: #fff; border-radius: 2px; padding: 1px 6px; font-size: 0.75rem; font-weight: 600; }
.badge-info     { background: var(--color-status-standby, #2dccff); color: #000; border-radius: 2px; padding: 1px 6px; font-size: 0.75rem; font-weight: 600; }
.badge-ready    { background: var(--color-status-normal, #56f000); color: #000; border-radius: 2px; padding: 1px 6px; font-size: 0.75rem; }
.badge-learning { background: var(--color-status-caution, #fce83a); color: #000; border-radius: 2px; padding: 1px 6px; font-size: 0.75rem; }
.badge-active   { background: var(--color-status-normal, #56f000); color: #000; border-radius: 2px; padding: 2px 7px; font-size: 0.75rem; }
.badge-disabled { background: var(--color-status-critical, #ff3838); color: #fff; border-radius: 2px; padding: 2px 7px; font-size: 0.75rem; }
.badge-error    { background: var(--color-status-serious, #ffb302); color: #000; border-radius: 2px; padding: 2px 7px; font-size: 0.75rem; }

/* ══ ie_config: inline edit inputs ══════════════════════════════════════ */
input.inline-edit,
select.inline-edit {
  background: var(--color-background-base-default, #101923);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  color: var(--color-text-primary, #ffffff);
  padding: 2px 5px;
  border-radius: 3px;
  font-family: inherit;
  font-size: 0.85rem;
  width: 100%;
  box-sizing: border-box;
}
input.inline-edit:focus,
select.inline-edit:focus { outline: none; border-color: var(--color-interactive-default, #4dacff); }
.disabled-row { opacity: 0.45; }

/* ══ ie_config: rules modal ══════════════════════════════════════════════ */
.modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 100; align-items: center; justify-content: center; }
.modal-overlay.open { display: flex; }
.modal-box {
  background: var(--color-background-surface-default, #1b2d3e);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  border-radius: 3px;
  padding: 1.5rem;
  width: 100%;
  max-width: 480px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.6);
}
.modal-box h4 { margin: 0 0 1rem; font-size: 1rem; color: var(--color-text-primary, #ffffff); }
.modal-field { margin-bottom: 0.75rem; }
.modal-field label { display: block; font-size: 0.8rem; color: var(--color-text-secondary, #85a5c1); margin-bottom: 0.25rem; }
.modal-field input,
.modal-field select {
  width: 100%; box-sizing: border-box;
  background: var(--color-background-base-default, #101923);
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  color: var(--color-text-primary, #ffffff);
  padding: 0.35rem 0.5rem;
  border-radius: 3px;
  font-size: 0.85rem;
  font-family: inherit;
}
.modal-field input:focus,
.modal-field select:focus { outline: none; border-color: var(--color-interactive-default, #4dacff); }
.modal-actions { display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 1rem; }
.modal-status { font-size: 0.82rem; min-height: 1.1em; margin-bottom: 0.5rem; color: var(--color-status-normal, #56f000); }
.modal-status.err { color: var(--color-status-critical, #ff3838); }

/* ══ ie_config: fingerprint sensor chips ════════════════════════════════ */
.sensor-chips span {
  display: inline-block;
  background: var(--color-background-base-default, #101923);
  border: 1px solid var(--color-border-interactive-muted, #182f45);
  border-radius: 2px;
  padding: 1px 5px;
  margin: 1px;
  font-size: 0.75rem;
  color: var(--color-text-primary, #ffffff);
}

/* ══ Shared utility ═════════════════════════════════════════════════════ */
.info-icon {
  cursor: help;
  color: var(--color-interactive-default, #4dacff);
  font-size: 0.85em;
  opacity: 0.8;
}
.info-icon:hover { opacity: 1; }

.btn,
.btn-secondary {
  background: transparent;
  border: 1px solid var(--color-border-interactive-default, #2b659b);
  color: var(--color-text-primary, #ffffff);
  border-radius: 3px;
  padding: 0.4em 0.9em;
  font-size: 0.88em;
  font-family: inherit;
  cursor: pointer;
  text-decoration: none;
  display: inline-block;
  transition: background 0.15s, border-color 0.15s;
}
.btn:hover,
.btn-secondary:hover {
  background: var(--color-background-surface-hover, #223f5a);
  border-color: var(--color-interactive-default, #4dacff);
}
.btn-warning {
  background: var(--color-status-caution, #fce83a);
  border-color: var(--color-status-caution, #fce83a);
  color: #000;
  font-weight: 600;
}
.btn-danger {
  background: var(--color-status-critical, #ff3838);
  border-color: var(--color-status-critical, #ff3838);
  color: #fff;
  font-weight: 600;
}
.btn-warning:hover,
.btn-danger:hover { opacity: 0.85; background: inherit; }

/* ══ Insights engine page: mode toggle banner ═══════════════════════════ */
#mode-label { font-weight: 600; color: var(--color-text-primary, #ffffff); }
```

- [ ] **Step 2: Verify app loads**

```bash
python -c "from app import create_app; app = create_app(); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add static/css/admin.css
git commit -m "style: rewrite admin.css with AstroUXDS tokens"
```

---

### Task 5: Update `templates/admin.html` — add AstroUXDS classes

**Files:**
- Modify: `templates/admin.html`

Preserve every `id=`, `data-tab=`, `onclick=`, and `data-*` attribute.

- [ ] **Step 1: Add `astro-tab` to admin tab buttons**

In `templates/admin.html`, change:

```html
<!-- BEFORE -->
<button class="tab-btn tab-active" data-tab="settings">🔧 Settings</button>
<button class="tab-btn" data-tab="users">👥 Users</button>
<button class="tab-btn" data-tab="insights-engine">🧠 Insights Engine</button>
```

to:

```html
<!-- AFTER -->
<button class="tab-btn astro-tab tab-active" data-tab="settings">🔧 Settings</button>
<button class="tab-btn astro-tab" data-tab="users">👥 Users</button>
<button class="tab-btn astro-tab" data-tab="insights-engine">🧠 Insights Engine</button>
```

- [ ] **Step 2: Add `astro-card` to every `.card` element inside `#tab-settings` and `#tab-users`**

There are 5 `.card` elements in the settings tab (fan settings, energy rate, location, inference thresholds, and one more) and 1 in the users tab. Apply this pattern to each:

```html
<!-- BEFORE -->
<div class="card">

<!-- AFTER -->
<div class="card astro-card">
```

Note: The users card already uses `class="card users-card"` — change to `class="card astro-card users-card"`.

- [ ] **Step 3: Add `astro-toggle` to every `.toggle-row` element**

There is one main toggle row (Enable auto fan control) and several smaller ones (tempEnabled, tvocEnabled, humidityEnabled, pm25Enabled). Change each:

```html
<!-- BEFORE -->
<div class="toggle-row">

<!-- AFTER -->
<div class="toggle-row astro-toggle">
```

- [ ] **Step 4: Update the inline `<style>` block in admin.html to use tokens**

The inline `<style>` block at the top of `admin.html` covers `insights-table` column widths and the score bar. Replace the hard-coded hex colours in that block:

```html
<!-- In the <style> block, replace: -->
.score-bar--green  { background: #22c55e; }
.score-bar--amber  { background: #f59e0b; }
.score-bar--red    { background: #ef4444; }
.score-bar--none   { background: #d1d5db; width: 4px !important; }
.score-label { font-size: 0.8rem; color: var(--text-muted, #6b7280); min-width: 32px; }
.status-elevated { color: #ef4444; font-weight: 600; }

<!-- With: -->
.score-bar--green  { background: var(--color-status-normal, #56f000); }
.score-bar--amber  { background: var(--color-status-caution, #fce83a); }
.score-bar--red    { background: var(--color-status-critical, #ff3838); }
.score-bar--none   { background: var(--color-status-off, #a4abb6); width: 4px !important; }
.score-label { font-size: 0.8rem; color: var(--color-text-secondary, #85a5c1); min-width: 32px; }
.status-elevated { color: var(--color-status-critical, #ff3838); font-weight: 600; }
```

- [ ] **Step 5: Run admin tests**

```bash
pytest tests/test_ui_admin.py -v
```

Expected: all tests `PASSED`.

- [ ] **Step 6: Run full regression**

```bash
pytest --ignore=tests/test_pi_resilience.py -q 2>&1 | tail -20
```

Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add templates/admin.html
git commit -m "style: add AstroUXDS classes and token colours to admin.html"
```

---

### Task 6: Update `templates/insights_engine.html` and `templates/ie_config.html`

**Files:**
- Modify: `templates/insights_engine.html`
- Modify: `templates/ie_config.html`

These pages use heavy inline `<style>` blocks. The rules are already covered by the new `admin.css`, so the inline blocks are reduced to table column width rules only (which are page-specific and safe to keep inline).

- [ ] **Step 1: Update `insights_engine.html` inline style block**

In `templates/insights_engine.html`, the inline `<style>` block contains both column widths and colour rules. Replace the score bar and status colour rules with token references, keeping only layout rules that need to stay inline. Change the existing `<style>` block to:

```html
<style>
  /* ── Insights page: prevent table text overflow ── */
  .card { overflow: hidden; }
  .insights-table {
    width: 100%;
    font-size: .85rem;
    border-collapse: collapse;
    table-layout: fixed;
  }
  .insights-table th,
  .insights-table td {
    padding: .3rem .5rem;
    word-break: break-word;
    overflow-wrap: break-word;
  }
  .insights-table thead tr {
    text-align: left;
    border-bottom: 1px solid var(--color-border-interactive-default, #2b659b);
  }
  .insights-table thead th { padding: .35rem .5rem; }
  .insights-table tbody tr { border-bottom: 1px solid var(--color-border-interactive-muted, #182f45); }
  /* Rules table column widths */
  .rules-table col.col-id         { width: 38%; }
  .rules-table col.col-event      { width: 30%; }
  .rules-table col.col-severity   { width: 17%; }
  .rules-table col.col-confidence { width: 15%; }
  /* Source Fingerprints table column widths */
  .fp-table col.col-id      { width: 22%; }
  .fp-table col.col-label   { width: 20%; }
  .fp-table col.col-floor   { width: 12%; }
  .fp-table col.col-sensors { width: 46%; }
  /* Anomaly Models table column widths */
  .anomaly-table col.col-channel   { width: 25%; }
  .anomaly-table col.col-readings  { width: 15%; }
  .anomaly-table col.col-coldstart { width: 15%; }
  .anomaly-table col.col-score     { width: 25%; }
  .anomaly-table col.col-status    { width: 20%; }
</style>
```

- [ ] **Step 2: Update `ie_config.html` inline style block**

In `templates/ie_config.html`, the inline `<style>` block is large. Remove all colour/background rules (they are now in admin.css) and keep only column widths and layout rules that must stay close to the template. Replace the entire `<style>` block with:

```html
<style>
  /* ── Section anchors: offset for sticky bar ── */
  .config-section { scroll-margin-top: 46px; }
  /* ── Rules table column widths ── */
  .rules-table col.col-id       { width: 15%; }
  .rules-table col.col-enabled  { width: 8%; }
  .rules-table col.col-event    { width: 22%; }
  .rules-table col.col-expr     { width: 35%; }
  .rules-table col.col-sev      { width: 10%; }
  .rules-table col.col-conf     { width: 10%; }
  /* ── FP table column widths ── */
  .fp-table col.col-id      { width: 18%; }
  .fp-table col.col-label   { width: 16%; }
  .fp-table col.col-floor   { width: 10%; }
  .fp-table col.col-sensors { width: 42%; }
  .fp-table col.col-actions { width: 14%; }
  /* ── Anomaly table column widths ── */
  .anomaly-table col.col-channel  { width: 22%; }
  .anomaly-table col.col-readings { width: 12%; }
  .anomaly-table col.col-cold     { width: 12%; }
  .anomaly-table col.col-score    { width: 28%; }
  .anomaly-table col.col-status   { width: 14%; }
  .anomaly-table col.col-actions  { width: 12%; }
  /* ── Classifier table column widths ── */
  .classifier-table col.col-tag      { width: 28%; }
  .classifier-table col.col-samples  { width: 12%; }
  .classifier-table col.col-conf     { width: 30%; }
  .classifier-table col.col-clstatus { width: 30%; }
  /* ── Sources table column widths ── */
  .sources-table col.col-channel  { width: 30%; }
  .sources-table col.col-type     { width: 20%; }
  .sources-table col.col-count    { width: 15%; }
  .sources-table col.col-last     { width: 20%; }
  .sources-table col.col-actions  { width: 15%; }
</style>
```

- [ ] **Step 3: Run the admin tests (they cover insights engine page)**

```bash
pytest tests/test_ui_admin.py -v
```

Expected: all tests `PASSED`.

- [ ] **Step 4: Run full regression**

```bash
pytest --ignore=tests/test_pi_resilience.py -q 2>&1 | tail -20
```

Expected: no new failures.

- [ ] **Step 5: Commit**

```bash
git add templates/insights_engine.html templates/ie_config.html
git commit -m "style: update insights_engine and ie_config templates to use AstroUXDS tokens"
```

---

### Task 7: Final full regression test run + Phase 3 close-out commit

- [ ] **Step 1: Run the complete test suite**

```bash
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility/.claude/worktrees/gifted-bhabha
pytest --ignore=tests/test_pi_resilience.py -v 2>&1 | tee /tmp/phase3-test-results.txt | tail -50
```

Expected: all tests pass. If any fail, investigate before proceeding.

- [ ] **Step 2: Stage the new test files and commit**

```bash
git add tests/test_ui_controls.py tests/test_ui_admin.py
git commit -m "test: add Phase 3 UI structure tests for controls, admin, and settings pages"
```

- [ ] **Step 3: Verify the full Phase 3 commit log looks clean**

```bash
git log --oneline -10
```

Expected output (most recent first):
```
<hash> test: add Phase 3 UI structure tests for controls, admin, and settings pages
<hash> style: update insights_engine and ie_config templates to use AstroUXDS tokens
<hash> style: add AstroUXDS classes and token colours to admin.html
<hash> style: rewrite admin.css with AstroUXDS tokens
<hash> style: add AstroUXDS classes to controls.html
<hash> style: rewrite controls.css with AstroUXDS tokens
```

---

## Self-Review

**Spec coverage check:**
- Controls page CSS + HTML: Task 2 (CSS) + Task 3 (HTML with `astro-device-card`) — covered
- Admin page CSS + HTML: Task 4 (CSS) + Task 5 (HTML with `astro-card`, `astro-toggle`, token colours) — covered
- Settings/Insights Engine pages CSS + HTML: Task 4 (`admin.css` includes `config-nav`, table styles, badge styles, `modal-box`) + Task 6 (both template inline style blocks updated to tokens) — covered
- Left nav sidebar (`config-nav`) in `ie_config.html`: covered in `admin.css` Task 4 `config-nav` block
- Final regression test: Task 7 — covered

**Placeholder scan:** All CSS blocks contain actual rules. All test functions contain actual assertions with specific element IDs. No "implement X" without code.

**ID preservation:** `test_all_fan_js_ids_preserved` and `test_fan_settings_form_ids_preserved` enumerate all JS-bound IDs. None of the HTML edits in Tasks 3 or 5 modify `id=` attributes — only `class=` attributes are changed.

**Token consistency:** All CSS files use the same token names (`--color-background-surface-default`, `--color-interactive-default`, `--color-status-normal`, etc.) as defined in Phase 1's `base.css` rewrite. Fallback hex values match Phase 1's token values.
