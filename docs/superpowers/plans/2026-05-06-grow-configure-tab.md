# Plant Grow Unit — Configure Tab (Phase 2.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable per-unit user-driven configuration of plant profile, PID tunables, light schedule, soil calibration, and a safety override — all wired to existing override fields in `grow_units`.

**Architecture:** Five new PUT endpoints under `/api/grow/units/<id>/...` (each `require_role("controller", "admin")`), each backed by a pydantic request schema in `mlss_contracts.config_payloads`, persisting to existing override columns. Frontend renders five panels in the Configure subtab when active. WS push notifies the firmware of new config (re-uses existing `command` message type with `kind: "config_changed"`).

**Tech Stack:** Flask blueprints, pydantic v2 (in `mlss_contracts`), SQLite, vanilla ES modules + DOM, asyncio cross-thread `run_coroutine_threadsafe` for WS push.

---

## File Structure

**Create:**
- `contracts/src/mlss_contracts/config_payloads.py` — `ProfileUpdate`, `PIDUpdate`, `LightWindowsUpdate`, `CalibrationUpdate`, `SafetyOverrideRequest` pydantic models
- `mlss_monitor/routes/api_grow_config.py` — five PUT endpoints
- `static/js/grow/components/configure-panel.mjs` — orchestrator that renders the five sub-panels
- `static/js/grow/components/profile-editor.mjs`, `pid-editor.mjs`, `light-windows-editor.mjs`, `calibration-wizard.mjs`, `safety-override.mjs`
- `static/css/grow-configure.css` (or extend existing `grow.css`)
- `tests/contracts/test_config_payloads.py`
- `tests/grow_server/test_api_grow_config.py`
- `tests/grow_server/test_api_grow_config_authz.py`
- `tests/js/test_profile_editor.mjs`, `test_pid_editor.mjs`, `test_light_windows_editor.mjs`, `test_calibration_wizard.mjs`, `test_safety_override.mjs`

**Modify:**
- `static/js/grow/unit_detail.mjs` — flip `configure` subtab to `enabled: true`, mount panel on click
- `mlss_monitor/app.py` — register the new blueprint
- `mlss_monitor/routes/api_grow_units.py` — extend GET to include calibration + override fields in response
- `mlss_monitor/grow/ws_registry.py` (only if needed) — helper to push `config_changed` to a unit

---

## Task 1: Pydantic schemas in `mlss_contracts`

**Files:**
- Create: `contracts/src/mlss_contracts/config_payloads.py`
- Test: `tests/contracts/test_config_payloads.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/contracts/test_config_payloads.py
import pytest
from pydantic import ValidationError
from mlss_contracts.config_payloads import (
    ProfileUpdate, PIDUpdate, LightWindowsUpdate,
    CalibrationUpdate, SafetyOverrideRequest,
)


def test_profile_update_accepts_valid():
    p = ProfileUpdate(label="Tom 1", plant_type="tomato", medium_type="soil",
                      sown_at="2026-04-01T00:00:00Z", current_phase="vegetative")
    assert p.label == "Tom 1"


def test_profile_update_rejects_bad_phase():
    with pytest.raises(ValidationError):
        ProfileUpdate(current_phase="not_a_phase")


def test_profile_update_rejects_bad_medium():
    with pytest.raises(ValidationError):
        ProfileUpdate(medium_type="hydroponic_nft")  # not in enum


def test_pid_update_clamps_kp_to_nonneg():
    with pytest.raises(ValidationError):
        PIDUpdate(kp=-0.1)


def test_pid_update_min_pulse_must_be_le_max():
    with pytest.raises(ValidationError):
        PIDUpdate(min_pulse_s=10, max_pulse_s=5)


def test_light_windows_24h_format_required():
    LightWindowsUpdate(phase="vegetative", windows=[
        {"start": "06:00", "end": "22:00"}])
    with pytest.raises(ValidationError):
        LightWindowsUpdate(phase="vegetative", windows=[
            {"start": "6am", "end": "10pm"}])


def test_light_windows_end_after_start_or_wraps_midnight_explicitly():
    # 22:00 → 02:00 wraps midnight, allowed
    LightWindowsUpdate(phase="flowering", windows=[
        {"start": "22:00", "end": "02:00"}])
    # 06:00 → 06:00 (zero-length) rejected
    with pytest.raises(ValidationError):
        LightWindowsUpdate(phase="flowering", windows=[
            {"start": "06:00", "end": "06:00"}])


def test_calibration_dry_must_be_less_than_wet():
    CalibrationUpdate(dry_raw=300, wet_raw=1500)
    with pytest.raises(ValidationError):
        CalibrationUpdate(dry_raw=1500, wet_raw=300)


def test_safety_override_requires_three_confirms():
    # Server-side schema just records intent; the 3-click is UI-side
    s = SafetyOverrideRequest(action="force_pump_on", duration_s=10,
                              acknowledged_warnings=["pump_safety"])
    assert s.duration_s == 10


def test_safety_override_rejects_excessive_duration():
    with pytest.raises(ValidationError):
        SafetyOverrideRequest(action="force_pump_on", duration_s=600,
                              acknowledged_warnings=["pump_safety"])
```

