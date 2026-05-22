# 🛠️ Bugs, Improvements & Learning Roadmap

[Back to main README](../readme.md)

This section tracks known issues, UX limitations, and planned enhancements to the MLSS Monitor system, particularly around inference accuracy, visualisation, and adaptive learning.

Shipped feature blocks live at the bottom of the file as historical
record.

---

## 🐛 Bug: Inference Card Plot UX & Rendering Issues

### Symptoms

* Plot appears cut off
* Low usefulness / unclear meaning
* Poor scaling and layout
* Minimal or confusing data shown

---

### Likely Root Causes

* Fixed container height / CSS overflow
* Plotly not resizing correctly
* Weak data selection (wrong window or signals)
* No contextual framing (baseline vs spike)

---

### Problem

The plot currently shows "sensor activity" but lacks:

* Context
* Focus
* Interpretability

---

### Proposed Improvements

#### 1. Redefine Purpose

**Option A — Key Signal Focus**

* Show only relevant sensors per inference
* Highlight trigger + supporting signals

**Option B — Before/After View**

* Show:

  * Baseline
  * Peak
  * Recovery

**Option C — Normalised Signals**

```
(value - baseline) / baseline
```

---

#### 2. UI Fixes

* Ensure responsive sizing:

  * `responsive: true`
  * `autosize: true`
* Increase minimum height (~250px)
* Add:

  * Axis labels
  * Legend
  * Tooltips

---

#### 3. Add View Modes

* Raw
* Normalised
* Single Sensor
* Multi-Sensor

---

#### 4. Event Context Enhancements

* Vertical event marker
* Highlight detection window
* Emphasise peak values

---

## 🐛 Bug: Correlation Plot Scaling & Interpretability

### Symptoms

* Sensor values not comparable
* Large values dominate (eCO2 vs PM)
* Hard to see relationships

---

### Root Cause

Different scales:

* eCO2: 100–2000+
* TVOC: 10–100s
* PM: 1–50

Raw plotting makes comparison meaningless.

---

### Core Insight

Correlation ≠ absolute values
It's about:

* Direction
* Magnitude of change
* Co-movement

---

### Proposed Solutions

#### Option 1 — Normalised Overlay (Recommended)

Z-score:

```
z = (x - mean) / std
```

Or baseline ratio:

```
ratio = x / baseline
```

---

#### Option 2 — Indexed Time Series

```
index = value / value_at_start * 100
```

---

#### Option 3 — Small Multiples

* Separate plots per sensor
* Shared time axis

---

#### Option 4 — Dual Axis (Not Recommended)

* Complex and confusing at scale

---

### Recommended Implementation

Default:

* Normalised (z-score or ratio)

Add toggles:

* Raw
* Normalised
* % change

Enhance hover:

* Show raw + transformed values

---

### Bonus: Correlation Insights

Extend existing calculations:

* Compute correlation matrix over selected window
* Display:

  * Strongest relationships
  * Correlation strength (r)

---

## 🔭 Future Direction: Explainable Events

Combine:

* Tagged data
* Feature vectors
* Correlation signals

To generate explanations like:

> "This event was likely caused by cooking because PM2.5 and TVOC rose together by 2.3× baseline, matching previous tagged cooking events."


---

## 🐛 Bug: PM sensor read-path reliability (MLSS server)

After fixing the double-poll bug (commit `e8712db`) and the serial-console hostage situation (operator: `do_serial 2` + `serial-getty@ttyAMA0` disabled + reboot), PM data flows but the read path is still noisy. Three concrete issues observed in production journal:

