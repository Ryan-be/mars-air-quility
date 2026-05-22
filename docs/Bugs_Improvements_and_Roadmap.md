# 🛠️ Bugs, Improvements & Learning Roadmap

[Back to main README](../readme.md)

This section tracks known issues, UX limitations, and planned enhancements to the MLSS Monitor system, particularly around inference accuracy, visualisation, and adaptive learning.

---

## 🧠 Feature: Event Tagging & Learning System

### Summary

The current attribution system (source fingerprints) is heuristic-based and occasionally misclassifies events. Introduce a **user-driven tagging system** to label events with their true source and enable **incremental learning** over time.

---

### Goals

* Allow users to tag:

  * Inference events (primary)
  * Raw time ranges (future extension)
* Persist tags in the database
* Use tagged data to:

  * Improve attribution accuracy
  * Train lightweight online ML models

---

### Proposed Design

#### 1. Data Model

```sql
CREATE TABLE event_tags (
    id INTEGER PRIMARY KEY,
    inference_id INTEGER,
    tag TEXT,
    confidence REAL DEFAULT 1.0,
    created_at DATETIME,
    FOREIGN KEY (inference_id) REFERENCES inferences(id)
);
```

Optional future:

* Add `sensor_data_start_id`, `sensor_data_end_id` for manual window tagging

---

#### 2. UI Integration

* Add to inference card:

  * Dropdown: **“What caused this?”**
  * Options + free text
* Display:

  * User tag
  * Model attribution (side-by-side)

---

#### 3. Learning Strategy

##### Phase 1 — Assisted Attribution

* Use tags to:

  * Evaluate fingerprint accuracy
  * Build confusion matrix
  * Adjust heuristic weights

---

##### Phase 2 — Online Supervised Learning

Train a classifier:

* Input: `FeatureVector`
* Output: `source_tag`

Suggested models:

* `HoeffdingTreeClassifier`
* `LogisticRegression`

---

##### Phase 3 — Hybrid Attribution

Combine heuristic + ML:

```
final_score = 0.6 * fingerprint + 0.4 * ML
```

---

### Challenges

* Cold start (no labels)
* Label quality (user input errors)
* Class imbalance (many “normal” cases)

---

### Notes

Online learning is a strong fit:

* No retraining cycles needed
* Updates per event
* Works naturally with streaming data

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

The plot currently shows “sensor activity” but lacks:

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
It’s about:

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

> “This event was likely caused by cooking because PM2.5 and TVOC rose together by 2.3× baseline, matching previous tagged cooking events.”


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

### Phase 2 (next)
- Filter / sort row on Grow tab fleet view
- Per-unit Configure tab (light windows editor, plant profile picker, PID tunables, calibration two-step, soak-window override, intentional-friction safety override)
- Per-unit History tab (long-range moisture chart, photo timelapse scrubber)
- Settings → Grow page (enrollment key rotation UI, default tunables, holiday mode)
- Photo lightbox on click

### Phase 3
- Per-unit Diagnostics tab (WS connection log, sensor sanity, firmware version, danger zone)
- grow_errors UI surfacing (separate from the air-quality Incidents tab)
- Buffered-message replay UI
- Storage warning UI

### Phase 4 (polish)

> **Reordered from Phase 5 → Phase 4.** First physical deployment surfaced enough rough edges (SD-card failure mid-deploy, opaque deploy command, no-thumbnail fleet view, "wall wart" terminology, no on-Pi diagnostics) that polish should land before any ML work. Smarts moved to Phase 5.
>
> **Already landed in this overnight session:** `bin/deploy` script + readme; "wall wart" → "USB power adapter" sweep across `PLANT_GROW_UNIT_HARDWARE.md` and `PLANT_GROW_UNIT_SETUP.md`.

