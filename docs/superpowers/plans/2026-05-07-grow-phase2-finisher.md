# Plant Grow Unit — Phase 2 Finisher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the remaining Phase 2 deliverables so the system is ready for first physical deployment with partial hardware (camera + soil sensor in place; pump/light may not yet be powered).

**Architecture:** Four independent feature batches. Sense-only mode is sequenced first because it gates the user's first deployment. Settings page exposes admin tooling. Lightbox + fleet filter are pure UX polish.

**Tech Stack:** Same as the rest of Phase 2 — Flask + SQLite + vanilla ES modules.

---

## Task 1: Sense-only mode (capability health field)

**The user's blocking concern:** First deployment will have camera + soil moisture sensor wired but pump + grow light not yet powered (waiting on second PSU). The UI must gracefully degrade — greyed-out actuator controls + "disconnected" indicator — without an explicit toggle.

### Design

Each capability gets a `health` field with 4 states:

| Health | Meaning | Trigger |
|---|---|---|
| `"connected"` | Working normally | Sensors: any non-null reading observed. Actuators: at least one successful actuation cycle. |
| `"untested"` | Declared but never observed | Capability declared in firmware boot but no observation yet. **First-boot default for actuators.** |
| `"unresponsive"` | Recently failed | Server sent a command, didn't see the expected telemetry within timeout. |
| `"no_hardware"` | Init explicitly failed | Firmware's HAT init raised an exception. |

Storage: existing `grow_unit_capabilities.details_json` — no schema migration. Field name `health` so it's easy to read in queries.

Detection lives in three layers:
1. **Firmware boot**: try to init each driver (Automation HAT, soil sensor, camera). On failure → emit capability with `health: "no_hardware"`. On success → `health: "untested"` for actuators (no actuation yet) and `"connected"` for sensors (boot reading is the first observation).
2. **Firmware telemetry**: every telemetry frame includes the actual observed actuator states. The server sees pump_state=1 → flips that unit's pump capability to `"connected"`. Same for light.
3. **Server-side timeout**: when the server sends `water_now`, it records `last_command_at`. If no `watering_event` for that unit lands within 30s, mark capability `"unresponsive"`.

UI (Quick Controls panel + Live Readings tiles):
- `health: "connected"` → normal styling
- `health: "untested"` → button greyed but clickable (lets user kick off the first test); pill: "⏱ Untested"; tooltip: "Click to test — connect 12V PSU to Automation HAT first"
- `health: "unresponsive"` → button greyed AND disabled; pill: "⚠ Unresponsive"; tooltip: "Last command didn't reach the unit. Check power + cabling."
- `health: "no_hardware"` → button hidden entirely (or greyed + disabled, your call); pill: "🔌 Not connected"

### Files

**Server:**
- `mlss_monitor/grow/handlers.py` — `handle_capabilities` writes the `health` field into `details_json` on registration; `handle_telemetry` updates the field for actuators when their state is non-zero
- `mlss_monitor/routes/api_grow_units.py` — GET response surfaces `capabilities[i].health` (parse from `details_json`)
- NEW: `mlss_monitor/grow/health_watchdog.py` — small helper called from `handle_telemetry` and `handle_event` that updates capability health based on observed state
- `tests/grow_server/test_capability_health.py` (new)
- `tests/grow_server/test_grow_units_api.py` — extend to assert `health` is in the GET response

**Firmware:**
- `grow_unit/src/mlss_grow/service.py` — at boot, try-catch around each driver init; emit capabilities with `health` accordingly
- `tests/grow_unit/test_service_capabilities.py` (new)

**Frontend:**
- `static/js/grow/components/quick-controls.mjs` (or wherever quick controls live in `unit_detail.mjs`) — read `unit.capabilities.find(c => c.channel === 'pump').health` and apply styling
- `static/js/grow/components/stat-tile.mjs` — extend with optional `health` prop for the muted-state rendering
- `tests/js/test_quick_controls.mjs` — extend with health-state assertions

**Contracts:**
- `contracts/src/mlss_contracts/capabilities.py` — add optional `health: Literal["connected", "untested", "unresponsive", "no_hardware"]` field with default `"untested"`
- `tests/contracts/test_capabilities.py` — extend

### Steps

- [ ] **Step 1: Write failing tests across all 4 packages**