1. **`PM sensor serial error: read failed: [Errno 9] Bad file descriptor`** — happens between retry attempts inside the runner loop. Suggests the fd is being closed mid-retry-sequence and then `read()` is called again on the closed fd. Doesn't lose data (the retry eventually succeeds on a fresh open) but produces avoidable warnings.
2. **`device reports readiness to read but returned no data (device disconnected or multiple access on port?)`** — classic Linux `select()` returning ready but `read()` getting zero bytes. The runner should treat this as a non-fatal partial-frame condition (re-try without closing the fd) rather than a hard error.
3. **`PM sensor unexpected error: 'NoneType' object cannot be interpreted as an integer`** — Python exception in the frame parse path. Something's expecting a length/checksum byte and getting `None` (partial frame where the parser tries to interpret a missing field as int). Caught broadly by the runner so it doesn't crash, but means one branch of the parser doesn't validate frame completeness before indexing.

**File**: `sensor_interfaces/sb_components_pm_sensor.py`. All three issues live in or near the `read_pm` / retry helper / frame parse code.

**Fix scope**: small. Tidy the fd lifecycle (don't close between retries unless the error is fatal), guard the parser against partial frames, demote the "device readiness but no data" line from error → debug. Adding a unit test that feeds a deliberately truncated frame and asserts the parser returns `None` cleanly would close the regression door.

**Why deferred**: data is flowing — these are quality-of-log issues + a minor robustness gap, not a data-correctness gap. Worth doing but no user-visible benefit until done.

---

## Plant Grow Unit roadmap

### Phase 4 (polish) — remaining

> Phase 2 (fleet/Configure/History/Settings tabs), Phase 3 (Diagnostics tab, grow_errors UI, buffer-replay UI, storage warnings), and most of Phase 4 (thumbnail endpoint, USB-SSD boot guide, Pi-image builder, local wheel infra, mobile-optimised fleet view, plant journal, time-lapse video) have all shipped. Only one Phase 4 item is still outstanding:

- **Local read-only status UI on the grow unit itself** — tiny Flask app on a separate port (e.g. `http://<pi-ip>:8080/`) so an operator can SSH-free check the unit's health when MLSS is unreachable. Surfaces: live sensor readings, buffered-message + buffered-photo counts, last successful WS connect time, last 50 log lines, WiFi RSSI. **Read-only — no actuator controls** (those route via MLSS so audit/RBAC stays consistent). No auth (LAN-only by definition; same trust model as MLSS). Particularly useful for diagnosing "is the Pi alive when MLSS is down?" scenarios — the firmware design tolerates MLSS outages (buffer + replay) but currently you need SSH + journalctl to verify. Discovered as a real gap during the first physical deployment when the MLSS server's SD card failed mid-deployment and the operator had no quick way to verify the Pi was still capturing.

### Phase 5 (smarts)

- Image-based phase classifier
- Plant-stage-aware PID adjustments
- Cross-unit anomaly detection
- Reservoir / water budget tracking

### Hardware/reliability deferred

- **Hardware watchdog (`/dev/watchdog`)** on Pi Zero — designed in but not wired up due to risk of misconfigured timer rebooting healthy Pi mid-write. Re-evaluate if a unit silently wedges in production despite systemd watchdog.

### Grow unit hardware additions

> Tracking new sensors / actuators to add to the per-unit hardware stack. Each entry covers the part to source, the firmware-side work, the server-side work, and how it slots into existing systems (capability auto-detect, plant happiness, plant_profiles). Do these on dedicated branches (one branch per sensor) so each can be reviewed + tested independently before merge.

#### Humidity / air-temperature sensor + VPD

Today the grow unit reports `soil_moisture`, `soil_temp_c`, `light_state`, `pump_state`, `camera`. Air temperature + relative humidity are measured on the MLSS server only — useless once a grow unit lives in a different room from MLSS. Add an air-T+RH sensor on the grow unit itself so we can:
- Display per-unit air temperature & humidity tiles
- Compute Vapor Pressure Deficit (VPD), the more meaningful "is the plant transpiring happily?" metric
- Extend today's plant-happiness indicator to air temperature + VPD

**Hardware to source** (any one):
- **Adafruit AHT20** (~£5, I2C 0x38, ±0.3 °C / ±2 % RH) — same chip MLSS already uses, driver code is near-zero-cost to port from `external_api_interfaces/aht20.py`. Recommended starting point.
- **Sensirion SHT40 / SHT41** (~£10, I2C 0x44, ±0.2 °C / ±1.5 % RH) — industry-standard horticulture sensor. Better long-term drift than AHT20. Adafruit STEMMA QT cable plug-and-play.
- **Bosch BME680** (~£12, I2C 0x77) — also gives barometric pressure + gas/VOC. Overkill unless we ever care about CO₂ proxy trends.

All three share the existing I2C bus on the grow unit (Seesaw soil sensor at 0x36, no conflict at 0x38 / 0x44 / 0x77). Wiring is the same 4-pin VCC / GND / SDA / SCL we already documented for the Seesaw in [`docs/PLANT_GROW_UNIT_HARDWARE.md`](PLANT_GROW_UNIT_HARDWARE.md).

**Firmware work** (`grow_unit/src/mlss_grow/`):
- New `sensors/aht20.py` (or `sht40.py` depending on chip choice) — mirror the existing `seesaw.py` pattern: probe at startup, expose `read()` returning `(temp_c, rh_pct)`, register as a capability so it auto-announces on connect
- Extend the periodic poller in `service.py` to read + include `air_temp_c` and `air_humidity_pct` in telemetry frames
- Add `capabilities.py` entry for the two new channels

**Server work**:
- `mlss_monitor/grow/handlers.py` LastKnownState TypedDict + `_last_known_state` projection: add `air_temp_c` + `air_humidity_pct`
- `database/grow_schema.py` `grow_telemetry`: columns already exist (`air_temp_c REAL`, `air_humidity_pct REAL`) — no schema change needed; verify the WS handler writes them when present
- `static/js/grow/unit_detail.mjs` CHANNEL_DISPLAY: add entries so tiles render for the new channels
- Extend the plant-happiness indicator from commit `80f2a3d` to cover `air_temp_c`: 8 more threshold columns on `grow_plant_profiles` + seed values per plant×phase (researched horticultural references)

**VPD compute** (new capability built on the above):
- VPD formula: `SVP_kPa = 0.6108 × exp(17.27 × T / (T + 237.3))` then `VPD_kPa = SVP × (1 − RH/100)`. Pure compute, no extra hardware.
- Add `vpd_kpa` as a synthetic / derived channel: NOT a sensor, but a per-tick computation from `air_temp_c` + `air_humidity_pct`. Compute in the firmware (cleaner — the unit owns its readings) or compute server-side in `_last_known_state` (cheaper change). Recommend server-side for v1: keeps the firmware contract stable.
- Add VPD tile to Live readings + per-plant VPD thresholds (extend the happiness work). Target VPD bands published in horticultural literature: ~0.4–0.8 kPa seedlings, ~0.8–1.2 kPa vegetative, ~1.2–1.6 kPa flowering/fruiting, >1.6 kPa transpiration stress.

**Future extension — LVPD (leaf VPD)**: closer to "true plant happiness" than air-VPD because it uses leaf surface temperature (typically 2–5 °C cooler than air due to evapotranspiration) instead of air temperature. Requires an IR thermopile sensor (MLX90614, ~£8 I2C 0x5A) pointed at the canopy. Worth re-evaluating once air-VPD is in production and we have a baseline to compare against.

---

## 🔌 Feature: Configurable smart-plug effectors (hub-scoped or grow-unit-scoped)

### Problem

Today the hub assumes **exactly one** smart plug, hardcoded as a fan
via `MLSS_FAN_KASA_SMART_PLUG_IP` in `.env`. Adding a second device —
say a heater for cold mornings (hub-scoped) or a heat pad for a
specific chilli plant (grow-unit-scoped) — requires editing config +
extending `fan_controller.py` to know about the new role. No operator
UI; no per-device rules; no way to wire a plug to a specific plant's
sensor.

### Goal

Let an admin add, configure, **reconfigure**, and remove **N smart
plugs** from the operator UI, each declaring its **effector type**
AND its **scope**:

- **Hub-scoped** plugs respond to the hub's whole-room sensors
  (TVOC, eCO₂, PM, temp, humidity). e.g. AC, dehumidifier, fan,
  whole-room heater.
- **Grow-unit-scoped** plugs respond to a specific plant's per-unit
  sensors (soil moisture, soil temp, air-T+RH when present). e.g.
  heat pad under one plant, top-up grow light for a single shelf,
  per-pot humidifier.

The plug itself is always on the LAN and brokered by the hub. Only
the **rule evaluation source** differs.

### Scope examples

| Effector | Typical scope | Why |
| --- | --- | --- |
| **Fan** | **Hub** | **Room ventilation — the current default** |
| AC unit | Hub | Cools the whole room |
| Whole-room heater | Hub | Heats the whole room |
| Carbon-filter fan | Hub | Air-quality for the room (longer min-on for the carbon media) |
| Dehumidifier | Hub | Room humidity |
| Heat pad | Grow unit | One plant's soil/root warmth |
| Top-up grow light | Grow unit | One plant's PAR budget |
| Per-pot mini humidifier | Grow unit | One plant's microclimate |
| Reservoir refill pump (future) | Grow unit | One unit's water supply |

### Effector types (ENUM)

| Type | Trigger signal(s) | Compatible scopes |
| --- | --- | --- |
| `fan` | TVOC · eCO₂ · PM₂.₅ · humidity · temp high | Hub |
| `fan_carbon_filter` | Same as `fan` + longer min-on time | Hub |
| `ac` | Temp high + humidity | Hub |
| `whole_room_heater` | Temp low | Hub |
| `humidifier` | Humidity low | Hub OR grow unit (per-pot variant) |
| `dehumidifier` | Humidity high · mould risk | Hub |
| `light_supplementary` | Schedule + (optional) lux threshold | Hub OR grow unit |
| `heat_pad` | Soil-temp low OR air-temp low (per-unit) | Grow unit |
| `generic` | Manual / scripted only | Either |

### Entry points (two add-paths, one wizard)

Admins can add a plug from two places. Both open the **same wizard**;
only the default value of the scope picker differs:

| Where the admin clicks | Default scope | Editable in wizard? |
| --- | --- | --- |
| `+ Add effector` button on `/controls` (admin-only) | **Hub** | Yes — admin can switch to any grow unit during setup |
| `+ Add effector` button inside Grow Unit settings (admin-only) | **This grow unit** | Yes — admin can switch to Hub or a different grow unit during setup |

Rationale: the admin's *intent* is usually obvious from where they
clicked. Whoever's looking at the controls page is thinking about
whole-room stuff; whoever's in a grow unit's settings is thinking
about that plant. The default reflects that, but the wizard never
locks them in.

### Reconfigurable scope (post-creation)

A plug's scope is **not fixed at creation time**. Every effector card
shows a **settings cog (⚙)** to admins; clicking it opens the slide-out
side panel (see design doc) pre-filled with the current values. Admins
can:

- Change the effector type (with re-validation of compatibility:
  e.g. you can't reassign a Hub-only `ac` plug to a grow unit
  scope — the picker greys those out).
- Move the plug between Hub ↔ any grow unit, or between grow units.
- Edit the per-type rule blob.
- Rename / re-IP / disable / delete.

Scope changes are reflected immediately in the live decision loop —
the next evaluation tick reads from the new sensor source.

### Visual + interaction design

A hi-fi React-on-Babel design handoff is in the tree:
[`docs/EFFECTOR_NODE_MAP_DESIGN.md`](EFFECTOR_NODE_MAP_DESIGN.md)
(wrapper) → [`docs/assets/effector-map-handoff/`](assets/effector-map-handoff/)
(live prototype — open `index.html` in a browser).

Summary of what the design specifies:

- **Pan/zoom-able node-map canvas** replaces today's single-fan
  `/controls` page. Hub at the centre with live sparklines (temp /
  RH / CO₂); N grow units with per-plant readings + phase tag; N
  effectors connected to either hub (blue edges) or specific grow
  units (green edges).
- **Per-effector Auto/ON/OFF segmented control on the card itself** —
  most common operation needs no modal.
- **Sticky telemetry topbar** with mission-clock, hub status, totals
  (grow units / effectors / active / auto vs forced split).
- **Slide-out side panel** for full configuration (type, scope,
  rules, manual override, re-parenting) — opens on node click.
- **Edges** colour-coded by parent type, weighted by live state
  (solid on, dashed off).
- **Re-arrange** + **Recenter** buttons in the topbar; node positions
  persist across sessions.
- **AstroUX design system** (matches the rest of MLSS). Use the
  official `@astrouxds/astro-web-components` library; the bundle's
  CSS is a reference-only hand-rolled approximation.
- **Tweaks panel** in the bottom-right of the prototype is a
  design-time tool only — omit from production.

### Data model sketch

```sql
CREATE TABLE smart_plugs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    label            TEXT NOT NULL,         -- "Living room AC", "Habanero heat pad"
    ip_address       TEXT NOT NULL UNIQUE,  -- LAN IP of the Kasa plug
    protocol         TEXT NOT NULL DEFAULT 'kasa',  -- future: 'shelly', 'tuya', 'matter'
    effector_type    TEXT NOT NULL CHECK(effector_type IN (
                         'fan', 'fan_carbon_filter', 'ac',
                         'whole_room_heater', 'humidifier', 'dehumidifier',
                         'light_supplementary', 'heat_pad', 'generic'
                     )),
    scope            TEXT NOT NULL CHECK(scope IN ('hub', 'grow_unit')),
    grow_unit_id     INTEGER REFERENCES grow_units(id) ON DELETE SET NULL,
    is_enabled       INTEGER NOT NULL DEFAULT 1,
    rules_json       TEXT,                  -- per-type rule blob (thresholds, schedules)
    layout_json      TEXT,                  -- node-map persisted position (x,y)
    last_state       TEXT,                  -- 'on' / 'off' / 'unknown' / 'unreachable'
    last_state_at    DATETIME,
    created_at       DATETIME NOT NULL,
    CHECK ((scope = 'hub'       AND grow_unit_id IS NULL) OR
           (scope = 'grow_unit' AND grow_unit_id IS NOT NULL))
);
CREATE INDEX idx_smart_plugs_grow_unit ON smart_plugs(grow_unit_id);
```

The existing `fan_settings` table content seeds one row in
`smart_plugs` at first migration (scope=`hub`, type=`fan`, rules from
the existing fan-controller config). The live single-fan flow keeps
working unchanged through the migration.

### Rule evaluation — where it runs (v1)

| Option | Where rules evaluate | Pros | Cons |
| --- | --- | --- | --- |
| **A — Hub-mediated** (recommended for v1) | All plug decisions happen on the hub. Grow-unit-scoped rules use telemetry that the grow unit already streams via WSS. Hub commands the Kasa plug. | Simple. One control surface. No new firmware deps on Pi Zero W. Matches existing single-fan flow. | Per-unit effectors stop responding during a hub outage. |
| **B — Edge-local** | Grow unit firmware talks Kasa directly to its associated plug. Hub only configures the rules. | Survives hub outages. Matches the existing "plants survive an MLSS outage" design principle. | Adds python-kasa to Pi Zero W firmware (~10 MB). New per-unit credentials/discovery. Two control paths to keep coherent. |

**Lean A for v1.** Per-unit effectors here are *comfort* (heat pad,
top-up light) not *safety* (pump, primary grow light — those already
live on the grow unit's Automation pHAT and are driven by the local
safety loop). Losing comfort during a brief hub outage is acceptable.
Move to B later if real-world usage shows hub outages cause plant
stress.

### Code touchpoints

- **`mlss_monitor/effectors/`** new package — one module per type
  (the existing top-level `effectors.py` becomes this package's
  `__init__.py` with the registry). Each module implements a small
  `EffectorController` ABC:
  ```python
  class EffectorController(ABC):
      def should_be_on(reading: SensorReading, rules: dict) -> bool
      def label() -> str
      def rules_schema() -> type[BaseModel]
      def compatible_scopes() -> set[Scope]  # {Scope.HUB, Scope.GROW_UNIT}
  ```
- **`fan_controller.py`** becomes the `fan.py` implementation of the
  ABC. The current `state.fan_smart_plug` becomes a dict
  `state.smart_plugs[plug_id] -> KasaSmartPlug`, populated from the
  new table at startup.
- **`mlss_monitor/effector_evaluator.py`** — new module: the periodic
  loop that asks each enabled plug "should you be on?" — sources the
  reading from `state.hot_tier` (hub) or `state.grow_telemetry` (per
  grow_unit_id) and applies the type-specific controller.
- **`mlss_monitor/routes/api_effectors.py`** — new blueprint:
  - `GET/POST /api/admin/effectors`
  - `PUT/DELETE /api/admin/effectors/<id>`
  - `POST /api/admin/effectors/<id>/test` — flick the plug for 2s
  - `POST /api/admin/effectors/<id>/toggle` — manual override
  - `GET /api/effectors/state` — live state + node-map layout
- **`database/init_db.py`** — new `smart_plugs` table + idempotent
  seed of one row from existing fan config so the migration is
  invisible to current single-fan users.
- **`templates/controls.html`** — replaced entirely by the node-map
  view from the design doc (admin-only `+ Add effector` action lives
  in the topbar).
- **`templates/grow_unit_detail.html`** — admin-only `+ Add effector`
  button in the settings panel + a small "Effectors for this unit"
  list scoped to this `grow_unit_id`.
- **`static/js/effectors/`** — full node-map implementation per the
  design doc (graph, side panel, topbar, per-type rule editors).
  Reuse `@astrouxds/astro-web-components` rather than the
  hand-rolled CSS in the handoff bundle.
- **`mlss_monitor/event_bus`** — `effector_state_changed` event so
  the node-map updates in real time over SSE.
- **Backup outbox** — `smart_plugs` becomes a replicated table so
  rotations / re-installs preserve effector config + node-map layout.

### Notification integration

The `notify_system_health` category in MLSS Mobile already covers
"sensor offline". Extend it to fire `effector_unreachable` when a
configured plug stops responding, with the deep_link routing to
the `/controls` node-map (or `/grow/<id>` for grow-unit-scoped plugs).

### Auth

- All CRUD: `admin` role (every settings cog visible only to admins).
- Manual toggle (`/toggle` endpoint): `controller` + `admin`. Matches
  the existing fan-override pattern.
- Read-only state + map view: any logged-in role.

### Considerations

- **Conflict avoidance:** two `whole_room_heater` plugs with
  overlapping temp floors → both come on. AC + heater fighting →
  operator's problem; we'll log a warning when both want to be on
  simultaneously but won't override. Single-household trust.
- **Compressor protection:** AC / heat-pump effectors enforce a
  minimum-off time (default 5 min) to prevent rapid cycling
  damaging the compressor. Configurable per plug.
- **Min-on time for carbon filters:** the `fan_carbon_filter` type
  enforces a longer min-on (default 5 min) so activated-carbon
  media has time to actually scrub VOCs out of the airflow.
- **Vendor lock-in:** Kasa-only for v1. The `protocol` column leaves
  the door open. A Shelly / Tuya / Matter adapter slots in as a new
  protocol module without UI changes.
- **YAGNI line:** no per-plug ML, no cross-plug scenes (e.g. "AC on
  ⇒ disable humidifier"), no calendar-based override schedules
  beyond the light type. Simple if-this-then-that rules per plug.

### Effort

~5-6 days. Schema migration + 9 controller classes + node-map UI
(per the handoff design) with two entry points + per-type rule
editors + side panel + display-only integration in grow-unit detail
page + tests. The node-map (pan/zoom canvas, SVG edges, drag-to-position
with persistence, SSE live colouring) is the biggest single chunk;
the controller registry is mostly a polymorphism refactor of the
existing fan code.

### Why this matters

- Unlocks the system for non-fan use cases (chilli plant wants a
  heat pad on cold nights; future hydroponic shelves want
  supplementary light per row; a room with poor passive ventilation
  wants AC + dehumidifier).
- Removes the hardcoding tax on adding any new effector role.
- Establishes the **per-unit effector** category as a first-class
  concept — opens the door to deeper grow-unit automation
  (reservoir pumps, nutrient dosers) without further schema
  thrashing.
- The node-map makes the topology legible at a glance — the
  operator sees the whole environmental-control loop in one view
  rather than tab-hopping.

---

## ✅ Shipped: Fleet-view "trust anchor" badge — not yet

> Carry-over from the `feature/mdns-resilient-host` branch — flagged here
> rather than as part of the resilient-host work because it's UI-only
> and slots more naturally with the upcoming effector node-map work.

**Problem.** After the CA-publish + `install.sh` rotation-safe update,
existing grow units still pin the LEAF cert (TOFU) and will break on
the next cert rotation. There's no way to tell at a glance which
units have which trust anchor.

**Proposal.** Have the grow firmware report its `/etc/mlss/server.crt`
fingerprint (SHA256 truncated to 8 chars) on every WS handshake or
capability handshake. The hub compares to its own ca.crt fingerprint
and its current leaf fingerprint, and stores a flag on `grow_units`
(`trust_anchor` = `'ca'` / `'leaf'` / `'unknown'`). The fleet card
shows a tiny `🔒 CA` badge for rotation-safe units and `⚠ leaf`
otherwise, with a hover tooltip explaining the difference + linking
to a "re-run install.sh on this unit to upgrade" runbook step in
PLANT_GROW_UNIT_SETUP.md.

**Effort:** ~1 day. New column on `grow_units`, capability protocol
extension (`fingerprint` field), fleet-card pill + tooltip, hub-side
comparison, one new test per side.

---

# Shipped

The blocks below document features that have landed in `main`, kept
as a historical record of what each feature actually delivered.

---

## ✅ Shipped: Off-Pi backup pipeline (all 9 phases)

**Date shipped:** 2026-05-18 (branch `feature/backup-to-home-server`, merged to main)

Asynchronous replication of canonical ML data from the hub's SQLite to
a home Postgres + S3-compatible backend, so SD-card failure / fire /
theft of the hub doesn't lose years of training data.

**What landed:**
- Outbox storage (`outbox_changes`, `outbox_blobs`,
  `outbox_delete_scope`, `bootstrap_progress`) with a lint guard
  preventing direct writes to replicated tables.
- `@tee_to_outbox` decorator wrapping every replicated-table writer
  so the outbox pointer commits in the same transaction as the live
  row.
- Postgres + S3 clients (S3-compatible — MinIO, AWS, Cloudflare R2
  all work) with config persisted to `app_settings`.
- `BackupWorker` daemon thread per pipeline (`db` + `files`), state
  machine with exponential backoff (1 s → 600 s cap), hot-reload
  via the event bus.
- Historical bootstrap scanner (`backup/bootstrap.py`) for one-shot
  back-fill of a clean Postgres from the live DB.
- Admin REST API (`/api/admin/backup/{config,status,test,init,maintenance}`)
  + Settings → Backup admin UI with SSE-driven live status panel.
- E2E test suite using testcontainers for Postgres + MinIO (six
  scenarios including overnight-outage simulation + idempotent
  resend).

**Operator docs:** [docs/BACKUP.md](BACKUP.md)
**Schema:** [docs/DATABASE.md → Backup outbox tables](DATABASE.md#backup-outbox-tables)
**Source:** `mlss_monitor/backup/`, `mlss_monitor/routes/api_backup.py`,
`templates/admin_backup.html`, `static/js/backup/`, `tests/backup_e2e/`.

---

## ✅ Shipped: Event Tagging & Learning System

User-driven tagging plus heuristic + ML attribution for inference
events.

**What landed:**
- `event_tags` table — user-supplied tags on inference rows.
- `mlss_monitor/routes/api_tags.py` — CRUD endpoints.
- `mlss_monitor/attribution/engine.py` — heuristic source-fingerprint
  attribution with confidence scoring.
- `mlss_monitor/anomaly_detector.py` + `multivar_anomaly_detector.py`
  — River-based online unsupervised detection.
- Inference engine + classifier admin tabs (Settings → Insights
  Engine) for rules, fingerprints, anomaly channels, classifier
  retrain controls.
- Inference cards on the dashboard display the attributed source +
  user-tag side-by-side.

**Source:** `mlss_monitor/attribution/`, `mlss_monitor/inference_engine.py`,
`mlss_monitor/routes/api_tags.py`, `mlss_monitor/routes/api_insights.py`.

---

## ✅ Shipped: MLSS Mobile (PWA + Web Push)

**Date shipped:** 2026-05-21 (branch `feature/mlss-mobile`)

**What it solved:**

- Combined roadmap items: real-time notifications + mobile-first
  install.
- iOS Safari PWA install enabled via a local CA + iOS
  `.mobileconfig` profile generator — avoids Apple's $99/yr Developer
  Program requirement that a "native" App Store app would need.
- Web Push lockscreen notifications via VAPID + `pywebpush` —
  covers air-quality inferences, grow-unit errors, system-health
  failures, and backup-pipeline issues.
- Per-user severity floors (`off` / `info` / `warning` / `critical`)
  across four categories, with a 60-second
  per-(user, category, severity) coalesce so a burst of related
  events collapses into a single `"3× ..."` notification instead of
  buzzing the phone three times.
- `/notifications` inbox page for reviewing the last 30 days of
  events (iOS expires lockscreen notifications after a few days —
  the inbox is the durable record).
- Responsive CSS (`static/css/mobile.css`) overhauls the UI for
  iPhone: bottom-fixed tab nav, 44 px tap targets, 16 px font on
  inputs (avoids iOS zoom-on-focus), single-column cards on narrow
  screens.

**User docs:** [docs/MOBILE.md](MOBILE.md)
**Schema:** [docs/DATABASE.md → Notifications](DATABASE.md#notifications-mlss-mobile)
**Config reference:** [docs/CONFIGURATION.md → Notifications](CONFIGURATION.md#notifications-mlss-mobile)

---

## ✅ Shipped: Resilient hub host resolution

**Date shipped:** 2026-05-21 (branch `feature/mdns-resilient-host`)

Firmware now resolves the hub address through `/etc/mlss/host` →
`/etc/mlss/host-cache` → mDNS `mlss.local`. Post-power-cut Avahi
outages no longer wedge units offline — the cache fallback kicks in
within seconds. The mDNS fallback also self-heals when the hub's
static IP changes (rare, ~once a year). Hub-side: no changes
(`mlss.local` is already auto-published by Pi OS's default Avahi
config).

Key behaviours:
- Strategy / Chain-of-Responsibility pattern — three independent
  `ResolutionStep` callables iterated by a 5-line orchestrator.
  Adding a 4th step is one line in `DEFAULT_STEPS`.
- `Candidate.is_authoritative` gates whether a successful connect
  rewrites `/etc/mlss/host` (only mDNS-discovered IPs do).
- Hostname-downgrade guard: refuses to silently rewrite
  `mlss.local` → `<IP literal>` in the host file, preserving
  operator intent.
- Symlink-safe writes (`_write_atomically` refuses symlinks to
  prevent a write-where-root-points primitive).
- Privacy CI (`tests/test_no_private_ips_committed.py`) pins the
  invariant that no RFC 1918 private IP gets committed.

**User-facing docs:** [PLANT_GROW_UNIT_SETUP.md → Host resolution](PLANT_GROW_UNIT_SETUP.md#host-resolution-after-first-boot)
**Source:** `grow_unit/src/mlss_grow/host_resolver.py`,
`grow_unit/src/mlss_grow/host_bootstrap.py`