- [ ] **Step 2: Run tests, confirm they fail with ImportError**

`cd contracts && py -m poetry run pytest ../tests/contracts/test_config_payloads.py -v`

- [ ] **Step 3: Implement schemas**

```python
# contracts/src/mlss_contracts/config_payloads.py
from typing import Literal, Optional
from datetime import datetime
from pydantic import BaseModel, Field, model_validator
import re

_PHASE = Literal["seedling", "vegetative", "flowering", "fruiting", "dormant"]
_MEDIUM = Literal["soil", "coco", "rockwool", "custom"]
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ProfileUpdate(BaseModel):
    label: Optional[str] = Field(None, max_length=64)
    description: Optional[str] = Field(None, max_length=500)
    plant_type: Optional[str] = Field(None, max_length=32)
    medium_type: Optional[_MEDIUM] = None
    sown_at: Optional[datetime] = None
    current_phase: Optional[_PHASE] = None


class PIDUpdate(BaseModel):
    target_pct: Optional[float] = Field(None, ge=0, le=100)
    deadband_pct: Optional[float] = Field(None, ge=0, le=20)
    kp: Optional[float] = Field(None, ge=0, le=10)
    ki: Optional[float] = Field(None, ge=0, le=10)
    kd: Optional[float] = Field(None, ge=0, le=10)
    soak_window_min: Optional[int] = Field(None, ge=0, le=240)
    min_pulse_s: Optional[float] = Field(None, ge=0, le=60)
    max_pulse_s: Optional[float] = Field(None, ge=0, le=60)

    @model_validator(mode="after")
    def _min_le_max(self):
        if (self.min_pulse_s is not None and self.max_pulse_s is not None
                and self.min_pulse_s > self.max_pulse_s):
            raise ValueError("min_pulse_s must be <= max_pulse_s")
        return self


class LightWindow(BaseModel):
    start: str
    end: str

    @model_validator(mode="after")
    def _check_format_and_nonzero(self):
        if not _HHMM_RE.match(self.start):
            raise ValueError(f"start must be HH:MM 24h: {self.start!r}")
        if not _HHMM_RE.match(self.end):
            raise ValueError(f"end must be HH:MM 24h: {self.end!r}")
        if self.start == self.end:
            raise ValueError("start and end must differ")
        return self


class LightWindowsUpdate(BaseModel):
    phase: _PHASE
    windows: list[LightWindow] = Field(default_factory=list, max_length=8)


class CalibrationUpdate(BaseModel):
    dry_raw: int = Field(..., ge=0, le=4095)
    wet_raw: int = Field(..., ge=0, le=4095)

    @model_validator(mode="after")
    def _dry_lt_wet(self):
        if self.dry_raw >= self.wet_raw:
            raise ValueError("dry_raw must be < wet_raw")
        return self


_SAFETY_ACTION = Literal[
    "force_pump_on", "force_pump_off",
    "force_light_on", "force_light_off",
    "skip_next_soak",
]


class SafetyOverrideRequest(BaseModel):
    action: _SAFETY_ACTION
    duration_s: float = Field(..., ge=0, le=300)
    acknowledged_warnings: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run tests, confirm pass**

`cd contracts && py -m poetry run pytest ../tests/contracts/test_config_payloads.py -v` → all pass

- [ ] **Step 5: Commit**

```
git add contracts/src/mlss_contracts/config_payloads.py tests/contracts/test_config_payloads.py
git commit -m "Add config_payloads schemas for grow Configure tab"
```

---

## Task 2: Profile + PID PUT endpoints (server side)

**Files:**
- Create: `mlss_monitor/routes/api_grow_config.py`
- Modify: `mlss_monitor/app.py` (register blueprint)
- Test: `tests/grow_server/test_api_grow_config.py` (CRUD), `test_api_grow_config_authz.py` (RBAC)

- [ ] **Step 1: Write failing tests for profile + PID**

Cover both unit-level (endpoint returns 200 / 400 / 404) AND stack-level (real app fixture with admin session, hits the route, asserts DB row updated). Include RBAC tests (viewer → 403, controller → 200, admin → 200).

Tests must include:
- `test_put_profile_updates_label_and_phase` — PUT with `{"label": "X", "current_phase": "flowering"}`, assert grow_units row updated
- `test_put_profile_rejects_bad_phase` — `{"current_phase": "bogus"}` → 400
- `test_put_profile_returns_404_for_unknown_unit`
- `test_put_pid_writes_override_columns` — PUT with `{"kp": 0.5, "soak_window_min": 60}` → asserts `watering_kp_override=0.5, soak_window_min_override=60`
- `test_put_pid_returns_400_when_min_gt_max`
- RBAC variants for both endpoints

- [ ] **Step 2: Implement profile + PID endpoints**

Both endpoints:
- `@require_role("controller", "admin")`
- Validate via pydantic
- `UPDATE grow_units SET ... WHERE id=?`
- After commit, push WS `command` with `{"kind": "config_changed", "section": "profile"|"pid"}` to the unit (best-effort; ignore if not connected)
- Return `{"ok": true}` 200

- [ ] **Step 3: Run tests, confirm pass**

- [ ] **Step 4: Commit**

```
git commit -m "Add PUT /api/grow/units/<id>/profile and /pid"
```

---

## Task 3: Light windows PUT endpoint

**Files:**
- Modify: `mlss_monitor/routes/api_grow_config.py`
- Test: `tests/grow_server/test_api_grow_config.py` (additions)

- [ ] **Step 1: Write failing tests**

- `test_put_light_windows_replaces_existing` — POST 2 windows for `vegetative`, then POST 1 window for `vegetative`, assert only 1 row remains for that (unit, phase)
- `test_put_light_windows_validates_hhmm`
- `test_put_light_windows_clears_when_empty_list` — empty list → all windows for that phase deleted (unit falls back to plant profile defaults)
- RBAC tests

- [ ] **Step 2: Implement**

Strategy: delete-then-insert in a transaction, scoped to the (unit_id, phase) pair. Don't touch other phases.

```python
@api_grow_config_bp.route("/api/grow/units/<int:unit_id>/light_windows",
                          methods=["PUT"])
