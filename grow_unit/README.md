# mlss-grow

Firmware for a Plant Grow Unit running on a Raspberry Pi Zero W with the
Pimoroni Automation pHAT. Talks to the MLSS server over a single
authenticated WebSocket per unit.

This package is built into a wheel by `scripts/build_grow_wheel.sh` and
served from the MLSS HTTP server at `/api/grow/dist/` for installation
on Pi Zeros via [`install.sh`](install.sh) — which also pins the MLSS
server cert at `/etc/mlss/server.crt` (TOFU) and verifies SHA256 hashes
of every downloaded artifact (wheels + the systemd unit file).

## Module layout

| Module | Purpose |
|---|---|
| `service.py` | Boot + main loop wiring; reads `/etc/mlss/grow.toml` (post-enrol) and `/boot/mlss-grow.yaml` (pre-enrol fallback) |
| `config.py` | Pydantic models for the in-memory `LoopConfig` + `UnitConfig` — single source of truth for what the safety loop reads |
| `enrol.py` | First-boot enrolment — POSTs `/api/grow/enroll` with the household key, persists per-unit bearer token to `/etc/mlss/grow.token` (mode 0600), deletes the YAML |
| `ws_client.py` | Persistent authenticated WebSocket; routes commands to `dispatch`; handles buffer replay + reconnect-time prune + config pull |
| `ws_protocol.py` | Frame encode/decode against `mlss_contracts.ws_messages` |
| `dispatch.py` | Switchboard for inbound `command` frames — routes `identify`, `water_now`, `snap_photo`, `config_changed`, `safety_override` to the right handler |
| `config_sync.py` | `pull_unit_config()` GET against `/api/grow/units/<id>/config` + `apply_config()` mutating PID + light schedule in-place; called on every `config_changed` push and every reconnect |
| `safety_override.py` | Direct actuator drive (`force_pump_on/off`, `force_light_on/off`, `skip_next_soak`) with non-blocking `threading.Timer` for `duration_s` auto-flip-off |
| `safety_loop.py` | 30-s tick: read sensors, run PID, execute pulses, persist state, emit telemetry |
| `pid.py` | Pure decision function — `(moisture_pct, config, state) → Decision` |
| `light_schedule.py` | Multi-window time-of-day evaluator (uses `grow_light_windows`) |
| `light_budget.py` | Tracks DLI/cumulative light hours per phase against the schedule for advisory output |
| `buffer.py` | SQLite outbox at `/var/lib/mlss-grow/buffer.sqlite` with three-layer disk-bounding: per-row delete on send, age-based prune on reconnect, hard size caps with FIFO eviction + `on_eviction` callback |
| `photo_buffer.py` | Filesystem-backed offline buffer for JPEGs at `/var/lib/mlss-grow/photos/` (commit `7b24c15`); 1 GB byte cap + 7-day age prune; uploaded oldest-first on reconnect |
| `camera.py` | picamera2 wrapper with JPEG compression knobs |
| `state_persistence.py` | Last-known config (`config.json`) + PIDState (`watering_state.json`) read/write with atomic-rename safety |
| `sensors/` | Sensor ABC + Seesaw soil-moisture driver; auto-detect at boot |
| `actuators/` | Actuator ABC + Automation pHAT relay/output drivers |

## Files written on the Pi

| Path | Purpose |
|---|---|
| `/etc/mlss/grow.token` | Per-unit bearer token (argon2-hashed server-side as `grow_units.bearer_token_hash`); mode 0600 |
| `/etc/mlss/server.crt` | Pinned MLSS cert from install-time TOFU |
| `/var/lib/mlss-grow/buffer.sqlite` | Local WS outbox |
| `/var/lib/mlss-grow/config.json` | Last-known config, used by safety loop when MLSS is unreachable |
| `/var/lib/mlss-grow/watering_state.json` | Persisted PIDState (last pulse, integral, last error) so service restarts don't reset accumulated history |

## Install (dev, on a non-Pi machine — Pi-only deps are skipped via markers)

```
poetry install
```

## See also

- [docs/PLANT_GROW_UNIT_ARCHITECTURE.md](../docs/PLANT_GROW_UNIT_ARCHITECTURE.md)
- [docs/PLANT_GROW_UNIT_SETUP.md](../docs/PLANT_GROW_UNIT_SETUP.md)
- [docs/DATABASE.md](../docs/DATABASE.md) — schema for both server + buffer DBs
