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

## ✅ Shipped: MLSS Topology — pan/zoom node-map at /controls (2026-05-22)

**Branch:** `feature/mlss-topology` (PR pending).

Replaced the hardcoded single-fan `/controls` page with a generalised
smart-plug effector model and a pan/zoom topology view. Admins can
add, configure, reconfigure, and remove **N** smart plugs from the
operator UI without ever editing config or code.

**What landed:**
- **11 effector types** (`fan`, `fan_carbon_filter`, `circulation_fan`,
  `ac`, `whole_room_heater`, `humidifier`, `dehumidifier`,
  `light_supplementary`, `heat_pad`, `generic`, `co2_injector`) with
  per-type rule controllers behind a shared `EffectorController` ABC
  in `mlss_monitor/effectors/`.
- **Hub-scoped or grow-unit-scoped** plugs, **reconfigurable** post-
  creation via the slide-out side panel (re-parent picker, type
  re-validation, per-type rule editor, manual override).
- **Two add-effector entry points** wired to one wizard — the
  `+ Add effector` button on the `/controls` topbar and the equivalent
  button inside each grow unit's Configure tab.
- **Live SSE updates** (`effector_state_changed`) with rolling
  sparklines, debounced server-persisted node positions
  (`PATCH /api/effectors/layout`), Re-arrange + Recenter buttons.
- **Backwards compatibility:** legacy `state.fan_smart_plug` handle,
  `POST /api/effector`, and the entire `/api/fan/*` surface keep
  working (the latter now tag every response with RFC 8594
  `Deprecation: true` + a `Link` header pointing at the v2 API).

**Code touchpoints:** new `database/effectors_schema.py` +
`mlss_monitor/effectors/` package + `routes/api_effectors_v2.py` +
`routes/api_topology.py` + `static/js/topology/` + `static/css/topology.css`.
Legacy `mlss_monitor/fan_controller.py` was inlined into the effectors
package and deleted; legacy `static/js/{controls,fan}.js` and
`static/css/controls.css` were deleted.

**Design references:** [`docs/EFFECTOR_NODE_MAP_DESIGN.md`](EFFECTOR_NODE_MAP_DESIGN.md)
(wrapper) → [`docs/assets/effector-map-handoff/`](assets/effector-map-handoff/)
(live prototype). Implementation plan:
[`docs/superpowers/plans/2026-05-22-mlss-topology.md`](superpowers/plans/2026-05-22-mlss-topology.md).

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