- ~~**Server-side photo thumbnail/resize endpoint**~~ — **shipped:** `GET /api/grow/units/<id>/photo/latest?size=thumb` and `GET /api/grow/units/<id>/photos/<photo_id>?size=thumb`. Pillow downscales to 320px on first request, caches to `data/grow_thumbnails/<unit_id>/<...>_w320.jpg`, reuses on subsequent hits. Fleet view (`grow-card.mjs`) now requests `?size=thumb` instead of the full ~2MB capture. `DELETE /api/grow/units/<id>/photos` invalidates the per-unit thumbnail cache.
- ~~**USB SSD boot guide for MLSS server and grow units**~~ — **shipped:** [`docs/USB_SSD_BOOT_GUIDE.md`](USB_SSD_BOOT_GUIDE.md). Hardware list, live `rsync` migration recipe with `fstab` / `cmdline.txt` PARTUUID fix-up, validation, rollback, troubleshooting. Notes that Pi Zero W grow units don't need this.
- ~~Custom Pi SD-card .img for one-step provisioning~~ — **infrastructure shipped:** `scripts/build_pi_image.sh` (wrapper around `pi-gen`), `scripts/stage-mlss-grow/` (apt package list, host-side + chroot run scripts, systemd unit, firstboot.sh, mlss-grow.yaml.template), `docs/PI_IMAGE_BUILD.md`. The image builder calls `scripts/build_local_wheels.sh` to bake locally-built `mlss-grow` + `mlss-contracts` wheels directly into the rootfs (no public package index dependency at provision time for our two packages). **Maintainer to-do** before first image release: run `bash scripts/build_pi_image.sh` on a Linux box (pi-gen needs chroot + binfmt_misc — won't work on macOS/Windows). After build, hand off the resulting `dist/mlss-pi-os-<version>.img.xz` per local distribution policy.
- ~~Local-only release infrastructure for `mlss-grow` / `mlss-contracts`~~ — **shipped:** classifiers/license/keywords/readme on `grow_unit/pyproject.toml` + `contracts/pyproject.toml`, root MIT `LICENSE`, `scripts/build_local_wheels.sh` (offline wheel builder writing to `dist/wheels/`), `scripts/_strip_pathdep.py` (post-build wheel patcher that fixes the path-baked `Requires-Dist` URL poetry would otherwise produce), `docs/RELEASE_PROCESS.md` (semver, version bump, local build flow). Public PyPI publication was scoped out — wheels stay private / local until that decision is revisited in a separate ticket.
- ~~Mobile-optimised fleet view~~ — **shipped** in `static/css/grow.css`. New `@media (max-width: 540px)` rules narrow grid padding, single-column cards, stacked page-header (full-width Add Unit button), horizontal-scrolling unit-detail tabs. New `@media (hover: none) and (pointer: coarse)` block enforces 44px minimum touch-target on `.px-btn`, `.gu-btn`, `.du-act-btn`, filter chips, and tabs.
- **Local read-only status UI on the grow unit itself** — tiny Flask app on a separate port (e.g. `http://<pi-ip>:8080/`) so an operator can SSH-free check the unit's health when MLSS is unreachable. Surfaces: live sensor readings, buffered-message + buffered-photo counts, last successful WS connect time, last 50 log lines, WiFi RSSI. **Read-only — no actuator controls** (those route via MLSS so audit/RBAC stays consistent). No auth (LAN-only by definition; same trust model as MLSS). Particularly useful for diagnosing "is the Pi alive when MLSS is down?" scenarios — the firmware design tolerates MLSS outages (buffer + replay) but currently you need SSH + journalctl to verify. Discovered as a real gap during the first physical deployment when the MLSS server's SD card failed mid-deployment and the operator had no quick way to verify the Pi was still capturing.
- ~~Plant journal / annotations on the History tab~~ — **shipped:** new `grow_journal_entries` table, `mlss_monitor/routes/api_grow_journal.py` (CRUD + RBAC + author-or-admin gate), `static/js/grow/components/journal-editor.mjs` mounted in `history-panel.mjs`. Composer hidden for viewers, edit/delete only on author's own rows (admin override). 27 backend tests, 15 frontend tests. **Marker overlay on the moisture chart + photo-timelapse scrubber deferred to v2** — the editor's `journal-changed` CustomEvent is in place so the orchestrator can re-fetch when the overlay lands.
- ~~Time-lapse video generation~~ — **shipped:** new `grow_timelapse_jobs` table + `mlss_monitor/grow/timelapse_jobs.py` (in-process daemon-thread runner, 30s poll), `mlss_monitor/routes/api_grow_timelapse.py` (POST controller+, GET viewer+), `static/js/grow/components/timelapse-generator.mjs` mounted in History tab. ffmpeg shells out via `subprocess.run` with a sequential `frame_%04d.jpg` symlink staging dir; missing-ffmpeg returns 503 with install hint. 18 backend + 8 frontend tests. README prereq updated to mention `apt install ffmpeg`.

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

## Off-Pi backup pipeline (`mlss_monitor/backup/`)

Asynchronous replication of canonical ML data from the hub's SQLite to
a home Postgres + S3 backend, so SD-card failure / fire / theft of the
hub doesn't lose years of training data. Designed as nine phases; the
first four landed on this branch.

### Partially shipped — Phases 1-4 of 9

- ~~**Phase 1 — outbox storage + lint guard.**~~ Four pointer tables
  added to the hub's SQLite (`outbox_changes`, `outbox_blobs`,
  `outbox_delete_scope`, `bootstrap_progress` — see
  [DATABASE.md](DATABASE.md#backup-outbox-tables)). Outbox storage
  helpers in `mlss_monitor/backup/outbox.py` (`enqueue_row`,
  `enqueue_blob`, `enqueue_delete_scope`, `peek_*`, `delete_*`,
  `pending_count_*`). Lint test
  `tests/test_no_direct_writes_to_replicated_tables.py` enforces every
  write to a replicated table goes through an allowlisted helper, so
  the outbox enqueue can't be bypassed.
- ~~**Phase 2 — `@tee_to_outbox` decorator + writer refactor.**~~
  Decorator wraps every replicated-table writer (`db_logger`,
  `grow/handlers`, `incident_grouper.regroup_all`, grow API route
  handlers, time-lapse jobs, inference evidence storage, …) so the
  outbox pointer commits in the same transaction as the live row.
  Strict-mirror tables (`incidents`, `incident_alerts`,
  `incident_signature_features`, `grow_light_windows`,
  `grow_unit_capabilities`) use the `outbox_delete_scope` path so
  wholesale-rebuild call sites replicate correctly.
- ~~**Phase 3 — Postgres + S3 clients + config.**~~ `PostgresClient`
  (`test_connection`, `upsert_rows` with `INSERT…ON CONFLICT`,
  `delete_scope`, `run_ddl`), `S3Client` (`test_connection`, `head`,
  `put`, `make_bucket` — boto3 wrapper, S3-compatible: MinIO / AWS /
  Cloudflare R2 / etc.), and a config module that stores settings in
  `app_settings` under the `backup.*` prefix with password masking on
  read and `get_secret` for the worker.
- ~~**Phase 4 — `BackupWorker` daemon thread.**~~ State machine
  (`DISABLED / IDLE / DRAINING / BACKOFF / PAUSED`), DB + files drain
  loops, exponential backoff (1 s → 600 s cap, resets on success),
  hot-reload via `EventBus` subscription so the admin saving new config
  wakes the worker without a restart, and status emission via
  `EventBus.publish` after every meaningful state change for the
  future admin SSE-driven status panel. Two instances run in parallel
  (`pipeline='db'` and `pipeline='files'`) with independent state +
  backoff so a Postgres outage doesn't block S3 shipping.

### Pending — Phases 5-9

- **Phase 5 — historical bootstrap.** One-shot scan that walks every
  existing row in the replicated tables and enqueues a pointer so a
  clean Postgres can be back-filled from the live DB. The
  `bootstrap_progress` table is already in the schema; no producer or
  consumer yet.
- **Phase 6 — admin REST API.** `GET/POST /api/admin/backup/config`,
  `GET /api/admin/backup/status`, `POST /api/admin/backup/test-connections`,
  `POST /api/admin/backup/pause` / `resume`.
- **Phase 7 — admin UI.** Settings → Backup page wiring the above
  endpoints, with the SSE status panel listening for the
  `backup_status_changed` event the worker already publishes.
- **Phase 8 — wire `BackupWorker` into `app.py` startup.** The worker
  is feature-complete but no code path constructs and starts it yet.
  This phase also ships the operator-facing `docs/BACKUP.md`.
- **Phase 9 — E2E smoke + operator runbook.** End-to-end test against
  a containerised Postgres + MinIO; document the bring-up procedure
  and the disaster-recovery restore path.

Until Phase 8 lands, the outbox tables fill up on every write but
never drain — the disk cost is pointer rows only (~50 bytes each) and
the lint guard keeps the design coherent in the meantime.

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
**Spec + plan:** branch worktree (not tracked in main).



---

## 🌱 Grow-unit onboarding — deferred polish

Follow-ups from the `feature/mlss-mobile` branch (the YAML-wizard +
CA-pin work shipped; these are nice-to-haves that didn't make the
cut).

### Auto-discovery via mDNS / Avahi

**Problem.** The "set IP, set key, done" YAML still requires the
operator to type the hub's LAN IP into `mlss_host`. On a fresh Pi
that hasn't had its DNS configured to resolve `mlss.local`, the IP
is the only working option — and IPs change when the router's lease
table rolls over.

**Proposal.** Publish `mlss.local` via Avahi on the hub (already
installed on Raspberry Pi OS by default). Set the hostname via
`raspi-config nonint do_hostname mlss` in `scripts/setup_pi.sh` so
new hub installs broadcast it automatically. Then update
`mlss-grow.yaml.template` to default `mlss_host: mlss.local` and
fall back to a manual IP only if mDNS resolution fails on the grow
Pi.

**Catch.** Pi Zero W's mDNS resolver is occasionally flaky on
boot-time first-look (avahi-daemon races with the wifi link bring-up).
Worth measuring before deploying — if first-boot enrolments retry
through a transient mDNS miss the user-visible outcome is "the unit
takes 90s instead of 30s to appear", which is acceptable.

**Effort:** ~half-day. New rule for setup_pi.sh, yaml template change,
docs update in `docs/PLANT_GROW_UNIT_SETUP.md`.

### Fleet-view "trust anchor" badge

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