@require_role("controller", "admin")
def put_light_windows(unit_id):
    body = request.get_json(silent=True) or {}
    try:
        payload = LightWindowsUpdate(**body)
    except ValidationError as e:
        return jsonify({"error": "invalid_payload", "detail": e.errors()}), 400
    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        if not conn.execute("SELECT 1 FROM grow_units WHERE id=?",
                            (unit_id,)).fetchone():
            return jsonify({"error": "unit_not_found"}), 404
        conn.execute("DELETE FROM grow_light_windows WHERE unit_id=? AND phase=?",
                     (unit_id, payload.phase))
        for i, w in enumerate(payload.windows):
            conn.execute(
                "INSERT INTO grow_light_windows "
                "(unit_id, phase, start_hh_mm, end_hh_mm, sort_order) "
                "VALUES (?, ?, ?, ?, ?)",
                (unit_id, payload.phase, w.start, w.end, i),
            )
        conn.commit()
    finally:
        conn.close()
    _push_config_changed(unit_id, "light_windows")
    return jsonify({"ok": True})
```

- [ ] **Step 3: Run tests, confirm pass**

- [ ] **Step 4: Commit**

---

## Task 4: Calibration + Safety Override endpoints

**Files:**
- Modify: `mlss_monitor/routes/api_grow_config.py`
- Test: `tests/grow_server/test_api_grow_config.py` (additions)

- [ ] **Step 1: Write failing tests**

- `test_put_calibration_writes_dry_and_wet_raw`
- `test_put_calibration_rejects_dry_ge_wet`
- `test_post_safety_override_pushes_command_via_ws` — mock the registry, assert the right `command` payload was sent (not just stored)
- `test_post_safety_override_records_in_grow_errors_as_info` — for audit trail
- RBAC tests (note: safety_override should require `admin`, not just `controller`)

- [ ] **Step 2: Implement calibration**

Updates `soil_dry_raw`, `soil_wet_raw` columns. Pushes `config_changed` with `section: "calibration"`.

- [ ] **Step 3: Implement safety_override**

- `@require_role("admin")` (stricter than the others)
- Pushes `command` with `{"kind": "safety_override", "action": ..., "duration_s": ...}` via `asyncio.run_coroutine_threadsafe(registry.send_to_unit(...), state.grow_ws_loop)`
- Records into `grow_errors` table as `severity='info', kind='safety_override_invoked'` for audit
- Returns 202 if pushed, 503 if unit not connected

- [ ] **Step 4: Run tests, confirm pass**

- [ ] **Step 5: Commit**

---

## Task 5: Extend GET /api/grow/units/<id> response

**Files:**
- Modify: `mlss_monitor/routes/api_grow_units.py`
- Test: `tests/grow_server/test_grow_units_api.py` (additions)

- [ ] **Step 1: Write failing tests**

- `test_get_unit_includes_overrides_block` — response now has `"overrides": {"watering_kp": null|float, "soak_window_min": null|int, ...}` so the frontend can show "default" vs "custom" indicators
- `test_get_unit_includes_calibration_block` — `"calibration": {"dry_raw": null|int, "wet_raw": null|int}`
- `test_get_unit_includes_light_windows_block` — `"light_windows": [{"phase": "vegetative", "start": "06:00", "end": "22:00"}, ...]`

- [ ] **Step 2: Implement**

Add the three new top-level keys to the response dict. Keep existing keys unchanged so the Live tab keeps working.

- [ ] **Step 3: Run tests + frontend regression check**

`cd .. && py -m poetry run pytest tests/grow_server/test_grow_units_api.py -v`
`node tests/js/test_unit_detail_skeleton.mjs` (or whatever the existing run command is — check `package.json`)

- [ ] **Step 4: Commit**

---

## Task 6: Frontend — Profile + PID editor panels

**Files:**
- Create: `static/js/grow/components/profile-editor.mjs`, `pid-editor.mjs`
- Create: `tests/js/test_profile_editor.mjs`, `test_pid_editor.mjs`
- Modify: `static/js/grow/unit_detail.mjs` (mount on tab activate)

- [ ] **Step 1: Write failing tests**

Use the same JSDOM-style pattern from existing `tests/js/test_*.mjs`. Cover:
- `test_profile_editor_renders_current_values` — given a unit, the form fields are populated
- `test_profile_editor_PUTs_on_save` — mock fetch, click Save, assert PUT body matches form state
- `test_profile_editor_disables_save_while_request_in_flight`
- `test_pid_editor_shows_default_vs_override_indicator` — fields with `null` override show "(default)" badge, fields with a value show "(custom)" + a "Reset to default" button
- `test_pid_editor_PUT_omits_unchanged_fields` (or uses partial-update semantics)

- [ ] **Step 2: Implement panels**

Both panels follow the same pattern: render form, wire submit handler, show toast/inline status on response.

- [ ] **Step 3: Implement subtab switcher in unit_detail.mjs**

```javascript
// Flip the disabled flag
{ id: "configure", label: "⚙ Configure", enabled: true },

