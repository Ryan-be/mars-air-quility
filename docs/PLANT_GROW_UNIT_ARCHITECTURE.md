# Plant Grow Unit — Architecture deep-dive

Audience: developers working on the Plant Grow Unit code (server, firmware,
or browser). For the original design intent and trade-offs, see the spec:
[`docs/superpowers/specs/2026-05-03-plant-grow-unit-system-design.md`](superpowers/specs/2026-05-03-plant-grow-unit-system-design.md).

> **Schema details** are in [DATABASE.md](DATABASE.md) (single source of
> truth for both `data/sensor_data.db` and the on-Pi `buffer.sqlite`).
> This doc summarises and links — it doesn't duplicate column lists.

---

## Repo structure

Single repo, three independently-installable Python packages:

```
mars-air-quility/
├── mlss_monitor/        # MLSS server (existing) + grow API endpoints + WS listener
├── grow_unit/           # mlss_grow firmware package (Pi Zero only)
├── contracts/           # mlss_contracts shared schemas (pydantic)
├── database/grow_schema.py
├── tests/
│   ├── grow_server/
│   ├── grow_unit/
│   └── contracts/
└── docs/
```

Each package has its own `pyproject.toml` and Poetry env. The MLSS server installs `mlss_contracts` as a path dep but **not** `mlss_grow`. The Pi Zero installs `mlss_grow` + `mlss_contracts` as wheels (built by `scripts/build_grow_wheel.sh`, served from MLSS at `/api/grow/dist/`). This guarantees the MLSS Pi never installs picamera2 / RPi.GPIO, and the Pi Zero never installs Flask / gunicorn.

---

## WebSocket protocol

One persistent WebSocket per unit, listening on MLSS port 5001:

```
wss://mlss.local:5001/api/grow/<unit_id>/ws
Authorization: Bearer <per-unit-token>
```

All traffic flows over this single connection:

| Direction | Frame | Payload |
|---|---|---|
| Unit → MLSS | text | `{type:"telemetry"\|"event"\|"capabilities"\|"ack", ts, payload}` |
| Unit → MLSS | binary | `[4 bytes BE header_len][JSON header][JPEG bytes]` |
| MLSS → Unit | text | `{type:"command"\|"config", ts, payload}` |

Schemas live in `contracts/src/mlss_contracts/ws_messages.py` — both server and firmware import the same pydantic classes, so a schema change is a single edit and any drift is a static error.

The server listener (`mlss_monitor/routes/api_grow_ws.py`) runs in its own asyncio loop on a background thread separate from Flask's request loop. Per-connection coroutines dispatch by message type to handlers in `mlss_monitor/grow/handlers.py` and `photo_storage.py`.

---

## Authentication

Two credentials:

- **Household enrollment key** — argon2-hashed in `app_settings.grow_enrollment_key_hash`. Used once at first-boot to mint the per-unit token. The raw key is shown once in the empty-state UI **to admin sessions only** (`peek_once` is gated by `require_role("admin")` because the key authorises the idempotent `POST /api/grow/enroll`, which lets any holder rotate any unit's bearer token by re-POSTing a known serial), then deleted from the DB.
- **Per-unit bearer token** — argon2-hashed in `grow_units.bearer_token_hash`. Stored on the unit at `/etc/mlss/grow.token` (mode 0600). Sent in `Authorization: Bearer ...` on every WS upgrade.

Tokens are revocable per-unit (`UPDATE grow_units SET is_active=0`). Rotating the household key doesn't invalidate existing tokens — it only blocks new enrollments with the old key.

---

## The Sensor and Actuator ABCs

Mirrors the MLSS server's existing `DataSource` ABC pattern. Adding a new sensor on a unit:

```python
# grow_unit/src/mlss_grow/sensors/my_new_sensor.py
class MyNewSensor(Sensor):
    @classmethod
    def detect(cls, i2c_bus):
        try:
            drv = MyDriver(i2c_bus, addr=0x42)
            return cls(driver=drv)
        except OSError:
            return None

    def channels(self):
        return ["my_channel"]

    def read(self):
        return {"my_channel": self._driver.read()}
```

Then add it to `REGISTERED_SENSORS` in `sensors/__init__.py`. On boot, `auto_detect()` calls `.detect()` on each registered class; surviving instances become the unit's capabilities and are pushed to MLSS on the WS handshake.

The dashboard renders one stat tile per declared capability — **the UI is data-driven**. Plug in a new sensor, restart the service, refresh the dashboard, the new tile appears with no MLSS deploy.

For the wide telemetry table to accept a new channel, add a column to `grow_telemetry` (one `ALTER TABLE ADD COLUMN` in `database/grow_schema.py`). NULL = sensor not present on this unit.

---

## Database schema

The grow tables live in the same SQLite DB as the air-quality side
(`data/sensor_data.db`) and are created in the same transaction by
[`database/grow_schema.py::create_grow_schema`](../database/grow_schema.py).
Per-unit identity + tunables in `grow_units`, channel inventory in
`grow_unit_capabilities`, time-series in `grow_telemetry`, audit trail
in `grow_watering_events` + `grow_errors`, image metadata in
`grow_photos`, and config tables (`grow_plant_profiles`,
`grow_light_windows`, `grow_medium_defaults`).

The on-device buffer is a separate single-table SQLite at
`/var/lib/mlss-grow/buffer.sqlite` managed by
[`grow_unit/src/mlss_grow/buffer.py`](../grow_unit/src/mlss_grow/buffer.py).

**For the full schema reference, see [DATABASE.md](DATABASE.md)** —
every column, every index, runtime-mutable fields, the
override-cascade for tunables, JSON-storage classification, and
retention policies.

---

## Capability health field

Each row in `grow_unit_capabilities` carries a typed `health` column
(`connected | untested | unresponsive | no_hardware`) that the dashboard
uses to grey out controls when an actuator isn't actually responding —
see the [sense-only mode UX](PLANT_GROW_UNIT_USAGE.md#sense-only-mode-greyed-out-actuator-buttons).

States and transitions:

| State | Set when | Reset when |
|---|---|---|
| `untested` | Capability inserted by enrolment / first capabilities frame | First evidence arrives |
| `connected` | Successful read (sensors) or successful actuation evidence (actuators) | Falls back to `unresponsive` after the watchdog timeout |
| `unresponsive` | Watchdog timeout fires after a command was sent without follow-up evidence | Next successful read / actuation evidence |
| `no_hardware` | Firmware reports the channel was probed-and-missing at boot | Re-detected on a future reboot |

The watchdog is intentionally **lazy** —
[`mlss_monitor/grow/health_watchdog.py`](../mlss_monitor/grow/health_watchdog.py)
holds a process-local dict `(unit_id, channel) → last_command_at` and
is consulted only when a `GET /api/grow/units/<id>` happens. There's
no background poller. Each command-pushing endpoint
(`water_now`, `light_toggle`) calls `record_command_sent` on a 202;
on the next GET the handler asks "did follow-up evidence arrive within
30 s?" and overrides the response's `health` to `unresponsive` if not
— the DB row stays unchanged so the next telemetry that proves the
actuator works quietly upgrades it back without a DB write.

The constant lives in `health_watchdog.DEFAULT_TIMEOUT_S = 30`. Below
30 s false-positives on slow units; above 30 s the user waits too long
to see the warning.

This is a single-process (one Flask + one gunicorn worker) design. If
the deployment grows to multiple workers, swap the module-level dict
for a Redis or DB-backed store — the function signatures don't change.

---

## PID watering

`grow_unit/src/mlss_grow/pid.py` is a pure function: given current moisture %, config, and state, returns a Decision (pulse_s + which terms contributed). The safety loop calls this on every 30s tick.

Default profiles (in `grow_plant_profiles`) ship with `Ki=Kd=0`, making this effectively a P-only controller with deadband + soak window:

```
IF (target - current) > deadband AND (now - last_pulse) > soak_window:
    pulse_s = clip(Kp * error, min_pulse, max_pulse)
```

Per-unit overrides cascade `grow_units.<field>_override → grow_plant_profiles.<field> → app_settings.grow_default_<field> → built-in default`.

---

## The soak window

Defends against "water hasn't reached sensor yet → fire another pulse." Default 30 min. Enforced **on the unit** even if MLSS sends a manual water-now command — the firmware refuses commands within the soak window. The dashboard's Water-now button is also disabled within the soak window so user expectations match.

The hard 30s pump pulse cap is enforced unconditionally in `Actuator.pulse()` regardless of any commanded duration.

---

## Buffer + replay

When the WS is down, telemetry text frames go to `/var/lib/mlss-grow/buffer.sqlite` instead of being sent. On reconnect, the client emits `event: buffer_replay_started`, sends every buffered row in original timestamp order, then `event: buffer_replay_complete`. Photos are **not** buffered (to save SD card writes) — they're dropped if the WS is down at capture time.

The replay loop **peeks** each row, sends it, and only deletes after
the send acks. A mid-replay disconnect leaves the un-sent tail in
place for the next attempt. (The earlier `pop_all` flow that deleted
everything up front silently dropped rows when the socket died
mid-replay — see I2 fix in `buffer.py`.)

Local config is persisted at `/var/lib/mlss-grow/config.json` and the safety loop runs from it whether or not MLSS is reachable. PIDState (last pulse, integral, last error) is persisted to `/var/lib/mlss-grow/watering_state.json` so a service restart doesn't reset accumulated history.

### Buffer housekeeping (C2)

Three layers bound disk usage so a permanently-down MLSS / misconfigured
server URL / cert-pinning failure can't fill the SD card:

1. **Per-row delete on send** during replay (above).
2. **Age-based prune on every reconnect.** `LocalBuffer.prune(retention_days)`
   is wired by `ws_client._on_reconnect` against the value provided by
   the server in the `config_changed` push: `grow_units.buffer_retention_days`
   → `app_settings.grow_default_buffer_retention_days` (default 7) →
   firmware fallback `_DEFAULT_BUFFER_RETENTION_DAYS=7`.
3. **Hard size caps inside `LocalBuffer.append()`**, applied
   unconditionally regardless of whether prune ever runs:
   `_DEFAULT_MAX_ROWS=100_000`, `_DEFAULT_MAX_BYTES=50 MB`. FIFO
   eviction — newer telemetry has more diagnostic value than week-old
   already-stale data. Byte cap is checked every 100 inserts (the
   `SUM(LENGTH(body))` scan is O(rows); row count is the cheap primary
   gate).

When the size caps fire, `LocalBuffer` invokes its
`on_eviction(reason, evicted_count)` callback. The WS client wires that
to emit a `buffer_eviction` event into `grow_errors` so the operator
sees the data loss explicitly rather than letting telemetry silently
disappear. Callback exceptions are caught and swallowed inside the
buffer commit flow — a buggy callback must not break the buffer.

---

## Config-on-reconnect-pull

The server pushes a `command_changed` notification when admin edits
land in `grow_units` / `grow_plant_profiles` / `grow_light_windows`,
but the firmware doesn't trust the push to carry the full config
state. Instead the dispatcher (`grow_unit/src/mlss_grow/dispatch.py`)
calls `pull_unit_config(unit_id)` which does an authenticated GET
against `/api/grow/units/<id>/config` and applies the response
in-place via `apply_config(unit_cfg, loop_cfg)`.

Why pull rather than rely on push payload:

- The server resolves null overrides against `grow_plant_profiles`
  **before** responding, so the firmware sees concrete numbers and
  never has to maintain its own profile table — smaller firmware,
  single source of truth on the server.
- A reconnect after long downtime can mean the firmware's
  in-memory `LoopConfig` is N edits stale; one pull is simpler and
  more robust than replaying N pushes.

The same `pull_unit_config` is also called by `ws_client._on_reconnect`
unconditionally on every reconnect — so an admin who edits config
while a unit is offline gets their changes applied as soon as the
unit reconnects, no service restart, no dashboard nudge required.

TLS posture matches `enrol.py` and `ws_client.py` — pinned cert from
`/etc/mlss/server.crt` when the file exists; falls back to `verify=False`
with a one-time `WARNING` log when it doesn't (dev/test before the
install pin step).

---

## Image storage + ML join key

Photos are stored as JPEG files at `MLSS_GROW_IMAGES_DIR/unit_NNN/YYYY-MM-DD/HHMMSS.jpg`. The path stored in `grow_photos.file_path` is **relative** so swapping storage disks is `rsync` + change env var.

At ingest time, the WS listener finds the closest `grow_telemetry` row for the same unit within ±60s and stores its `id` in `grow_photos.telemetry_id`. ML training queries become a simple JOIN — no fuzzy time-window matching needed at training time.

---

## Where to add code

| Want to... | Edit |
|---|---|
| Add a new sensor type | `grow_unit/src/mlss_grow/sensors/<new>.py` + add to `REGISTERED_SENSORS` + `ALTER TABLE grow_telemetry` |
| Change a WS message shape | `contracts/src/mlss_contracts/ws_messages.py` + update both consumer sites |
| Add a new dashboard tile | The capability auto-renders. To add a new computed metric, edit the renderer in `static/js/grow/unit_detail.mjs::renderLiveReadings`. |
| Add a server REST endpoint | New blueprint in `mlss_monitor/routes/api_grow_*.py` + register in `routes/__init__.py` |
| Add a new MLSS-side command | Add `CommandName` enum value in `contracts/enums.py`, server `_push_command_blocking()` call, firmware command handler in `service.py` |

---

## Testing

- Server: `pytest tests/grow_server/`
- Firmware: `cd grow_unit && pytest ../tests/grow_unit/`
- Contracts: `cd contracts && pytest ../tests/contracts/`
- JS components: `node --test tests/js/`

CI runs all four. Pi-only deps (RPi.GPIO, picamera2, adafruit-circuitpython-seesaw) are marked optional in `grow_unit/pyproject.toml` so dev laptops can install + test.