Server tests:
- `test_handle_capabilities_writes_health_to_details_json` — emit a capabilities message with `health: "no_hardware"` on the pump channel; assert grow_unit_capabilities row's `details_json` contains `"health": "no_hardware"`
- `test_handle_telemetry_with_pump_state_1_promotes_pump_health_to_connected` — seed capability with health=untested, send telemetry with pump_state=1, assert health flipped to "connected"
- `test_handle_telemetry_with_pump_state_0_does_not_demote_connected_health` — once connected, stays connected (pump just being off doesn't mean disconnected)
- `test_get_unit_response_includes_health_per_capability` — GET response has `capabilities[i].health` field
- `test_water_now_command_with_no_event_within_timeout_marks_pump_unresponsive` — needs a clock-injection or a manual call to the watchdog; assert health flipped after timeout

Contracts tests:
- `test_capability_accepts_optional_health_field` — `Capability(channel="pump", ..., health="untested")` validates
- `test_capability_default_health_is_untested` — `Capability(channel="pump", ...)` (omitted) has `health == "untested"`
- `test_capability_rejects_invalid_health_value` — `health="bogus"` raises ValidationError

Firmware tests:
- `test_service_emits_capability_with_no_hardware_health_on_init_failure` — mock the Automation HAT driver to raise; service emits pump capability with health="no_hardware"
- `test_service_emits_actuator_capability_with_untested_health_on_clean_init` — mock driver init succeeds; emits health="untested"
- `test_service_emits_sensor_capability_with_connected_health_when_first_reading_succeeds`

JS tests:
- `test_quick_controls_greys_out_pump_button_when_health_is_no_hardware`
- `test_quick_controls_greys_out_light_button_when_health_is_unresponsive`
- `test_quick_controls_normal_styling_when_health_is_connected`
- `test_quick_controls_renders_disconnected_pill_when_health_not_connected`

- [ ] **Step 2: Implement health field across stack**

Contracts first (sets the schema):
```python
# contracts/src/mlss_contracts/capabilities.py
class Capability(BaseModel):
    channel: str
    hardware: Optional[str] = None
    is_required: bool = False
    unit_label: Optional[str] = None
    details: dict = Field(default_factory=dict)
    health: Literal["connected", "untested", "unresponsive", "no_hardware"] = "untested"
```

Firmware:
```python
# grow_unit/src/mlss_grow/service.py — at boot
def _init_actuators_with_health(...):
    try:
        actuators = AutomationPhatActuators(...)
        return actuators, "untested"  # init OK; not yet exercised
    except Exception as exc:
        log.warning("Automation HAT init failed: %s — pump/light unavailable", exc)
        return None, "no_hardware"

# Then when emitting capabilities:
caps = [
    Capability(channel="soil_moisture", hardware="seesaw", is_required=True,
               health="connected" if soil_reading_ok else "no_hardware"),
    Capability(channel="pump", hardware="automation_phat", is_required=False,
               health=actuator_health),
    Capability(channel="light", hardware="automation_phat", is_required=False,
               health=actuator_health),
    # ...
]
```

Server `handle_capabilities`:
- Persist `health` into `details_json["health"]`

Server `handle_telemetry`:
- After writing the telemetry row, if `pump_state == 1`, update pump capability's health to `"connected"` in `grow_unit_capabilities.details_json`
- Same for light

Server `handle_event`:
- For `kind=watering_event`: same as telemetry pump-promotion

Server `health_watchdog.py`:
- Module-level `_last_command_sent_at: dict[(unit_id, channel), datetime]`
- `record_command_sent(unit_id, channel)` called from `api_grow_units.py::water_now`
- `check_unresponsive(unit_id, channel, timeout_s=30)` called periodically OR lazily on each GET (preferred — no background task)
- For lazy: the GET handler calls `check_unresponsive` for each actuator capability; if `now - last_command > timeout AND no watering_event in that window`, mark unresponsive

Frontend:
```javascript
// static/js/grow/components/quick-controls.mjs (extract from unit_detail.mjs)
function actuatorHealth(unit, channel) {
  const cap = (unit.capabilities || []).find(c => c.channel === channel);
  return cap ? (cap.health || "untested") : "no_hardware";
}

function applyHealthStyling(btn, health) {
  if (health === "connected") {
    btn.disabled = false;
    btn.classList.remove("greyed", "unresponsive");
  } else if (health === "untested") {
    btn.classList.add("greyed");
    btn.title = "Click to test — connect 12V PSU first";
  } else if (health === "unresponsive") {
    btn.classList.add("greyed", "unresponsive");
    btn.disabled = true;
    btn.title = "Last command didn't reach the unit. Check power + cabling.";
  } else if (health === "no_hardware") {
    btn.classList.add("greyed", "no-hardware");
    btn.disabled = true;
    btn.title = "Hardware not detected at boot.";
  }
}
```

CSS additions:
```css
.du-act-btn.greyed { opacity: 0.5; cursor: not-allowed; }
.du-act-btn.unresponsive { border-color: #c84747; }
.du-act-btn.no-hardware { border-color: #555; }
.cap-health-pill.disconnected { background: #2c3540; color: #888; }
.cap-health-pill.untested { background: #2a3a4d; color: #7d92a8; }
.cap-health-pill.unresponsive { background: #4d2424; color: #c84747; }
```

- [ ] **Step 3: Run tests, confirm pass; full regression sweep**

- [ ] **Step 4: Commit**

```
Add capability health field for sense-only-mode UI degradation (Task 1)
```

---

## Task 2: Settings → Grow page

The largest remaining Phase 2 item. New top-level page under `/settings/grow` with three sections:

### 2a. Enrollment key rotation
- One-shot generate-and-reveal flow (current key replaced with new argon2-hashed value, raw shown once)
- Admin-only (mirrors `peek_enrollment_key` from Phase 1)
- New endpoint: `POST /api/grow/enrollment-key/rotate` → returns new raw key, persists hash
- Existing `app_settings` keys: `grow_enrollment_key_hash` and `grow_enrollment_key_raw_pending_reveal`

### 2b. Default tunables editor
- Edits the seeded rows in `grow_plant_profiles` table (e.g. tomato/vegetative target=55 etc.)
- Per-(plant_type, phase) tunables editing
- `is_shipped` rows are editable but flagged in UI ("modified from default")
- New endpoint: `PUT /api/grow/plant-profiles/<id>` (admin)

### 2c. Holiday mode toggle
- Existing `app_settings` key `grow_holiday_mode` (currently always `0`)
- New endpoint: `PUT /api/grow/settings/holiday-mode`
- Firmware reads on `config_changed` push (existing config_sync would need to include this — extend `pull_unit_config` response)
- Behavior: when ON, pump pulses are skipped; light schedule continues; telemetry continues. (User leaves for vacation, sensor logs continue but plant doesn't get over-watered.)

### Files
- `mlss_monitor/routes/api_grow_settings.py` (new) — 3 new endpoints
- `templates/grow_settings.html` (new) — 3 panel sections
- `static/js/grow/settings.mjs` (new) — orchestrator
- `static/js/grow/components/enrollment-key-rotator.mjs` (new)
- `static/js/grow/components/plant-profiles-editor.mjs` (new)
- `static/js/grow/components/holiday-mode-toggle.mjs` (new)
- `mlss_monitor/routes/pages.py` — add `/settings/grow` route
- 3 server test files
- 3 JS test files

### Steps (high-level — break into sub-tasks during dispatch)

- [ ] 2a. Enrollment key rotation endpoint + UI + tests
- [ ] 2b. Plant profiles editor endpoint + UI + tests
- [ ] 2c. Holiday mode endpoint + firmware sync + UI + tests
- [ ] 2d. Wire `/settings/grow` page route + nav link

---

## Task 3: Photo lightbox

- Click any photo (latest on Live tab, hero on Configure, scrubber img on History) → modal
- Modal: full-size photo, prev/next arrows, ESC/click-outside to close
- Pure frontend; no backend changes (uses existing `/photos/<id>` endpoint)

### Files
- `static/js/grow/components/photo-lightbox.mjs` (new)
- `static/css/grow.css` — lightbox styles
- Wire-up in `static/js/grow/components/photo-timelapse.mjs` and the Live tab's photo panel
- `tests/js/test_photo_lightbox.mjs` (new)

### Steps

- [ ] Write failing tests
- [ ] Implement lightbox component (modal, prev/next, keyboard handlers)
- [ ] Wire onClick from existing photo elements to open lightbox
- [ ] Commit

---

## Task 4: Fleet filter/sort row

On the main Grow tab (`/grow`), add a row at the top of the fleet view with:
- Filter chips: phase (5 options), status (online/offline), plant_type
- Sort dropdown: label / last_seen / moisture
- Pure frontend (sort/filter happens client-side over the existing fetched fleet data)

### Files
- `static/js/grow/components/fleet-filter-row.mjs` (new)
- `static/js/grow/fleet.mjs` — extend to apply filters/sort before rendering cards
- `tests/js/test_fleet_filter_row.mjs` (new)

### Steps

- [ ] Write failing tests
- [ ] Implement filter/sort row + state management
- [ ] Wire into `fleet.mjs` rendering
- [ ] Commit

---

## Task 5: E2E stack test for sense-only mode

Real Flask app + admin session. Boot a fake firmware that emits capabilities with `health="no_hardware"` for pump+light + `"connected"` for soil moisture. GET `/api/grow/units/<id>` and assert the response contains the health states. Send a telemetry frame with pump_state=1 → re-fetch → assert pump's health is now `"connected"`.

### Files
- `tests/grow_server/test_sense_only_mode_e2e.py` (new)

### Steps

- [ ] Write the e2e test (mirror `test_configure_e2e.py` fixture pattern minus the WS push assertions)
- [ ] Commit

---

## Self-review notes

- Sense-only mode is FIRST because the user's first deployment depends on it. Settings/lightbox/filter are nice-to-haves that don't block deployment.
- The health field uses existing `details_json` storage — zero schema migration. If a future need arises (querying by health), promote to a real column.
- The watchdog is lazy (called from GET handler, not a background task) — keeps the architecture simple. Background watchdog is Phase 3 if needed.
- Holiday mode firmware sync extends `pull_unit_config` (Task 8 from Configure plan) — small additive change to the GET /config response shape.
- All new endpoints: server-side validation via pydantic in `mlss_contracts.config_payloads` if reusable, otherwise inline.

---
