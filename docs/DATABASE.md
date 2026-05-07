# Database — mars-air-quility

> Source of truth for all schema. If a doc elsewhere contradicts this file,
> trust this file and submit a PR fixing the other doc.

[Back to main README](../readme.md)

---

## Overview

The project uses **two SQLite databases**:

| Database | Location | Purpose |
|---|---|---|
| Main MLSS DB | `data/sensor_data.db` (configurable via `MLSS_DB_FILE`) | Air quality sensor data, grow unit fleet state, settings, inferences, incidents |
| Grow unit buffer | `/var/lib/mlss-grow/buffer.sqlite` (per-unit, on each Pi Zero) | Telemetry/events buffered locally when MLSS is unreachable |

Both use SQLite's WAL journal mode for concurrent read+write durability.
The main DB is created/migrated by [`database/init_db.py`](../database/init_db.py)
which calls [`database/grow_schema.py::create_grow_schema`](../database/grow_schema.py)
for all `grow_*` tables. The grow buffer is created/managed by
[`grow_unit/src/mlss_grow/buffer.py`](../grow_unit/src/mlss_grow/buffer.py).

---

## Schema policy

- **Idempotent CREATE.** All table creation uses `CREATE TABLE IF NOT EXISTS`
  so fresh installs and re-running `init_db.py` on an existing DB use the
  same code path. `_seed_*` helpers gate inserts on `SELECT COUNT(*)` so
  seed data is added once and never duplicated.
- **Additive migrations only.** New columns are added via `ALTER TABLE
  ADD COLUMN` inside a `try/except: pass` loop in `init_db.py`. Never drop
  a column without a deprecation cycle (one release where the column is
  unread in code, then a `DROP COLUMN` migration in the following
  release). The two existing drops (`grow_units.last_known_state_json`
  and `grow_units.light_phase_override_json`) followed exactly that
  pattern in the C1 batch — see [JSON_STORAGE_AUDIT.md](JSON_STORAGE_AUDIT.md).
- **CHECK constraints are best-effort.** SQLite cannot add a `CHECK`
  constraint via `ALTER TABLE`, so freshly-created tables get the full
  constraint set but existing tables that gain a column via migration
  do not. Pydantic at the WS/HTTP boundary is the primary enforcement
  layer; the column simply receives the validated string.
- **Indexes are explicit.** Every `(unit_id, timestamp_utc DESC)` query
  pattern has a matching index; same for `(unit_id, channel)` lookups.
  Indexes use `CREATE INDEX IF NOT EXISTS` so they're idempotent too.

---

## Main MLSS database

`data/sensor_data.db` — single file, WAL mode, ~25 tables. The path is
controlled by `MLSS_DB_FILE` (env var, default `data/sensor_data.db`).

### Air quality tables