// Add tab click handler
nav.addEventListener("click", (ev) => {
  const tab = ev.target.closest("[data-tab]");
  if (!tab || tab.disabled) return;
  switchSubtab(tab.dataset.tab, unit);
});

function switchSubtab(tabId, unit) {
  const body = document.getElementById("du-body");
  body.innerHTML = "";
  if (tabId === "live") {
    // re-render live panels
  } else if (tabId === "configure") {
    body.appendChild(renderConfigurePanel(unit));
  }
}
```

- [ ] **Step 4: Run JS tests, confirm pass**

- [ ] **Step 5: Commit**

---

## Task 7: Frontend — Light windows + Calibration + Safety panels

**Files:**
- Create: `static/js/grow/components/light-windows-editor.mjs`, `calibration-wizard.mjs`, `safety-override.mjs`
- Create: corresponding test files in `tests/js/`
- Modify: `static/js/grow/components/configure-panel.mjs` to mount all five sub-panels

- [ ] **Step 1: Write failing tests**

Per panel:
- `test_light_windows_editor_renders_per_phase_groups` — five phases (seedling/vegetative/flowering/fruiting/dormant) each get a sub-section
- `test_light_windows_editor_can_add_remove_window`
- `test_calibration_wizard_two_step_flow` — click "I'm dry now" captures current raw → click "I'm wet now" captures current raw → Save sends both
- `test_safety_override_requires_three_clicks_within_5s` — first click arms (button text "Confirm 1/3"), 3 clicks within 5s commits, after 5s without click 3 it resets

- [ ] **Step 2: Implement**

Per spec — focus on the friction UX for safety override. The "3 clicks in 5s" pattern is meant to make accidental triggers nearly impossible while still being fast for intentional use.

- [ ] **Step 3: Run all JS + frontend regression tests**

- [ ] **Step 4: Commit**

---

## Task 8: WS firmware-side handling of config_changed

**Files:**
- Modify: `grow_unit/src/mlss_grow/service.py` (or wherever WS commands are dispatched in firmware)
- Test: `tests/grow_unit/test_config_changed_handler.py`

- [ ] **Step 1: Write failing tests**

- `test_config_changed_command_re_fetches_unit_config` — when firmware receives `{"kind": "config_changed", "section": "pid"}`, it makes a GET back to the server to fetch the latest config and re-loads its in-memory PID config
- `test_safety_override_command_invokes_actuator_directly` — bypasses normal PID logic for the duration

- [ ] **Step 2: Implement**

The firmware needs a small new module `mlss_grow/config_sync.py` that:
- Has a `pull_unit_config(server_url, token, server_cert_path)` function (GET /api/grow/units/<id>/config — a new endpoint, ALSO add this in Task 5)
- Updates the in-memory PID config without restarting the service

- [ ] **Step 3: Run firmware test suite**

- [ ] **Step 4: Commit**

---

## Task 9: End-to-end stack test

**Files:**
- Create: `tests/grow_server/test_configure_e2e.py`

- [ ] **Step 1: Write the e2e test**

Boot the real Flask app (admin session), boot the WS listener, register a fake firmware client, invoke each Configure endpoint, and assert that:
1. The DB row updates
2. A `config_changed` (or `safety_override`) command is delivered to the registered client

This is the "business logic through the stack" test layer the user specifically asked for.

- [ ] **Step 2: Implement (only test code; no production code changes)**

- [ ] **Step 3: Run + commit**

---

## Self-review notes

- All 5 PUTs use `require_role("controller", "admin")` except `safety_override` which is `admin`-only.
- All payloads validated via shared pydantic models — frontend can rely on the same types via TypeScript-like JSDoc.
- WS push is best-effort: a disconnected unit just doesn't get the live notification, but the next time it reconnects it should pull fresh config (Task 8).
- No DB migration needed: every override field already exists in `grow_units` (verified in `database/grow_schema.py`).
- Test pattern: per-task unit tests + per-task RBAC tests + a final cross-task e2e (Task 9) — matches the user's explicit "go overboard with unit tests + business logic through the stack" preference.

---
