# Plant Grow Unit — Architecture deep-dive

Audience: developers working on the Plant Grow Unit code (server, firmware,
or browser). For the original design intent and trade-offs, see the spec:
[`docs/superpowers/specs/2026-05-03-plant-grow-unit-system-design.md`](superpowers/specs/2026-05-03-plant-grow-unit-system-design.md).

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

Local config is persisted at `/var/lib/mlss-grow/config.json` and the safety loop runs from it whether or not MLSS is reachable. PIDState (last pulse, integral, last error) is persisted to `/var/lib/mlss-grow/watering_state.json` so a service restart doesn't reset accumulated history.

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