Defined in [`database/init_db.py`](../database/init_db.py). The big-picture
ER diagram lives in the main [`readme.md`](../readme.md#database-design); this
section is a one-line-per-table summary.

| Table | Purpose | Retention |
|---|---|---|
| `sensor_data` | One row per polling cycle. Channels: temp, humidity, eco2, tvoc, pm1.0, pm2.5, pm10, gas_co, gas_no2, gas_nh3, fan_power_w, vpd_kpa. Annotatable. | Indefinite. |
| `fan_settings` | Single-row snapshot of fan auto-mode thresholds. | Permanent config. |
| `app_settings` | Key/value store for misc settings (location, energy rate, grow defaults, enrollment-key hash). Never holds runtime-mutable structured state — keep it scalar. | Permanent config. |
| `weather_log` | One row per hourly Open-Meteo fetch. | Auto-purged after 7 days. |
| `inferences` | Detector output rows. `evidence` is JSON-in-TEXT (heterogeneous diagnostic context) — see [JSON storage](#json-storage). | Indefinite. |
| `inference_thresholds` | Per-key default + optional user override. | Permanent config. |
| `event_tags` | User-applied source labels on inferences. | Indefinite. |
| `users` | Authorised GitHub users + roles (admin/controller/viewer). | Soft-delete via `is_active=0`. |
| `login_log` | Append-only login audit. | Indefinite. |
| `incidents` | Sessionised inference clusters (30-min silence gap). `signature` is a 32-float JSON vector — see [JSON storage](#json-storage). | Follows `inferences`. |
| `incident_alerts` | M:N inferences ↔ incidents with `is_primary` flag. | Rebuilt on regroup. |
| `alert_signal_deps` | Per-sensor Pearson r + lag for each alert. | Rebuilt on regroup. |
| `incident_splits` | Operator markers that veto a merge. | Indefinite, manual. |
| `hot_tier` | 1-second-resolution rolling buffer (~2 h). | Auto-trimmed by row count. |

### Plant Grow Unit tables

Defined in [`database/grow_schema.py`](../database/grow_schema.py). Created
in the same transaction as the air-quality schema by `create_db()`'s call
to `create_grow_schema(cur)`.

#### `grow_units` — one row per enrolled Pi

Per-unit identity, current phase, plant + medium type, calibration
points, and per-unit override columns for every PID/light/buffer
tunable. The full cascade for tunable lookups is:

```
grow_units.<field>_override
  → grow_plant_profiles.<field>          (resolved per (plant_type, phase))
    → app_settings.grow_default_<field>  (household default)
      → built-in firmware default
```

Notable runtime-mutable columns (updated by handlers, not migrations):

- `current_phase` — set by user (UI) or `image_classifier` (future Phase 4)
- `phase_set_by`, `phase_set_at` — provenance for the current phase
- `last_seen_at` — touched by every WS keepalive
- `last_telemetry_at` — touched by every telemetry frame
- `bearer_token_hash` — argon2 hash, rotated on re-enrolment of a known
  `hardware_serial`

Index: `idx_grow_units_active(is_active, last_seen_at DESC)` — drives
the dashboard list-active-units query.

#### `grow_unit_capabilities` — channel-by-channel hardware inventory

PRIMARY KEY `(unit_id, channel)`. One row per
sensor/actuator the unit detected at boot. Runtime-mutable columns:

- `health` — `connected | untested | unresponsive | no_hardware`. Driven
  by [`mlss_monitor/grow/health_watchdog.py`](../mlss_monitor/grow/health_watchdog.py)
  and the WS handler in [`mlss_monitor/grow/handlers.py`](../mlss_monitor/grow/handlers.py).
  Promoted from JSON to a typed column in Phase 2 C1 — see
  [JSON_STORAGE_AUDIT.md](JSON_STORAGE_AUDIT.md#already-cleaned-phase-2-c1-batch).
- `last_seen_at` — most recent evidence the channel is alive (telemetry
  for sensors, watering_event/light_state=1 telemetry for actuators).

The CHECK constraint on `health` applies on freshly-created tables only;
existing tables migrated in via `ALTER TABLE` get the column without the
constraint, with pydantic at the WS boundary as the enforcement layer.

Index: `idx_grow_caps_unit_health(unit_id, health)` — drives the
"any unhealthy capability?" badge on the dashboard cards.

The `details_json` column is reserved for legitimately heterogeneous
metadata per channel (e.g. I2C address, calibration coefficients). It
is currently unused after the C1 cleanup — kept for forward
compatibility.

#### `grow_telemetry` — wide time-series table

One row per 30-second safety-loop tick (configurable). Columns:
`soil_moisture_raw` (NOT NULL), `soil_moisture_pct`, `light_state`
(NOT NULL), `pump_state` (NOT NULL), `soil_temp_c`, `ambient_lux`,
`air_temp_c`, `air_humidity_pct`, `reservoir_level_pct`. NULL means the
sensor is not present on that unit — adding a new channel is one
`ALTER TABLE ADD COLUMN` plus a frame field.

Index: `idx_grow_telemetry_unit_time(unit_id, timestamp_utc DESC)` —
drives the History tab range queries and the photo→telemetry join.

#### `grow_watering_events` — pump pulse audit trail

One row per pump activation. `trigger ∈ {pid, manual, identify_test}`.
Records the PID decision components (`pid_p_term`, `pid_i_term`,
`pid_d_term`, `pid_error`) for diagnostics. `soil_pct_after_5min` is
filled in lazily from telemetry by a follow-up query.

Index: `idx_grow_watering_unit_time(unit_id, timestamp_utc DESC)`.

#### `grow_photos` — image metadata + ML training join key

One row per JPEG written under `MLSS_GROW_IMAGES_DIR`. The `file_path`
is **relative** so the storage disk can be swapped with `rsync` + an
env var change. UNIQUE constraint `(unit_id, taken_at)` makes ingest
idempotent.

`telemetry_id` is the foreign key into `grow_telemetry` for the closest
reading within ±60s of capture — set at ingest time by the WS listener
([`mlss_monitor/grow/photo_storage.py`](../mlss_monitor/grow/photo_storage.py)).
ML training queries become a simple JOIN, no fuzzy time-window matching
at training time. See [PLANT_GROW_UNIT_ARCHITECTURE.md](PLANT_GROW_UNIT_ARCHITECTURE.md#image-storage--ml-join-key).

`classified_phase` / `classifier_confidence` / `classified_at` are
reserved for the future Phase 4 image classifier; NULL for all rows
today.

Indexes:
- `idx_grow_photos_unit_time(unit_id, taken_at DESC)` — History tab timelapse
- `idx_grow_photos_telemetry(telemetry_id)` — ML join

#### `grow_plant_profiles` — shipped + custom defaults per (plant_type, phase)

UNIQUE `(plant_type, phase)`. `is_shipped=1` rows are seeded at
DB creation (see `_SHIPPED_PROFILES` in `grow_schema.py`); custom
profiles get `is_shipped=0` and are editable in the Settings → Grow
plant profile editor.

Each row holds a complete tunable bundle: `target_moisture_pct`,
`deadband_pct`, `kp/ki/kd`, `min_pulse_s/max_pulse_s`,
`soak_window_min`, `default_light_hours`. The cascade above resolves
these to firmware-ready numbers before sending to the unit.

#### `grow_light_windows` — per-(unit, phase) light schedule overrides

Multiple rows per (unit, phase) define the union of "light on" windows
in `HH:MM` local time. `sort_order` is the row's display order in the
Configure tab editor. NULL = use the phase's default from
`grow_plant_profiles.default_light_hours`.

Index: `idx_glw_unit_phase(unit_id, phase)`.

#### `grow_medium_defaults` — calibration constants per medium type

PRIMARY KEY `medium_type ∈ {soil, coco, rockwool, custom}`. Holds
ship-defaults for `dry_raw` / `wet_raw` Seesaw values per medium —
seeded once at DB creation. A unit's per-row `soil_dry_raw` /
`soil_wet_raw` overrides these when calibration is run via the Configure
tab.

#### `grow_errors` — operational error log with audit trail

One row per error/warning the firmware or server emits about a unit:
sensor unresponsive, buffer eviction, safety override fired, watering
cap hit, etc. `kind` is a free-form string (see `mlss_contracts/enums.py`
for the well-known set); `details_json` is **legitimately heterogeneous**
per error kind — see [JSON storage](#json-storage). `subject_sensor`
is populated for `sensor_*` kinds, NULL otherwise. `resolved_at` is
NULL until the operator (or auto-recovery in handlers.py) marks the
error fixed.

Indexes:
- `idx_grow_errors_unit_time(unit_id, timestamp_utc DESC)` — error feed
- `idx_grow_errors_unresolved(resolved_at) WHERE resolved_at IS NULL` —
  partial index, fast "unresolved badge count" query
- `idx_grow_errors_recovery(unit_id, kind, subject_sensor, resolved_at)` —
  drives the auto-recovery probe in handlers

### JSON storage

Three columns intentionally store JSON in TEXT. Each is justified — see
the full audit and roadmap in
[JSON_STORAGE_AUDIT.md](JSON_STORAGE_AUDIT.md).

| Column | Why JSON | Status |
|---|---|---|
| `grow_errors.details_json` | Genuinely heterogeneous per `kind` (sensor address, threshold values, eviction counts, …); written-once-read-as-blob | Legitimate |
| `grow_unit_capabilities.details_json` | Reserved for future per-channel heterogeneous metadata; currently unused | Legitimate (reserved) |
| `inferences.evidence` | Heterogeneous per `event_type`; readers consistently extract a handful of common fields | Refactor candidate (Phase 3+) — promote common fields to typed columns |
| `incidents.signature` | Fixed-shape 32-float vector; schema-versioned in code | Refactor candidate (Phase 3+) — promote to BLOB or sub-table |

`grow_unit_capabilities.health`, `grow_units.last_known_state_json`,
and `grow_units.light_phase_override_json` were **dropped** in Phase 2
C1 and replaced by typed columns / the `grow_light_windows` table.

---

## Grow unit buffer database

`/var/lib/mlss-grow/buffer.sqlite` — created and managed by
[`grow_unit/src/mlss_grow/buffer.py`](../grow_unit/src/mlss_grow/buffer.py)
on the Pi Zero, mode 0750 owned by the `mlss-grow` user. WAL mode.
**One table** — deliberately tiny.

### Schema

```sql
CREATE TABLE buffer (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  msg_type      TEXT NOT NULL,
  body          TEXT NOT NULL,
  timestamp_utc DATETIME NOT NULL
)
```

Each row holds a single text WS frame the firmware tried to send while
the link was down: telemetry, event, ack, or capabilities. Photos are
**not** buffered (saves SD-card writes; capture-time photos are dropped
if the WS is down).

### Housekeeping

Three layers of bound, in priority order:

1. **Per-row delete on send** during replay. The client peeks each row,
   sends it, and only deletes after the send acks. A mid-replay
   disconnect leaves the un-sent tail in place for the next attempt.
2. **Time-based prune on every reconnect**, driven by
   `grow_units.buffer_retention_days` (per-unit override) →
   `app_settings.grow_default_buffer_retention_days` (default 7) →
   firmware fallback (`_DEFAULT_BUFFER_RETENTION_DAYS=7`). Implemented
   in `LocalBuffer.prune(retention_days)`, called by the WS client
   from `_on_reconnect`.
3. **Hard size caps inside `LocalBuffer.append()`**, applied
   unconditionally even when prune never gets to run (misconfigured
   server URL, MLSS permanently down, cert pinning failure):
   - `_DEFAULT_MAX_ROWS = 100_000`
   - `_DEFAULT_MAX_BYTES = 50 * 1024 * 1024` (50 MB)
   - FIFO eviction — oldest rows dropped first (newer telemetry has
     more diagnostic value than week-old already-stale data)
   - Byte-cap is only checked every 100 inserts (the
     `SUM(LENGTH(body))` scan is O(rows))

### Eviction event

When the size caps fire, `LocalBuffer` invokes its
`on_eviction(reason, evicted_count)` callback. The WS client wires that
callback to emit a `buffer_eviction` event into `grow_errors` so the
operator sees "this unit dropped N rows because the server was
unreachable too long" rather than letting telemetry silently disappear.

The callback runs inside the buffer commit flow — exceptions raised by
the callback are caught and swallowed, so a buggy callback can never
break the buffer.

---

## Configuration

| Setting | Where | Default | Effect |
|---|---|---|---|
| `MLSS_DB_FILE` | env var (`config.py`) | `data/sensor_data.db` | Server DB path |
| `MLSS_GROW_IMAGES_DIR` | env var (`mlss_monitor/grow/photo_storage.py`) | `/var/lib/mlss/grow_images` | Where uploaded photos land on the MLSS Pi |
| `app_settings.grow_default_soak_window_min` | seeded by `grow_schema._seed_grow_data` | `30` | Default PID soak window (minutes) |
| `app_settings.grow_default_buffer_retention_days` | seeded by `grow_schema._seed_grow_data` | `7` | Default firmware buffer prune retention (days) |
| `app_settings.grow_disk_warn_pct` | seeded by `grow_schema._seed_grow_data` | `90` | Disk-usage threshold for the "MLSS storage almost full" alert |
| `app_settings.grow_holiday_mode` | seeded by `grow_schema._seed_grow_data` | `0` | Household-wide vacation flag — pumps suspended, light schedule continues |
| `app_settings.grow_images_dir` | seeded blank | `""` | DB-side override for `MLSS_GROW_IMAGES_DIR` (empty = use env var) |
| `app_settings.grow_enrollment_key_hash` | argon2-hashed by `_seed_grow_data` | random | Authorises `POST /api/grow/enroll`. Rotate via Settings → Grow. |
| `grow_units.buffer_retention_days` | per-unit override | NULL = use default | Per-unit buffer prune retention |
| `grow_units.soak_window_min_override` | per-unit override | NULL = use default | Per-unit PID soak window |

See [CONFIGURATION.md](CONFIGURATION.md) for the full env-var reference
on the air-quality side.

---

## See also

- [JSON_STORAGE_AUDIT.md](JSON_STORAGE_AUDIT.md) — current state of JSON-in-TEXT-column usage and the promote-to-typed-columns roadmap
- [PLANT_GROW_UNIT_ARCHITECTURE.md](PLANT_GROW_UNIT_ARCHITECTURE.md) — how the WS protocol, watchdog, and buffer use these tables
- [CONFIGURATION.md](CONFIGURATION.md) — env-var reference for MLSS server config
- Source: [`database/init_db.py`](../database/init_db.py),
  [`database/grow_schema.py`](../database/grow_schema.py),
  [`grow_unit/src/mlss_grow/buffer.py`](../grow_unit/src/mlss_grow/buffer.py)
