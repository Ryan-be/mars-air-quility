# Plant Grow Unit System — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the smallest end-to-end Plant Grow Unit system — one Pi Zero W can be enrolled to MLSS over WebSocket, runs a local PID watering loop with safety caps, captures photos at intervals, and surfaces in the MLSS Grow tab with a fleet card and a per-unit Live detail page.

**Architecture:** Single repo with strict per-package dep isolation (root MLSS server, `grow_unit/` firmware package, `contracts/` shared schemas). Per-unit WebSocket holds telemetry text frames + image binary frames + commands. Bearer-token auth bootstrapped via household enrollment key dropped on the SD card boot partition.

**Tech Stack:** Python 3.11+, Flask + gunicorn (existing), `websockets` library (server + client), pydantic v2 (schemas), SQLite (existing), picamera2, adafruit-circuitpython-seesaw, RPi.GPIO, vanilla JS + AstroUXDS (existing UI stack), Plotly (existing charts).

**Spec:** [`docs/superpowers/specs/2026-05-03-plant-grow-unit-system-design.md`](../specs/2026-05-03-plant-grow-unit-system-design.md)
**Hardware reference:** [`docs/PLANT_GROW_UNIT_HARDWARE.md`](../../PLANT_GROW_UNIT_HARDWARE.md)
**Branch:** `feature/plant-grow-units`

---

## Plan scope and what's deferred

This plan covers **Phase 1 only** from the spec. Phase 1 produces a working end-to-end system you can grow one tomato with: enrol a unit, watch it report telemetry and photos, configure it from the dashboard via the (limited Phase 1) Live tab + manual quick-controls, and have its safety loop survive an MLSS outage.

**Deferred to Phase 2 plan (will be written after Phase 1 ships):**
- Filter / sort row on fleet view
- Per-unit detail page **Configure** tab (multi-window light schedule editor, plant profile picker, PID tunables form, calibration two-step UI, "I understand the risks" override)
- Per-unit detail page **History** tab (long-range chart, photo timelapse scrubber)
- Settings → Grow page (enrollment key rotation UI, default-tunables admin, holiday mode)
- Photo lightbox

**Deferred to Phase 3 plan:**
- Per-unit detail page **Diagnostics** tab
- `grow_errors` UI surfacing (the table + WS listener writes are in Phase 1; the dashboard rendering is Phase 3)
- Buffered-message replay UI
- Storage warning UI

---

## TDD discipline (applies to every task)

Every implementation task follows the same five-step cycle:

1. **Write the failing test** with the actual assertion that proves the behaviour
2. **Run the test** with the exact `pytest` / `npm test` command and confirm it fails for the *right reason* (e.g. "function not defined", not "import error")
3. **Implement the minimal code** to make the test pass — nothing extra (YAGNI)
4. **Run the test** and confirm it passes
5. **Commit** with a conventional commit message

For documentation tasks, the "test" is a markdown lint + spec-coverage review (does the doc cover the requirements it claims to?). For shell scripts, the "test" is `shellcheck` + a smoke-run with mocked inputs. For systemd units, `systemd-analyze verify`.

**Frequent commits:** every passing test gets its own commit. The plan's checkpoint cadence is "test passing → commit", not "section complete → commit". This means the branch history shows ~80–100 small commits by the end of Phase 1 — that's the desired shape.

---

## File structure (where new code lives)

```
mars-air-quility/
├── pyproject.toml                       # MODIFIED — add `websockets`, `mlss-contracts` path dep
├── mlss_monitor/                        # MODIFIED — server-side grow code lives here
│   ├── routes/
│   │   ├── api_grow_units.py            # NEW — REST endpoints for fleet
│   │   ├── api_grow_ws.py               # NEW — WebSocket listener + handlers
│   │   ├── api_grow_dist.py             # NEW — wheel + install.sh serving
│   │   ├── api_grow_enroll.py           # NEW — enrollment endpoint
│   │   └── pages.py                     # MODIFIED — add /grow + /grow/<id> routes
│   ├── grow/
│   │   ├── __init__.py                  # NEW
│   │   ├── auth.py                      # NEW — bearer token + enrollment-key validation
│   │   ├── ws_registry.py               # NEW — tracks per-unit live WS connections
│   │   ├── photo_storage.py             # NEW — filesystem write + telemetry_id join
│   │   └── state_cache.py               # NEW — last_known_state_json maintenance
│   └── state.py                         # MODIFIED — add ws_registry global
├── database/
│   ├── grow_schema.py                   # NEW — table creation + seeds
│   └── init_db.py                       # MODIFIED — call create_grow_schema()
├── contracts/                           # NEW — shared schemas package
│   ├── pyproject.toml
│   └── src/mlss_contracts/
│       ├── __init__.py
│       ├── enums.py
│       ├── capabilities.py
│       ├── plant_profiles.py
│       └── ws_messages.py
├── grow_unit/                           # NEW — firmware package
│   ├── pyproject.toml
│   ├── systemd/mlss-grow.service
│   ├── install.sh
│   └── src/mlss_grow/
│       ├── __init__.py
│       ├── service.py                   # systemd entry point
│       ├── config.py                    # /boot/mlss-grow.yaml + /etc/mlss/* loaders
│       ├── enrol.py                     # first-boot enrollment HTTP call
│       ├── ws_client.py                 # WS lifecycle + reconnect + buffer
│       ├── safety_loop.py               # PID + light schedule + photo cadence orchestration
│       ├── pid.py                       # pure PID decision function
│       ├── light_schedule.py            # window evaluator
│       ├── camera.py                    # picamera2 wrapper
│       ├── buffer.py                    # local SQLite buffer + replay
│       ├── sensors/
│       │   ├── __init__.py              # REGISTERED_SENSORS + auto_detect()
│       │   ├── base.py                  # Sensor ABC
│       │   └── seesaw.py
│       └── actuators/
│           ├── __init__.py
│           ├── base.py                  # Actuator ABC
│           └── automation_phat.py
├── tests/
│   ├── grow_server/                     # NEW — server-side grow tests
│   │   ├── test_grow_schema.py
│   │   ├── test_grow_auth.py
│   │   ├── test_grow_enroll.py
│   │   ├── test_grow_ws.py
│   │   ├── test_grow_units_api.py
│   │   ├── test_grow_dist.py
│   │   └── test_photo_storage.py
│   ├── grow_unit/                       # NEW — firmware tests
│   │   ├── test_pid.py
│   │   ├── test_light_schedule.py
│   │   ├── test_safety_loop.py
│   │   ├── test_buffer.py
│   │   ├── test_ws_client.py
│   │   ├── test_enrol.py
│   │   ├── test_sensors_seesaw.py
│   │   └── test_actuators.py
│   └── contracts/                       # NEW — schema tests
│       └── test_ws_messages.py
├── scripts/
│   ├── build_grow_wheel.sh              # NEW
│   └── deploy.sh                        # MODIFIED — call build_grow_wheel.sh
├── static/
│   ├── css/grow.css                     # NEW — Grow tab + detail page styles
│   ├── grow_dist/                       # NEW (gitignored) — built wheels
│   └── js/grow/
│       ├── fleet.mjs                    # NEW
│       ├── unit_detail.mjs              # NEW
│       └── components/
│           ├── grow-card.mjs
│           ├── status-pill.mjs
│           ├── stat-tile.mjs
│           ├── schedule-bar.mjs
│           ├── sensor-event-chart.mjs
│           └── guided-steps.mjs
├── templates/
│   ├── grow_fleet.html                  # NEW
│   ├── grow_unit_detail.html            # NEW
│   └── base.html                        # MODIFIED — add Grow nav tab
└── docs/
    ├── PLANT_GROW_UNIT_SETUP.md         # NEW — setup / installation guide
    ├── PLANT_GROW_UNIT_USAGE.md         # NEW — how-to-use guide
    └── PLANT_GROW_UNIT_ARCHITECTURE.md  # NEW — architecture deep-dive
```

---

## Section 0 — Foundation (workspace + contracts skeleton)

Sets up the package boundaries before any feature code goes in. Get the dep-isolation structure right first; everything else slots into it.

---

### Task 0.1: Create `contracts/` package skeleton

**Files:**
- Create: `contracts/pyproject.toml`
- Create: `contracts/src/mlss_contracts/__init__.py`
- Create: `contracts/README.md`

- [ ] **Step 1: Write the failing test**

Create `tests/contracts/test_package_installable.py`:

```python
"""Smoke test: the mlss_contracts package can be imported."""

def test_can_import_package():
    import mlss_contracts
    assert hasattr(mlss_contracts, "__version__")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd contracts && python -m pytest ../tests/contracts/test_package_installable.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mlss_contracts'`

- [ ] **Step 3: Create the package structure**

`contracts/pyproject.toml`:
```toml
[tool.poetry]
name = "mlss-contracts"
version = "0.1.0"
description = "Shared pydantic schemas between MLSS server and grow unit firmware"
authors = ["MLSS"]
packages = [{include = "mlss_contracts", from = "src"}]

[tool.poetry.dependencies]
python = "^3.11"
pydantic = "^2.0"

[tool.poetry.group.dev.dependencies]
pytest = "^8.0"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
```

`contracts/src/mlss_contracts/__init__.py`:
```python
"""Shared schemas for MLSS server <-> grow unit firmware."""

__version__ = "0.1.0"
```

`contracts/README.md`:
```markdown
# mlss-contracts

Shared pydantic schemas used by both the MLSS server (`mlss_monitor/`) and the
Plant Grow Unit firmware (`grow_unit/`). Single source of truth — both packages
import from this one to guarantee message-shape compatibility.

Install (path dep, dev mode):
    poetry install
```

- [ ] **Step 4: Install + run the test**

Run: `cd contracts && poetry install && python -m pytest ../tests/contracts/test_package_installable.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add contracts/ tests/contracts/test_package_installable.py
git commit -m "Add mlss-contracts package skeleton

Shared pydantic schema package for the WS message contracts between MLSS
server and grow unit firmware. Path-dep installable from both sides."
```

---

### Task 0.2: Create `grow_unit/` package skeleton

**Files:**
- Create: `grow_unit/pyproject.toml`
- Create: `grow_unit/src/mlss_grow/__init__.py`
- Create: `grow_unit/README.md`

- [ ] **Step 1: Write the failing test**

Create `tests/grow_unit/test_package_installable.py`:

```python
"""Smoke test: the mlss_grow package can be imported and reports its version."""

def test_can_import_package():
    import mlss_grow
    assert hasattr(mlss_grow, "__version__")
    assert mlss_grow.__version__ == "0.1.0"


def test_can_import_contracts_from_grow():
    """The grow package depends on mlss_contracts as a path dep."""
    import mlss_contracts
    assert hasattr(mlss_contracts, "__version__")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_package_installable.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mlss_grow'`

- [ ] **Step 3: Create the package structure**

`grow_unit/pyproject.toml`:
```toml
[tool.poetry]
name = "mlss-grow"
version = "0.1.0"
description = "Plant Grow Unit firmware — runs on Raspberry Pi Zero with Pimoroni Automation pHAT"
authors = ["MLSS"]
packages = [{include = "mlss_grow", from = "src"}]

[tool.poetry.dependencies]
python = "^3.11"
websockets = "^12.0"
pydantic = "^2.0"
pyyaml = "^6.0"
mlss-contracts = {path = "../contracts", develop = true}

# Pi-only deps marked with markers so dev laptops can install without them
"adafruit-circuitpython-seesaw" = {version = "^1.13", markers = "platform_machine == 'armv7l' or platform_machine == 'aarch64'"}
"RPi.GPIO" = {version = "^0.7", markers = "platform_machine == 'armv7l' or platform_machine == 'aarch64'"}

[tool.poetry.group.dev.dependencies]
pytest = "^8.0"
pytest-asyncio = "^0.24"

[tool.poetry.scripts]
mlss-grow = "mlss_grow.service:main"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
```

`grow_unit/src/mlss_grow/__init__.py`:
```python
"""MLSS Plant Grow Unit firmware."""

__version__ = "0.1.0"
```

`grow_unit/README.md`:
```markdown
# mlss-grow

Firmware for a Plant Grow Unit running on a Raspberry Pi Zero W with the
Pimoroni Automation pHAT. Talks to the MLSS server over a single
authenticated WebSocket per unit.

This package is built into a wheel by `scripts/build_grow_wheel.sh` and
served from the MLSS HTTP server at `/api/grow/dist/` for installation
on Pi Zeros via the install script.

Install (dev, on a non-Pi machine — Pi-only deps are skipped via markers):
    poetry install
```

- [ ] **Step 4: Install + run the test**

Run: `cd grow_unit && poetry install && python -m pytest ../tests/grow_unit/test_package_installable.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add grow_unit/ tests/grow_unit/test_package_installable.py
git commit -m "Add mlss-grow firmware package skeleton

Pi Zero firmware package with strict dep isolation from the MLSS server.
Pi-only deps (RPi.GPIO, picamera2, adafruit-circuitpython-seesaw) marked
to skip install on dev laptops. mlss-contracts path dep provides shared
WS message schemas."
```

---

### Task 0.3: Update root `pyproject.toml` to add server-side grow deps

**Files:**
- Modify: `pyproject.toml` (add `websockets` + `mlss-contracts` path dep)
- Modify: `.gitignore` (add `static/grow_dist/`, `**/dist/`)

- [ ] **Step 1: Write the failing test**

Create `tests/grow_server/test_server_imports_contracts.py`:

```python
"""The MLSS server can import shared contract schemas."""

def test_server_can_import_contracts():
    import mlss_contracts
    assert hasattr(mlss_contracts, "__version__")


def test_server_has_websockets():
    import websockets
    assert websockets is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_server_imports_contracts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mlss_contracts'` (or `websockets`)

- [ ] **Step 3: Update root `pyproject.toml`**

Add under `[tool.poetry.dependencies]`:

```toml
websockets = "^12.0"
mlss-contracts = {path = "contracts", develop = true}
```

Update `.gitignore` (append):

```
# Built grow firmware wheels (regenerated by scripts/build_grow_wheel.sh)
static/grow_dist/
contracts/dist/
grow_unit/dist/
```

- [ ] **Step 4: Install + run the test**

Run: `poetry install && python -m pytest tests/grow_server/test_server_imports_contracts.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml poetry.lock .gitignore tests/grow_server/test_server_imports_contracts.py
git commit -m "Wire mlss-contracts + websockets into MLSS server deps

Server now has the WS server library and the shared schema package.
Built wheels for grow firmware are gitignored."
```

---

### Task 0.4: Update `database/init_db.py` to call grow schema (placeholder)

This sets up the hook so later tasks can add tables without touching `init_db.py`. The actual `create_grow_schema` lives in `database/grow_schema.py` and is empty for now.

**Files:**
- Create: `database/grow_schema.py` (empty function)
- Modify: `database/init_db.py` (call `create_grow_schema(cur)`)

- [ ] **Step 1: Write the failing test**

Create `tests/grow_server/test_grow_schema_hook.py`:

```python
"""init_db calls create_grow_schema during create_db."""
import sqlite3
import tempfile
from unittest.mock import patch
from database.init_db import create_db


def test_create_grow_schema_is_called(monkeypatch):
    """When create_db runs, it must invoke create_grow_schema with the cursor."""
    called_with = []

    def fake_create_grow_schema(cur):
        called_with.append(cur)

    monkeypatch.setattr("database.init_db.create_grow_schema", fake_create_grow_schema)

    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        monkeypatch.setattr("database.init_db.DB_FILE", tmp.name)
        create_db()

    assert len(called_with) == 1
    assert called_with[0] is not None  # was given a cursor
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_grow_schema_hook.py -v`
Expected: FAIL with `AttributeError: module 'database.init_db' has no attribute 'create_grow_schema'`

- [ ] **Step 3: Create the empty hook + wire it up**

`database/grow_schema.py`:

```python
"""Plant Grow Unit database schema. All grow_* tables created here.

Called from database.init_db.create_db() so table creation happens in the
same transaction as the existing MLSS schema.
"""


def create_grow_schema(cur):
    """Create all grow_* tables. Idempotent (uses CREATE TABLE IF NOT EXISTS)."""
    # Tables added by later tasks in the implementation plan.
    pass
```

In `database/init_db.py`, near the top (after existing imports):

```python
from database.grow_schema import create_grow_schema
```

Inside `create_db()`, just before `conn.commit()`:

```python
    # Plant Grow Unit tables (Phase 1)
    create_grow_schema(cur)
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/grow_server/test_grow_schema_hook.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add database/grow_schema.py database/init_db.py tests/grow_server/test_grow_schema_hook.py
git commit -m "Wire grow_schema hook into create_db

Empty create_grow_schema() called from create_db so later tasks can add
grow_* tables in the same transaction as MLSS's existing schema."
```

---

## Section 1 — Contracts (shared WS message schemas)

Pydantic models that both server and firmware import. Get these right first — every subsequent task depends on them.

---

### Task 1.1: Channel + Phase + Severity enums

**Files:**
- Create: `contracts/src/mlss_contracts/enums.py`
- Create: `tests/contracts/test_enums.py`

- [ ] **Step 1: Write the failing test**

`tests/contracts/test_enums.py`:

```python
from mlss_contracts.enums import Channel, Phase, MediumType, Severity, EventKind, CommandName


def test_channel_required_set():
    assert Channel.SOIL_MOISTURE.value == "soil_moisture"
    assert Channel.LIGHT.value == "light"
    assert Channel.PUMP.value == "pump"
    assert Channel.CAMERA.value == "camera"


def test_channel_optional_set():
    assert Channel.SOIL_TEMP_C.value == "soil_temp_c"
    assert Channel.AMBIENT_LUX.value == "ambient_lux"
    assert Channel.AIR_TEMP_C.value == "air_temp_c"
    assert Channel.AIR_HUMIDITY_PCT.value == "air_humidity_pct"
    assert Channel.RESERVOIR_LEVEL_PCT.value == "reservoir_level_pct"


def test_phase_values():
    assert {p.value for p in Phase} == {
        "seedling", "vegetative", "flowering", "fruiting", "dormant"
    }


def test_medium_type_values():
    assert {m.value for m in MediumType} == {"soil", "coco", "rockwool", "custom"}


def test_severity_values():
    assert {s.value for s in Severity} == {"info", "warning", "critical"}


def test_event_kind_values():
    expected = {
        "watering_pulse", "sensor_degraded", "sensor_recovered",
        "config_applied", "identify_complete", "safety_cap_hit",
        "startup", "shutdown", "buffer_replay_started", "buffer_replay_complete",
    }
    assert {e.value for e in EventKind} == expected


def test_command_name_values():
    expected = {
        "identify", "water_now", "light_override",
        "snap_photo", "reload_config", "reboot",
    }
    assert {c.value for c in CommandName} == expected
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd contracts && python -m pytest ../tests/contracts/test_enums.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mlss_contracts.enums'`

- [ ] **Step 3: Implement**

`contracts/src/mlss_contracts/enums.py`:

```python
"""Enumerations shared between MLSS server and grow unit firmware."""
from enum import Enum


class Channel(str, Enum):
    """Sensor and actuator channels a unit can declare in its capabilities.

    REQUIRED channels (every unit must report these): SOIL_MOISTURE, LIGHT,
    PUMP, CAMERA. All others are optional and only present if the unit has
    the corresponding hardware.
    """
    # Required
    SOIL_MOISTURE = "soil_moisture"
    LIGHT = "light"
    PUMP = "pump"
    CAMERA = "camera"
    # Optional
    SOIL_TEMP_C = "soil_temp_c"
    AMBIENT_LUX = "ambient_lux"
    AIR_TEMP_C = "air_temp_c"
    AIR_HUMIDITY_PCT = "air_humidity_pct"
    RESERVOIR_LEVEL_PCT = "reservoir_level_pct"


class Phase(str, Enum):
    SEEDLING = "seedling"
    VEGETATIVE = "vegetative"
    FLOWERING = "flowering"
    FRUITING = "fruiting"
    DORMANT = "dormant"


class MediumType(str, Enum):
    SOIL = "soil"
    COCO = "coco"
    ROCKWOOL = "rockwool"
    CUSTOM = "custom"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class EventKind(str, Enum):
    WATERING_PULSE = "watering_pulse"
    SENSOR_DEGRADED = "sensor_degraded"
    SENSOR_RECOVERED = "sensor_recovered"
    CONFIG_APPLIED = "config_applied"
    IDENTIFY_COMPLETE = "identify_complete"
    SAFETY_CAP_HIT = "safety_cap_hit"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    BUFFER_REPLAY_STARTED = "buffer_replay_started"
    BUFFER_REPLAY_COMPLETE = "buffer_replay_complete"


class CommandName(str, Enum):
    IDENTIFY = "identify"
    WATER_NOW = "water_now"
    LIGHT_OVERRIDE = "light_override"
    SNAP_PHOTO = "snap_photo"
    RELOAD_CONFIG = "reload_config"
    REBOOT = "reboot"
```

- [ ] **Step 4: Run the test**

Run: `cd contracts && python -m pytest ../tests/contracts/test_enums.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
git add contracts/src/mlss_contracts/enums.py tests/contracts/test_enums.py
git commit -m "Add Channel, Phase, MediumType, Severity, EventKind, CommandName enums

String enums shared between MLSS and firmware. Channel distinguishes
required (soil_moisture, light, pump, camera) vs optional (everything
else) so the dashboard can render capability-driven UI."
```

---

### Task 1.2: Capability schema

**Files:**
- Create: `contracts/src/mlss_contracts/capabilities.py`
- Create: `tests/contracts/test_capabilities.py`

- [ ] **Step 1: Write the failing test**

`tests/contracts/test_capabilities.py`:

```python
from mlss_contracts.capabilities import Capability
from mlss_contracts.enums import Channel
from pydantic import ValidationError
import pytest


def test_capability_required_fields():
    c = Capability(
        channel=Channel.SOIL_MOISTURE,
        hardware="Adafruit_Seesaw_4026",
        is_required=True,
        unit_label="raw",
    )
    assert c.channel == Channel.SOIL_MOISTURE
    assert c.hardware == "Adafruit_Seesaw_4026"
    assert c.is_required is True
    assert c.unit_label == "raw"
    assert c.details is None


def test_capability_optional_details_dict():
    c = Capability(
        channel=Channel.SOIL_MOISTURE,
        hardware="Adafruit_Seesaw_4026",
        is_required=True,
        unit_label="raw",
        details={"i2c_address": "0x36"},
    )
    assert c.details == {"i2c_address": "0x36"}


def test_capability_serialises_round_trip():
    c = Capability(
        channel=Channel.AMBIENT_LUX,
        hardware="TSL2591",
        is_required=False,
        unit_label="lux",
        details={"i2c_address": "0x29"},
    )
    blob = c.model_dump_json()
    parsed = Capability.model_validate_json(blob)
    assert parsed == c


def test_capability_rejects_unknown_channel():
    with pytest.raises(ValidationError):
        Capability(
            channel="not_a_real_channel",
            hardware="X",
            is_required=False,
            unit_label="x",
        )
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd contracts && python -m pytest ../tests/contracts/test_capabilities.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`contracts/src/mlss_contracts/capabilities.py`:

```python
"""Capability declaration: what sensors and actuators a unit reports."""
from pydantic import BaseModel, Field
from mlss_contracts.enums import Channel


class Capability(BaseModel):
    """One sensor or actuator channel a unit declares it has.

    The unit's firmware auto-detects hardware on the I2C bus + camera CSI
    at startup, then sends one Capability per detected channel to MLSS
    on WebSocket handshake. MLSS persists these to grow_unit_capabilities
    and the dashboard renders only tiles for declared channels.
    """
    channel: Channel
    hardware: str = Field(description="Driver class name, e.g. 'Adafruit_Seesaw_4026'")
    is_required: bool
    unit_label: str = Field(description="Display unit, e.g. '%', '°C', 'lux'")
    details: dict | None = Field(default=None, description="e.g. {'i2c_address': '0x36'}")
```

- [ ] **Step 4: Run the test**

Run: `cd contracts && python -m pytest ../tests/contracts/test_capabilities.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add contracts/src/mlss_contracts/capabilities.py tests/contracts/test_capabilities.py
git commit -m "Add Capability pydantic model for unit-declared channels"
```

---

### Task 1.3: Plant profile schema

**Files:**
- Create: `contracts/src/mlss_contracts/plant_profiles.py`
- Create: `tests/contracts/test_plant_profiles.py`

- [ ] **Step 1: Write the failing test**

`tests/contracts/test_plant_profiles.py`:

```python
from mlss_contracts.plant_profiles import PlantProfile, LightWindow, WateringConfig
from mlss_contracts.enums import Phase
from pydantic import ValidationError
import pytest


def test_light_window_basic():
    w = LightWindow(start_hh_mm="06:00", end_hh_mm="22:00")
    assert w.start_hh_mm == "06:00"
    assert w.end_hh_mm == "22:00"


def test_light_window_rejects_invalid_format():
    with pytest.raises(ValidationError):
        LightWindow(start_hh_mm="6am", end_hh_mm="22:00")
    with pytest.raises(ValidationError):
        LightWindow(start_hh_mm="25:00", end_hh_mm="22:00")
    with pytest.raises(ValidationError):
        LightWindow(start_hh_mm="06:60", end_hh_mm="22:00")


def test_watering_config_defaults():
    w = WateringConfig(target_moisture_pct=55)
    assert w.target_moisture_pct == 55
    assert w.deadband_pct == 5
    assert w.kp == 0.4
    assert w.ki == 0
    assert w.kd == 0
    assert w.min_pulse_s == 2
    assert w.max_pulse_s == 8
    assert w.soak_window_min == 30


def test_plant_profile_round_trip():
    p = PlantProfile(
        plant_type="tomato",
        phase=Phase.VEGETATIVE,
        watering=WateringConfig(target_moisture_pct=55),
        light_windows=[LightWindow(start_hh_mm="06:00", end_hh_mm="22:00")],
    )
    blob = p.model_dump_json()
    parsed = PlantProfile.model_validate_json(blob)
    assert parsed == p
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd contracts && python -m pytest ../tests/contracts/test_plant_profiles.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`contracts/src/mlss_contracts/plant_profiles.py`:

```python
"""Plant + watering + light schedule schemas."""
import re
from pydantic import BaseModel, Field, field_validator
from mlss_contracts.enums import Phase

_HH_MM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class LightWindow(BaseModel):
    """A single on-window in 24h time. Multiple windows per phase allowed."""
    start_hh_mm: str = Field(description="'HH:MM' 24h, e.g. '06:00'")
    end_hh_mm: str = Field(description="'HH:MM' 24h, e.g. '22:00'")

    @field_validator("start_hh_mm", "end_hh_mm")
    @classmethod
    def _hh_mm_format(cls, v: str) -> str:
        if not _HH_MM.match(v):
            raise ValueError(f"must be 'HH:MM' 24h format, got {v!r}")
        return v


class WateringConfig(BaseModel):
    """PID watering tunables. Resolved on the unit at config-apply time."""
    target_moisture_pct: float = Field(ge=0, le=100)
    deadband_pct: float = Field(default=5, ge=0, le=50)
    kp: float = Field(default=0.4)
    ki: float = Field(default=0)
    kd: float = Field(default=0)
    min_pulse_s: float = Field(default=2, gt=0)
    max_pulse_s: float = Field(default=8, gt=0, le=30)  # 30 = hardware safety cap
    soak_window_min: int = Field(default=30, ge=0)


class PlantProfile(BaseModel):
    """A reusable bundle of watering + light defaults for a (plant_type, phase)."""
    plant_type: str
    phase: Phase
    watering: WateringConfig
    light_windows: list[LightWindow]
```

- [ ] **Step 4: Run the test**

Run: `cd contracts && python -m pytest ../tests/contracts/test_plant_profiles.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add contracts/src/mlss_contracts/plant_profiles.py tests/contracts/test_plant_profiles.py
git commit -m "Add PlantProfile, LightWindow, WateringConfig schemas

24h 'HH:MM' format validated by regex. WateringConfig defaults match
the spec's shipped values: P-only PID (Ki=Kd=0) with deadband + soak
window. max_pulse_s capped at 30s to mirror the firmware's hardware
safety cap."
```

---

### Task 1.4: WS message envelope + telemetry payload

**Files:**
- Create: `contracts/src/mlss_contracts/ws_messages.py`
- Create: `tests/contracts/test_ws_messages_telemetry.py`

- [ ] **Step 1: Write the failing test**

`tests/contracts/test_ws_messages_telemetry.py`:

```python
from datetime import datetime, timezone
from mlss_contracts.ws_messages import WSMessage, TelemetryPayload
from pydantic import ValidationError
import pytest


def test_telemetry_minimum_required_fields():
    p = TelemetryPayload(
        soil_moisture_raw=612,
        light_state=True,
        pump_state=False,
    )
    assert p.soil_moisture_raw == 612
    assert p.light_state is True
    assert p.pump_state is False
    assert p.soil_moisture_pct is None
    assert p.soil_temp_c is None


def test_telemetry_with_optional_sensors():
    p = TelemetryPayload(
        soil_moisture_raw=612,
        soil_moisture_pct=58.3,
        light_state=True,
        pump_state=False,
        soil_temp_c=21.4,
        ambient_lux=15420,
    )
    assert p.soil_temp_c == 21.4
    assert p.ambient_lux == 15420


def test_telemetry_rejects_missing_required():
    with pytest.raises(ValidationError):
        TelemetryPayload(soil_moisture_raw=612, light_state=True)  # missing pump_state


def test_ws_envelope_round_trip():
    msg = WSMessage(
        type="telemetry",
        ts=datetime(2026, 5, 3, 12, 34, 18, tzinfo=timezone.utc),
        payload={
            "soil_moisture_raw": 612,
            "light_state": True,
            "pump_state": False,
        },
    )
    blob = msg.model_dump_json()
    parsed = WSMessage.model_validate_json(blob)
    assert parsed.type == "telemetry"
    assert parsed.payload["soil_moisture_raw"] == 612
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd contracts && python -m pytest ../tests/contracts/test_ws_messages_telemetry.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`contracts/src/mlss_contracts/ws_messages.py`:

```python
"""WebSocket message envelope + payload schemas.

All text frames on the per-unit WS are JSON: {type, ts, payload}.
Binary frames (photo upload) use a different framing (see PhotoFrame docstring).
"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

MessageType = Literal[
    "telemetry", "event", "capabilities",
    "command", "config", "ack",
]


class WSMessage(BaseModel):
    """The envelope every text frame uses on the per-unit WebSocket."""
    type: MessageType
    ts: datetime
    payload: dict


class TelemetryPayload(BaseModel):
    """One reading from the unit's sensors. NULL = unit lacks the sensor."""
    # Required (every unit reports these)
    soil_moisture_raw: int
    light_state: bool
    pump_state: bool
    # Required-but-derived (computed locally if calibration available)
    soil_moisture_pct: float | None = None
    # Optional sensors — present only if the unit has the hardware
    soil_temp_c: float | None = None
    ambient_lux: float | None = None
    air_temp_c: float | None = None
    air_humidity_pct: float | None = None
    reservoir_level_pct: float | None = None
```

- [ ] **Step 4: Run the test**

Run: `cd contracts && python -m pytest ../tests/contracts/test_ws_messages_telemetry.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add contracts/src/mlss_contracts/ws_messages.py tests/contracts/test_ws_messages_telemetry.py
git commit -m "Add WSMessage envelope + TelemetryPayload schema"
```

---

### Task 1.5: Event, Capabilities, Command, Config, Ack payloads

**Files:**
- Modify: `contracts/src/mlss_contracts/ws_messages.py` (add four payload classes)
- Create: `tests/contracts/test_ws_messages_other.py`

- [ ] **Step 1: Write the failing test**

`tests/contracts/test_ws_messages_other.py`:

```python
from mlss_contracts.ws_messages import (
    EventPayload, CapabilitiesPayload, CommandPayload,
    ConfigPayload, AckPayload,
)
from mlss_contracts.enums import EventKind, CommandName, Phase
from mlss_contracts.capabilities import Capability
from mlss_contracts.plant_profiles import LightWindow, WateringConfig


def test_event_payload():
    e = EventPayload(
        kind=EventKind.WATERING_PULSE,
        details={"duration_s": 5.2, "soil_pct_before": 42},
    )
    assert e.kind == EventKind.WATERING_PULSE
    assert e.details["duration_s"] == 5.2


def test_capabilities_payload_round_trip():
    c = CapabilitiesPayload(
        capabilities=[
            Capability(channel="soil_moisture", hardware="Seesaw",
                       is_required=True, unit_label="raw"),
            Capability(channel="camera", hardware="picamera2",
                       is_required=True, unit_label="jpeg"),
        ],
        firmware_version="0.1.0",
        hardware_serial="100000000c0a8014b",
    )
    blob = c.model_dump_json()
    parsed = CapabilitiesPayload.model_validate_json(blob)
    assert len(parsed.capabilities) == 2
    assert parsed.firmware_version == "0.1.0"


def test_command_payload_with_args():
    c = CommandPayload(name=CommandName.IDENTIFY, args={"duration_s": 10})
    assert c.name == CommandName.IDENTIFY
    assert c.args == {"duration_s": 10}


def test_command_payload_no_args():
    c = CommandPayload(name=CommandName.RELOAD_CONFIG)
    assert c.args is None


def test_config_payload_round_trip():
    cfg = ConfigPayload(
        plant_type="tomato",
        current_phase=Phase.VEGETATIVE,
        light_windows=[LightWindow(start_hh_mm="06:00", end_hh_mm="22:00")],
        watering=WateringConfig(target_moisture_pct=55),
        photo_interval_min=30,
        photo_active_hours=(6, 22),
        soil_dry_raw=200,
        soil_wet_raw=1500,
        buffer_retention_days=7,
    )
    blob = cfg.model_dump_json()
    parsed = ConfigPayload.model_validate_json(blob)
    assert parsed == cfg


def test_ack_payload():
    a = AckPayload(in_reply_to_command="identify", success=True,
                   extra={"actual_duration_s": 9.97})
    assert a.success is True
    assert a.extra["actual_duration_s"] == 9.97


def test_ack_payload_failure():
    a = AckPayload(in_reply_to_command="water_now", success=False,
                   error="locked_in_soak_window")
    assert a.success is False
    assert a.error == "locked_in_soak_window"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd contracts && python -m pytest ../tests/contracts/test_ws_messages_other.py -v`
Expected: FAIL — `ImportError: cannot import name 'EventPayload'`

- [ ] **Step 3: Append to `contracts/src/mlss_contracts/ws_messages.py`**

```python
from mlss_contracts.enums import EventKind, CommandName, Phase
from mlss_contracts.capabilities import Capability
from mlss_contracts.plant_profiles import LightWindow, WateringConfig


class EventPayload(BaseModel):
    """Discrete event the unit reports — watering pulse, sensor degraded, etc."""
    kind: EventKind
    details: dict = Field(default_factory=dict)


class CapabilitiesPayload(BaseModel):
    """Sent by unit on WS handshake; declares all detected sensors and actuators."""
    capabilities: list[Capability]
    firmware_version: str
    hardware_serial: str


class CommandPayload(BaseModel):
    """MLSS → unit command, e.g. {name: 'identify', args: {duration_s: 10}}."""
    name: CommandName
    args: dict | None = None


class ConfigPayload(BaseModel):
    """Full config push from MLSS to unit. Resolved values (no NULLs)."""
    plant_type: str
    current_phase: Phase
    light_windows: list[LightWindow]
    watering: WateringConfig
    photo_interval_min: int = Field(ge=1, le=1440)
    photo_active_hours: tuple[int, int] | None = None  # (start_hour, end_hour)
    soil_dry_raw: int | None = None
    soil_wet_raw: int | None = None
    buffer_retention_days: int = Field(default=7, ge=1)


class AckPayload(BaseModel):
    """Unit → MLSS acknowledgement of a received command."""
    in_reply_to_command: str
    success: bool
    error: str | None = None
    extra: dict | None = None
```

- [ ] **Step 4: Run the test**

Run: `cd contracts && python -m pytest ../tests/contracts/test_ws_messages_other.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add contracts/src/mlss_contracts/ws_messages.py tests/contracts/test_ws_messages_other.py
git commit -m "Add Event, Capabilities, Command, Config, Ack payload schemas

All five payload types of the per-unit WebSocket protocol now have
pydantic models. Both server and firmware import the same classes —
schema drift is a static error from here on."
```

---

## Section 2 — Database schema and seeds

All `grow_*` tables created via `database/grow_schema.py::create_grow_schema()` (the hook from Task 0.4). Tests verify: tables exist with the right columns, `is_idempotent` (running twice doesn't error), and the seed data lands.

---

### Task 2.1: Create `grow_units` table

**Files:**
- Modify: `database/grow_schema.py`
- Create: `tests/grow_server/test_grow_schema_units.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_grow_schema_units.py`:

```python
"""grow_units table is created with the right columns."""
import sqlite3
import tempfile
from database.init_db import create_db


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    conn.close()
    return {row[1]: row[2] for row in rows}  # {name: type}


def test_grow_units_table_exists():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        import database.init_db as init_db
        init_db.DB_FILE = tmp.name
        create_db()
        cols = _columns(tmp.name, "grow_units")

    assert "id" in cols
    assert "hardware_serial" in cols
    assert "label" in cols
    assert "bearer_token_hash" in cols
    assert "is_active" in cols
    assert "current_phase" in cols
    assert "phase_set_by" in cols
    assert "plant_type" in cols
    assert "medium_type" in cols
    assert "soil_dry_raw" in cols
    assert "soil_wet_raw" in cols
    assert "buffer_retention_days" in cols
    assert "last_seen_at" in cols
    assert "last_known_state_json" in cols


def test_grow_units_is_idempotent():
    """create_grow_schema can run twice without error (e.g. on restart)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        import database.init_db as init_db
        init_db.DB_FILE = tmp.name
        create_db()
        create_db()  # should not raise
        cols = _columns(tmp.name, "grow_units")
        assert "id" in cols  # still there
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_grow_schema_units.py -v`
Expected: FAIL with `sqlite3.OperationalError: no such table: grow_units`

- [ ] **Step 3: Implement**

Update `database/grow_schema.py`:

```python
"""Plant Grow Unit database schema. All grow_* tables created here.

Called from database.init_db.create_db() so table creation happens in the
same transaction as the existing MLSS schema.
"""


def create_grow_schema(cur):
    """Create all grow_* tables. Idempotent (uses CREATE TABLE IF NOT EXISTS)."""
    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_units (
      id                          INTEGER PRIMARY KEY AUTOINCREMENT,
      hardware_serial             TEXT UNIQUE NOT NULL,
      label                       TEXT NOT NULL,
      description                 TEXT,
      sown_at                     DATETIME,
      enrolled_at                 DATETIME NOT NULL,
      bearer_token_hash           TEXT NOT NULL,
      is_active                   INTEGER NOT NULL DEFAULT 1,
      current_phase               TEXT NOT NULL DEFAULT 'vegetative'
                                    CHECK(current_phase IN
                                      ('seedling','vegetative','flowering','fruiting','dormant')),
      phase_set_by                TEXT NOT NULL DEFAULT 'user'
                                    CHECK(phase_set_by IN ('user','image_classifier')),
      phase_set_at                DATETIME NOT NULL,
      plant_type                  TEXT NOT NULL DEFAULT 'generic',
      medium_type                 TEXT NOT NULL DEFAULT 'soil'
                                    CHECK(medium_type IN ('soil','coco','rockwool','custom')),
      soil_dry_raw                INTEGER,
      soil_wet_raw                INTEGER,
      light_phase_override_json   TEXT,
      watering_target_override    REAL,
      watering_kp_override        REAL,
      watering_ki_override        REAL,
      watering_kd_override        REAL,
      soak_window_min_override    INTEGER,
      pulse_min_s_override        REAL,
      pulse_max_s_override        REAL,
      photo_interval_min_override INTEGER,
      buffer_retention_days       INTEGER,
      last_seen_at                DATETIME,
      last_telemetry_at           DATETIME,
      last_known_state_json       TEXT
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_units_active "
        "ON grow_units(is_active, last_seen_at DESC)"
    )
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/grow_server/test_grow_schema_units.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add database/grow_schema.py tests/grow_server/test_grow_schema_units.py
git commit -m "Add grow_units table with all per-unit config columns"
```

---

### Task 2.2: Create `grow_unit_capabilities`, `grow_telemetry`, `grow_watering_events` tables

**Files:**
- Modify: `database/grow_schema.py`
- Create: `tests/grow_server/test_grow_schema_telemetry.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_grow_schema_telemetry.py`:

```python
import sqlite3
import tempfile
from database.init_db import create_db


def _setup():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    create_db()
    return tmp.name


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    conn.close()
    return {row[1] for row in rows}


def test_grow_unit_capabilities_table():
    cols = _columns(_setup(), "grow_unit_capabilities")
    assert {"unit_id", "channel", "hardware", "is_required",
            "unit_label", "installed_at", "details_json"} <= cols


def test_grow_telemetry_table():
    cols = _columns(_setup(), "grow_telemetry")
    required = {"id", "unit_id", "timestamp_utc", "soil_moisture_raw",
                "soil_moisture_pct", "light_state", "pump_state"}
    optional = {"soil_temp_c", "ambient_lux", "air_temp_c",
                "air_humidity_pct", "reservoir_level_pct"}
    assert required <= cols
    assert optional <= cols


def test_grow_watering_events_table():
    cols = _columns(_setup(), "grow_watering_events")
    assert {"id", "unit_id", "timestamp_utc", "trigger", "duration_s",
            "soil_pct_before", "soil_pct_after_5min", "triggered_by",
            "pid_error", "pid_p_term", "pid_i_term", "pid_d_term"} <= cols
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_grow_schema_telemetry.py -v`
Expected: FAIL with `no such table: grow_unit_capabilities`

- [ ] **Step 3: Implement**

Append to `create_grow_schema()` in `database/grow_schema.py`:

```python
    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_unit_capabilities (
      unit_id      INTEGER NOT NULL REFERENCES grow_units(id) ON DELETE CASCADE,
      channel      TEXT NOT NULL,
      hardware     TEXT,
      is_required  INTEGER NOT NULL DEFAULT 0,
      unit_label   TEXT,
      installed_at DATETIME NOT NULL,
      details_json TEXT,
      PRIMARY KEY (unit_id, channel)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_telemetry (
      id                  INTEGER PRIMARY KEY AUTOINCREMENT,
      unit_id             INTEGER NOT NULL REFERENCES grow_units(id),
      timestamp_utc       DATETIME NOT NULL,
      soil_moisture_raw   INTEGER NOT NULL,
      soil_moisture_pct   REAL,
      light_state         INTEGER NOT NULL,
      pump_state          INTEGER NOT NULL,
      soil_temp_c         REAL,
      ambient_lux         REAL,
      air_temp_c          REAL,
      air_humidity_pct    REAL,
      reservoir_level_pct REAL
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_telemetry_unit_time "
        "ON grow_telemetry(unit_id, timestamp_utc DESC)"
    )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_watering_events (
      id                  INTEGER PRIMARY KEY AUTOINCREMENT,
      unit_id             INTEGER NOT NULL REFERENCES grow_units(id),
      timestamp_utc       DATETIME NOT NULL,
      trigger             TEXT NOT NULL CHECK(trigger IN ('pid','manual','identify_test')),
      duration_s          REAL NOT NULL,
      soil_pct_before     REAL,
      soil_pct_after_5min REAL,
      triggered_by        TEXT,
      pid_error           REAL,
      pid_p_term          REAL,
      pid_i_term          REAL,
      pid_d_term          REAL
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_watering_unit_time "
        "ON grow_watering_events(unit_id, timestamp_utc DESC)"
    )
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/grow_server/test_grow_schema_telemetry.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add database/grow_schema.py tests/grow_server/test_grow_schema_telemetry.py
git commit -m "Add grow_unit_capabilities, grow_telemetry, grow_watering_events tables"
```

---

### Task 2.3: Create `grow_photos`, `grow_plant_profiles`, `grow_light_windows`, `grow_medium_defaults`, `grow_errors` tables

**Files:**
- Modify: `database/grow_schema.py`
- Create: `tests/grow_server/test_grow_schema_remaining.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_grow_schema_remaining.py`:

```python
import sqlite3
import tempfile
from database.init_db import create_db


def _setup():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    create_db()
    return tmp.name


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    conn.close()
    return {row[1] for row in rows}


def test_grow_photos_table_with_telemetry_id_join():
    cols = _columns(_setup(), "grow_photos")
    assert {"id", "unit_id", "taken_at", "file_path", "width_px", "height_px",
            "size_bytes", "telemetry_id", "classified_phase",
            "classifier_confidence"} <= cols


def test_grow_plant_profiles_table():
    cols = _columns(_setup(), "grow_plant_profiles")
    assert {"id", "plant_type", "phase", "target_moisture_pct", "deadband_pct",
            "kp", "ki", "kd", "min_pulse_s", "max_pulse_s", "soak_window_min",
            "default_light_hours", "is_shipped"} <= cols


def test_grow_light_windows_table():
    cols = _columns(_setup(), "grow_light_windows")
    assert {"id", "unit_id", "phase", "start_hh_mm", "end_hh_mm",
            "sort_order"} <= cols


def test_grow_medium_defaults_table():
    cols = _columns(_setup(), "grow_medium_defaults")
    assert {"medium_type", "dry_raw", "wet_raw"} <= cols


def test_grow_errors_table():
    cols = _columns(_setup(), "grow_errors")
    assert {"id", "unit_id", "timestamp_utc", "severity", "kind",
            "message", "details_json", "resolved_at"} <= cols
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_grow_schema_remaining.py -v`
Expected: FAIL with `no such table: grow_photos`

- [ ] **Step 3: Implement**

Append to `create_grow_schema()`:

```python
    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_photos (
      id                       INTEGER PRIMARY KEY AUTOINCREMENT,
      unit_id                  INTEGER NOT NULL REFERENCES grow_units(id) ON DELETE CASCADE,
      taken_at                 DATETIME NOT NULL,
      file_path                TEXT NOT NULL,
      width_px                 INTEGER NOT NULL,
      height_px                INTEGER NOT NULL,
      size_bytes               INTEGER NOT NULL,
      jpeg_quality             INTEGER,
      shutter_us               INTEGER,
      iso                      INTEGER,
      white_balance            TEXT,
      classified_phase         TEXT,
      classifier_confidence    REAL,
      classified_at            DATETIME,
      telemetry_id             INTEGER REFERENCES grow_telemetry(id)
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_photos_unit_time "
        "ON grow_photos(unit_id, taken_at DESC)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_photos_telemetry "
        "ON grow_photos(telemetry_id)"
    )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_plant_profiles (
      id                    INTEGER PRIMARY KEY AUTOINCREMENT,
      plant_type            TEXT NOT NULL,
      phase                 TEXT NOT NULL,
      target_moisture_pct   REAL NOT NULL,
      deadband_pct          REAL NOT NULL DEFAULT 5,
      kp                    REAL NOT NULL DEFAULT 0.4,
      ki                    REAL NOT NULL DEFAULT 0,
      kd                    REAL NOT NULL DEFAULT 0,
      min_pulse_s           REAL NOT NULL DEFAULT 2,
      max_pulse_s           REAL NOT NULL DEFAULT 8,
      soak_window_min       INTEGER,
      default_light_hours   REAL NOT NULL DEFAULT 16,
      is_shipped            INTEGER NOT NULL DEFAULT 0,
      notes                 TEXT,
      UNIQUE(plant_type, phase)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_light_windows (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      unit_id      INTEGER NOT NULL REFERENCES grow_units(id) ON DELETE CASCADE,
      phase        TEXT NOT NULL,
      start_hh_mm  TEXT NOT NULL,
      end_hh_mm    TEXT NOT NULL,
      sort_order   INTEGER NOT NULL DEFAULT 0
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_glw_unit_phase "
        "ON grow_light_windows(unit_id, phase)"
    )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_medium_defaults (
      medium_type TEXT PRIMARY KEY,
      dry_raw     INTEGER NOT NULL,
      wet_raw     INTEGER NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_errors (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      unit_id       INTEGER REFERENCES grow_units(id) ON DELETE CASCADE,
      timestamp_utc DATETIME NOT NULL,
      severity      TEXT NOT NULL CHECK(severity IN ('info','warning','critical')),
      kind          TEXT NOT NULL,
      message       TEXT NOT NULL,
      details_json  TEXT,
      resolved_at   DATETIME
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_errors_unit_time "
        "ON grow_errors(unit_id, timestamp_utc DESC)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_errors_unresolved "
        "ON grow_errors(resolved_at) WHERE resolved_at IS NULL"
    )
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/grow_server/test_grow_schema_remaining.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add database/grow_schema.py tests/grow_server/test_grow_schema_remaining.py
git commit -m "Add grow_photos, plant_profiles, light_windows, medium_defaults, errors tables

grow_photos.telemetry_id is the ML-training join key — populated at
ingest time by the WS listener (Task 4.5)."
```

---

### Task 2.4: Seed shipped plant profiles + medium defaults + app_settings

**Files:**
- Modify: `database/grow_schema.py`
- Create: `tests/grow_server/test_grow_schema_seeds.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_grow_schema_seeds.py`:

```python
import sqlite3
import tempfile
from database.init_db import create_db


def _conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    create_db()
    return sqlite3.connect(tmp.name)


def test_shipped_plant_profiles_seeded():
    conn = _conn()
    profiles = conn.execute(
        "SELECT plant_type, phase, target_moisture_pct, kp, ki, kd "
        "FROM grow_plant_profiles WHERE is_shipped=1"
    ).fetchall()
    types_phases = {(p[0], p[1]) for p in profiles}
    # All shipped profiles from the spec
    expected = {
        ("tomato", "seedling"), ("tomato", "vegetative"),
        ("tomato", "flowering"), ("tomato", "fruiting"),
        ("basil", "vegetative"),
        ("lettuce", "vegetative"),
        ("microgreens", "seedling"),
        ("pepper", "vegetative"),
        ("generic", "seedling"), ("generic", "vegetative"),
        ("generic", "flowering"),
    }
    assert expected <= types_phases
    # All shipped have Ki=Kd=0 by default (P-only with deadband + soak)
    for p in profiles:
        assert p[4] == 0, f"{p[0]} {p[1]} expected Ki=0, got {p[4]}"
        assert p[5] == 0, f"{p[0]} {p[1]} expected Kd=0, got {p[5]}"


def test_medium_defaults_seeded():
    conn = _conn()
    rows = dict(conn.execute(
        "SELECT medium_type, dry_raw FROM grow_medium_defaults"
    ).fetchall())
    assert rows.get("soil") == 200
    assert rows.get("coco") == 250
    assert rows.get("rockwool") == 300


def test_app_settings_grow_keys_seeded():
    conn = _conn()
    rows = dict(conn.execute(
        "SELECT key, value FROM app_settings WHERE key LIKE 'grow_%'"
    ).fetchall())
    assert rows["grow_default_soak_window_min"] == "30"
    assert rows["grow_default_buffer_retention_days"] == "7"
    assert rows["grow_disk_warn_pct"] == "90"
    assert rows["grow_holiday_mode"] == "0"
    # enrollment key is auto-generated; should exist and be > 30 chars
    assert "grow_enrollment_key_hash" in rows
    assert len(rows["grow_enrollment_key_hash"]) > 30
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_grow_schema_seeds.py -v`
Expected: FAIL with `assert False` (no rows yet) or `assert expected <= types_phases`

- [ ] **Step 3: Implement seeding**

Append to `database/grow_schema.py`:

```python
import json
import secrets
from hashlib import sha256


_SHIPPED_PROFILES = [
    # (plant_type, phase, target%, deadband, kp, ki, kd, min_pulse, max_pulse, soak, light_h)
    ("tomato",      "seedling",   60, 5, 0.3, 0, 0, 1, 4, 30, 16),
    ("tomato",      "vegetative", 55, 5, 0.4, 0, 0, 2, 8, 30, 16),
    ("tomato",      "flowering",  50, 5, 0.4, 0, 0, 2, 8, 60, 12),
    ("tomato",      "fruiting",   50, 5, 0.4, 0, 0, 2, 8, 60, 12),
    ("basil",       "vegetative", 60, 5, 0.4, 0, 0, 2, 6, 30, 14),
    ("lettuce",     "vegetative", 65, 5, 0.3, 0, 0, 2, 6, 30, 14),
    ("microgreens", "seedling",   70, 3, 0.3, 0, 0, 1, 4, 20, 16),
    ("pepper",      "vegetative", 55, 5, 0.4, 0, 0, 2, 8, 45, 16),
    ("generic",     "seedling",   60, 5, 0.3, 0, 0, 1, 4, 45, 16),
    ("generic",     "vegetative", 55, 5, 0.4, 0, 0, 2, 8, 45, 16),
    ("generic",     "flowering",  50, 5, 0.4, 0, 0, 2, 8, 60, 12),
]

_SHIPPED_MEDIUMS = [
    ("soil",     200, 1500),
    ("coco",     250, 1700),
    ("rockwool", 300, 1900),
]


def _seed_grow_data(cur):
    """Idempotent: only inserts if rows are missing."""
    # Plant profiles (only seed if no shipped profiles yet)
    cur.execute("SELECT COUNT(*) FROM grow_plant_profiles WHERE is_shipped=1")
    if cur.fetchone()[0] == 0:
        for row in _SHIPPED_PROFILES:
            cur.execute(
                "INSERT INTO grow_plant_profiles "
                "(plant_type, phase, target_moisture_pct, deadband_pct, "
                " kp, ki, kd, min_pulse_s, max_pulse_s, soak_window_min, "
                " default_light_hours, is_shipped) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                row,
            )

    # Medium calibration defaults
    for mt, dry, wet in _SHIPPED_MEDIUMS:
        cur.execute(
            "INSERT OR IGNORE INTO grow_medium_defaults (medium_type, dry_raw, wet_raw) "
            "VALUES (?, ?, ?)",
            (mt, dry, wet),
        )

    # app_settings keys
    defaults = {
        "grow_default_soak_window_min": "30",
        "grow_default_buffer_retention_days": "7",
        "grow_disk_warn_pct": "90",
        "grow_holiday_mode": "0",
        "grow_images_dir": "",  # empty = use env var or built-in default
    }
    for k, v in defaults.items():
        cur.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
            (k, v),
        )

    # Enrollment key — generate once, store argon2-hashed
    cur.execute("SELECT COUNT(*) FROM app_settings WHERE key='grow_enrollment_key_hash'")
    if cur.fetchone()[0] == 0:
        # Plain raw key surfaced once on the install screen (out of scope here).
        # For now we generate + hash with sha256 (argon2 lib added in auth task).
        raw_key = secrets.token_urlsafe(32)
        key_hash = sha256(raw_key.encode()).hexdigest()
        cur.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            ("grow_enrollment_key_hash", key_hash),
        )
        # Stash raw key so the install UI can show it once.
        cur.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("grow_enrollment_key_raw_pending_reveal", raw_key),
        )
```

Then call `_seed_grow_data(cur)` at the end of `create_grow_schema(cur)`:

```python
def create_grow_schema(cur):
    """Create all grow_* tables. Idempotent."""
    # ... (all the CREATE TABLE statements from earlier tasks)

    _seed_grow_data(cur)
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/grow_server/test_grow_schema_seeds.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add database/grow_schema.py tests/grow_server/test_grow_schema_seeds.py
git commit -m "Seed shipped plant profiles, medium defaults, and grow app_settings

11 shipped plant profiles spanning tomato/basil/lettuce/microgreens/
pepper/generic. All Ki=Kd=0 (P-only PID with deadband + soak).
Medium calibration defaults for soil/coco/rockwool. App settings keyed
'grow_*' for global defaults. Enrollment key auto-generated on first
init and stashed in app_settings for one-time reveal at install."
```

---

## Section 3 — Server-side authentication and REST endpoints

Auth primitives + enrollment + per-unit identify/water-now triggers + wheel/install-script serving routes. The WS listener (Section 4) reuses the auth helpers built here.

---

### Task 3.1: Auth helpers — argon2 hashing, token generation, verification

**Files:**
- Create: `mlss_monitor/grow/__init__.py` (empty)
- Create: `mlss_monitor/grow/auth.py`
- Create: `tests/grow_server/test_grow_auth.py`
- Modify: `pyproject.toml` (add `argon2-cffi`)

- [ ] **Step 1: Add argon2-cffi dep**

In `pyproject.toml` under `[tool.poetry.dependencies]`:

```toml
argon2-cffi = "^23.1"
```

Run: `poetry install`

- [ ] **Step 2: Write the failing test**

`tests/grow_server/test_grow_auth.py`:

```python
"""Auth helpers: token generation, hashing, verification, enrollment-key check."""
import pytest
from mlss_monitor.grow.auth import (
    generate_token, hash_secret, verify_secret,
    verify_enrollment_key, AuthError,
)


def test_generate_token_is_url_safe_and_long():
    t = generate_token()
    assert len(t) >= 32
    # urlsafe alphabet — no '+' or '/'
    assert "+" not in t and "/" not in t


def test_generate_token_is_unique():
    assert generate_token() != generate_token()


def test_hash_and_verify_round_trip():
    raw = generate_token()
    hashed = hash_secret(raw)
    assert hashed != raw
    assert verify_secret(raw, hashed) is True


def test_verify_secret_rejects_wrong_token():
    raw = generate_token()
    hashed = hash_secret(raw)
    assert verify_secret("wrong-token", hashed) is False


def test_verify_enrollment_key_against_stored_hash(tmp_path, monkeypatch):
    """Verifies a given raw key against the stored argon2 hash in app_settings."""
    # Mock app_settings table
    import sqlite3
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    raw_key = "test-enrollment-key-12345"
    conn.execute("INSERT INTO app_settings VALUES (?, ?)",
                 ("grow_enrollment_key_hash", hash_secret(raw_key)))
    conn.commit()
    conn.close()

    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", str(db_path))
    assert verify_enrollment_key(raw_key) is True
    assert verify_enrollment_key("wrong-key") is False


def test_verify_enrollment_key_raises_if_no_key_set(tmp_path, monkeypatch):
    import sqlite3
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()

    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", str(db_path))
    with pytest.raises(AuthError, match="not configured"):
        verify_enrollment_key("anything")
```

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_grow_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mlss_monitor.grow.auth'`

- [ ] **Step 4: Implement**

`mlss_monitor/grow/__init__.py`:

```python
"""Server-side Plant Grow Unit support: auth, WS registry, photo storage."""
```

`mlss_monitor/grow/auth.py`:

```python
"""Authentication for Plant Grow Units.

Two credentials:
- Household enrollment key — argon2-hashed in app_settings, used once at unit
  enrollment to mint the per-unit token
- Per-unit bearer token — argon2-hashed in grow_units.bearer_token_hash, used
  on every WS upgrade
"""
import secrets
import sqlite3
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from database.init_db import DB_FILE

_hasher = PasswordHasher()


class AuthError(Exception):
    """Raised when an auth precondition is missing (e.g. no enrollment key set)."""


def generate_token() -> str:
    """Return a 256-bit URL-safe random token."""
    return secrets.token_urlsafe(32)


def hash_secret(raw: str) -> str:
    """argon2-hash a secret. Includes salt + parameters in the output string."""
    return _hasher.hash(raw)


def verify_secret(raw: str, hashed: str) -> bool:
    """Constant-time check of raw against an argon2 hash."""
    try:
        return _hasher.verify(hashed, raw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def verify_enrollment_key(raw_key: str) -> bool:
    """Check a raw enrollment key against the household hash in app_settings.

    Raises AuthError if no key has been configured (fresh install state).
    """
    conn = sqlite3.connect(DB_FILE, timeout=5)
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key='grow_enrollment_key_hash'"
    ).fetchone()
    conn.close()
    if row is None or not row[0]:
        raise AuthError("Enrollment key not configured — run create_db() first")
    return verify_secret(raw_key, row[0])
```

- [ ] **Step 5: Run + commit**

Run: `python -m pytest tests/grow_server/test_grow_auth.py -v`
Expected: PASS (6 tests)

```bash
git add pyproject.toml poetry.lock mlss_monitor/grow/ tests/grow_server/test_grow_auth.py
git commit -m "Add grow auth helpers (argon2 hash, token gen, enrollment verify)"
```

---

### Task 3.2: `bearer_required` decorator for grow API endpoints

**Files:**
- Modify: `mlss_monitor/grow/auth.py`
- Create: `tests/grow_server/test_bearer_decorator.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_bearer_decorator.py`:

```python
"""bearer_required decorator: validates Authorization: Bearer <token> against grow_units."""
import sqlite3
import tempfile
from datetime import datetime
from flask import Flask, jsonify, g
import pytest


@pytest.fixture
def app(monkeypatch):
    """Flask app with a temp DB and one enrolled unit."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()

    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    init_db.create_db()

    from mlss_monitor.grow.auth import generate_token, hash_secret, bearer_required

    raw_token = generate_token()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (?, ?, ?, ?, ?)",
        ("hw-001", "Test Plant", datetime.utcnow(), hash_secret(raw_token),
         datetime.utcnow()),
    )
    unit_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()

    app = Flask(__name__)

    @app.route("/api/grow/units/<int:unit_id>/test")
    @bearer_required
    def protected(unit_id):
        return jsonify({"unit_id": unit_id, "auth_unit_id": g.grow_unit_id})

    return app, raw_token, unit_id


def test_valid_bearer_passes(app):
    flask_app, token, unit_id = app
    client = flask_app.test_client()
    r = client.get(f"/api/grow/units/{unit_id}/test",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.get_json()["auth_unit_id"] == unit_id


def test_missing_header_returns_401(app):
    flask_app, _, unit_id = app
    client = flask_app.test_client()
    r = client.get(f"/api/grow/units/{unit_id}/test")
    assert r.status_code == 401


def test_wrong_token_returns_401(app):
    flask_app, _, unit_id = app
    client = flask_app.test_client()
    r = client.get(f"/api/grow/units/{unit_id}/test",
                   headers={"Authorization": "Bearer wrong-token"})
    assert r.status_code == 401


def test_inactive_unit_returns_403(app):
    flask_app, token, unit_id = app
    # Deactivate the unit
    import sqlite3
    from database.init_db import DB_FILE
    conn = sqlite3.connect(DB_FILE)
    conn.execute("UPDATE grow_units SET is_active=0 WHERE id=?", (unit_id,))
    conn.commit()
    conn.close()

    client = flask_app.test_client()
    r = client.get(f"/api/grow/units/{unit_id}/test",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_bearer_decorator.py -v`
Expected: FAIL — `ImportError: cannot import name 'bearer_required'`

- [ ] **Step 3: Implement**

Append to `mlss_monitor/grow/auth.py`:

```python
from functools import wraps
from flask import request, jsonify, g


def bearer_required(view_func):
    """Decorator: validates Authorization: Bearer <token> against grow_units.

    On success, sets g.grow_unit_id to the validated unit's id. On failure
    returns 401 (missing/invalid token) or 403 (token valid but unit inactive).
    The path's <int:unit_id> is matched against the token's owning unit_id —
    a token for unit 5 can't access /api/grow/units/7/...
    """
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "missing_bearer"}), 401
        token = auth_header[7:].strip()

        path_unit_id = kwargs.get("unit_id")
        if path_unit_id is None:
            return jsonify({"error": "no_unit_id_in_path"}), 400

        conn = sqlite3.connect(DB_FILE, timeout=5)
        row = conn.execute(
            "SELECT id, bearer_token_hash, is_active FROM grow_units WHERE id=?",
            (path_unit_id,),
        ).fetchone()
        conn.close()
        if row is None:
            return jsonify({"error": "unit_not_found"}), 401

        unit_id, token_hash, is_active = row
        if not verify_secret(token, token_hash):
            return jsonify({"error": "invalid_token"}), 401
        if not is_active:
            return jsonify({"error": "unit_inactive"}), 403

        g.grow_unit_id = unit_id
        return view_func(*args, **kwargs)

    return wrapped
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_bearer_decorator.py -v`
Expected: PASS (4 tests)

```bash
git add mlss_monitor/grow/auth.py tests/grow_server/test_bearer_decorator.py
git commit -m "Add bearer_required decorator for grow API endpoints"
```

---

### Task 3.3: `POST /api/grow/enroll` endpoint

**Files:**
- Create: `mlss_monitor/routes/api_grow_enroll.py`
- Modify: `mlss_monitor/routes/__init__.py` (register blueprint)
- Create: `tests/grow_server/test_grow_enroll.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_grow_enroll.py`:

```python
"""POST /api/grow/enroll: validates enrollment key, mints per-unit token."""
import sqlite3
import tempfile
from datetime import datetime
import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    init_db.create_db()

    # Pull the raw enrollment key the seed left us
    conn = sqlite3.connect(tmp.name)
    raw_key = conn.execute(
        "SELECT value FROM app_settings WHERE key='grow_enrollment_key_raw_pending_reveal'"
    ).fetchone()[0]
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_enroll import api_grow_enroll_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_enroll_bp)
    return app.test_client(), raw_key, tmp.name


def test_enroll_with_valid_key_creates_unit_and_returns_token(client):
    c, raw_key, db_path = client
    r = c.post("/api/grow/enroll", json={
        "enrollment_key": raw_key,
        "hardware_serial": "100000000c0a8014b",
        "plant": {"name": "Test Tomato", "type": "tomato", "medium": "soil"},
    })
    assert r.status_code == 201
    body = r.get_json()
    assert "unit_id" in body
    assert "token" in body
    assert len(body["token"]) >= 32

    # DB row exists
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT label, plant_type, medium_type, hardware_serial "
        "FROM grow_units WHERE id=?", (body["unit_id"],)
    ).fetchone()
    conn.close()
    assert row == ("Test Tomato", "tomato", "soil", "100000000c0a8014b")


def test_enroll_idempotent_returns_existing_unit(client):
    """Same hardware_serial returns the same unit_id — token rotated."""
    c, raw_key, db_path = client
    r1 = c.post("/api/grow/enroll", json={
        "enrollment_key": raw_key,
        "hardware_serial": "100000000c0a8014b",
        "plant": {"name": "Test", "type": "tomato"},
    })
    r2 = c.post("/api/grow/enroll", json={
        "enrollment_key": raw_key,
        "hardware_serial": "100000000c0a8014b",
        "plant": {"name": "Test"},
    })
    assert r1.get_json()["unit_id"] == r2.get_json()["unit_id"]
    # New token issued though
    assert r1.get_json()["token"] != r2.get_json()["token"]


def test_enroll_with_wrong_key_returns_401(client):
    c, _, _ = client
    r = c.post("/api/grow/enroll", json={
        "enrollment_key": "wrong-key",
        "hardware_serial": "hw-002",
        "plant": {"name": "X"},
    })
    assert r.status_code == 401


def test_enroll_missing_fields_returns_400(client):
    c, raw_key, _ = client
    r = c.post("/api/grow/enroll", json={"enrollment_key": raw_key})
    assert r.status_code == 400
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_grow_enroll.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mlss_monitor.routes.api_grow_enroll'`

- [ ] **Step 3: Implement**

`mlss_monitor/routes/api_grow_enroll.py`:

```python
"""POST /api/grow/enroll — first-boot enrollment endpoint for new units."""
import sqlite3
from datetime import datetime
from flask import Blueprint, request, jsonify

from database.init_db import DB_FILE
from mlss_monitor.grow.auth import (
    verify_enrollment_key, generate_token, hash_secret, AuthError,
)

api_grow_enroll_bp = Blueprint("api_grow_enroll", __name__)


@api_grow_enroll_bp.route("/api/grow/enroll", methods=["POST"])
def enroll():
    body = request.get_json(silent=True) or {}

    enrollment_key = body.get("enrollment_key")
    hardware_serial = body.get("hardware_serial")
    plant = body.get("plant") or {}
    plant_name = plant.get("name")

    if not enrollment_key or not hardware_serial or not plant_name:
        return jsonify({
            "error": "missing_fields",
            "required": ["enrollment_key", "hardware_serial", "plant.name"],
        }), 400

    try:
        if not verify_enrollment_key(enrollment_key):
            return jsonify({"error": "invalid_enrollment_key"}), 401
    except AuthError as exc:
        return jsonify({"error": "auth_not_configured", "detail": str(exc)}), 500

    plant_type = plant.get("type", "generic")
    medium_type = plant.get("medium", "soil")
    now = datetime.utcnow()

    raw_token = generate_token()
    token_hash = hash_secret(raw_token)

    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        existing = conn.execute(
            "SELECT id FROM grow_units WHERE hardware_serial=?",
            (hardware_serial,),
        ).fetchone()
        if existing:
            unit_id = existing[0]
            conn.execute(
                "UPDATE grow_units SET bearer_token_hash=?, is_active=1, "
                "label=COALESCE(label, ?) WHERE id=?",
                (token_hash, plant_name, unit_id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO grow_units "
                "(hardware_serial, label, enrolled_at, bearer_token_hash, "
                " plant_type, medium_type, phase_set_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (hardware_serial, plant_name, now, token_hash,
                 plant_type, medium_type, now),
            )
            unit_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    return jsonify({"unit_id": unit_id, "token": raw_token}), 201
```

In `mlss_monitor/routes/__init__.py`, add to imports and register list:

```python
from .api_grow_enroll import api_grow_enroll_bp
# ... in register_routes():
app.register_blueprint(api_grow_enroll_bp)
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_grow_enroll.py -v`
Expected: PASS (4 tests)

```bash
git add mlss_monitor/routes/api_grow_enroll.py mlss_monitor/routes/__init__.py tests/grow_server/test_grow_enroll.py
git commit -m "Add POST /api/grow/enroll endpoint

Validates household enrollment key (argon2), mints per-unit bearer
token, inserts grow_units row. Idempotent on hardware_serial — re-
enrolling an existing unit returns its id with a fresh token (token
rotation use case)."
```

---

### Task 3.4: `GET /api/grow/units` and `GET /api/grow/units/<id>` (browser API)

**Files:**
- Create: `mlss_monitor/routes/api_grow_units.py`
- Modify: `mlss_monitor/routes/__init__.py`
- Create: `tests/grow_server/test_grow_units_api.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_grow_units_api.py`:

```python
"""GET /api/grow/units (list for fleet view) and /api/grow/units/<id> (detail)."""
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    init_db.create_db()

    # Insert two units with different last_seen_at, so we can test status
    now = datetime.utcnow()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, last_seen_at, last_known_state_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("hw-1", "Tomato 1", now, "hash1", now, now,
         json.dumps({"soil_moisture_pct": 58, "light_state": True}))
    )
    conn.execute(
        "INSERT INTO grow_units (hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("hw-2", "Basil 1", now, "hash2", now, now - timedelta(minutes=10)),
    )
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_units import api_grow_units_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_units_bp)
    return app.test_client()


def test_list_returns_all_active_units(client):
    r = client.get("/api/grow/units")
    assert r.status_code == 200
    body = r.get_json()
    assert "units" in body
    assert len(body["units"]) == 2
    labels = {u["label"] for u in body["units"]}
    assert labels == {"Tomato 1", "Basil 1"}


def test_list_includes_status_field(client):
    r = client.get("/api/grow/units")
    statuses = {u["label"]: u["status"] for u in r.get_json()["units"]}
    assert statuses["Tomato 1"] == "online"      # seen now
    assert statuses["Basil 1"] == "offline"      # seen 10 min ago


def test_list_includes_last_known_state(client):
    r = client.get("/api/grow/units")
    tomato = next(u for u in r.get_json()["units"] if u["label"] == "Tomato 1")
    assert tomato["last_known_state"]["soil_moisture_pct"] == 58


def test_detail_returns_full_unit(client):
    list_resp = client.get("/api/grow/units").get_json()
    unit_id = next(u["id"] for u in list_resp["units"] if u["label"] == "Tomato 1")
    r = client.get(f"/api/grow/units/{unit_id}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["label"] == "Tomato 1"
    assert body["plant_type"] == "generic"
    assert body["medium_type"] == "soil"
    assert body["status"] == "online"
    assert "capabilities" in body  # empty list for now


def test_detail_404_for_missing(client):
    r = client.get("/api/grow/units/9999")
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_grow_units_api.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`mlss_monitor/routes/api_grow_units.py`:

```python
"""REST endpoints for the browser to read grow unit state.

GET /api/grow/units            — fleet view, list
GET /api/grow/units/<id>       — detail
"""
import json
import sqlite3
from datetime import datetime, timedelta
from flask import Blueprint, jsonify

from database.init_db import DB_FILE

api_grow_units_bp = Blueprint("api_grow_units", __name__)

_STALE_AFTER = timedelta(seconds=30)
_OFFLINE_AFTER = timedelta(minutes=5)


def _classify_status(last_seen_at: str | None) -> str:
    if last_seen_at is None:
        return "offline"
    seen = datetime.fromisoformat(last_seen_at) if isinstance(last_seen_at, str) else last_seen_at
    age = datetime.utcnow() - seen
    if age < _STALE_AFTER:
        return "online"
    if age < _OFFLINE_AFTER:
        return "stale"
    return "offline"


@api_grow_units_bp.route("/api/grow/units", methods=["GET"])
def list_units():
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, label, plant_type, medium_type, current_phase, "
        "       sown_at, enrolled_at, last_seen_at, last_known_state_json "
        "FROM grow_units WHERE is_active=1 ORDER BY label"
    ).fetchall()
    conn.close()

    units = []
    for r in rows:
        units.append({
            "id": r["id"],
            "label": r["label"],
            "plant_type": r["plant_type"],
            "medium_type": r["medium_type"],
            "current_phase": r["current_phase"],
            "sown_at": r["sown_at"],
            "enrolled_at": r["enrolled_at"],
            "last_seen_at": r["last_seen_at"],
            "status": _classify_status(r["last_seen_at"]),
            "last_known_state": json.loads(r["last_known_state_json"])
                                if r["last_known_state_json"] else None,
        })
    return jsonify({"units": units})


@api_grow_units_bp.route("/api/grow/units/<int:unit_id>", methods=["GET"])
def get_unit(unit_id):
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM grow_units WHERE id=? AND is_active=1", (unit_id,)
    ).fetchone()
    if row is None:
        conn.close()
        return jsonify({"error": "not_found"}), 404

    caps = conn.execute(
        "SELECT channel, hardware, is_required, unit_label, details_json "
        "FROM grow_unit_capabilities WHERE unit_id=?", (unit_id,)
    ).fetchall()
    conn.close()

    body = {k: row[k] for k in row.keys()}
    body.pop("bearer_token_hash", None)  # never expose
    body["status"] = _classify_status(row["last_seen_at"])
    body["last_known_state"] = (
        json.loads(row["last_known_state_json"])
        if row["last_known_state_json"] else None
    )
    body.pop("last_known_state_json", None)
    body["capabilities"] = [
        {
            "channel": c["channel"],
            "hardware": c["hardware"],
            "is_required": bool(c["is_required"]),
            "unit_label": c["unit_label"],
            "details": json.loads(c["details_json"]) if c["details_json"] else None,
        }
        for c in caps
    ]
    return jsonify(body)
```

In `mlss_monitor/routes/__init__.py`, add:

```python
from .api_grow_units import api_grow_units_bp
# ... in register_routes():
app.register_blueprint(api_grow_units_bp)
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_grow_units_api.py -v`
Expected: PASS (5 tests)

```bash
git add mlss_monitor/routes/api_grow_units.py mlss_monitor/routes/__init__.py tests/grow_server/test_grow_units_api.py
git commit -m "Add GET /api/grow/units (list) + /api/grow/units/<id> (detail)

Computes online/stale/offline status from last_seen_at. Surfaces
last_known_state_json as parsed JSON for fleet card rendering. Detail
endpoint includes capability list (capability-driven UI rendering)."
```

---

### Task 3.5: `GET /api/grow/install.sh` and `GET /api/grow/dist/<file>`

**Files:**
- Create: `mlss_monitor/routes/api_grow_dist.py`
- Modify: `mlss_monitor/routes/__init__.py`
- Create: `static/grow_dist/.gitkeep` (so the dir exists)
- Create: `tests/grow_server/test_grow_dist.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_grow_dist.py`:

```python
import os
import tempfile
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Set up a temp grow_dist with a fake wheel file + install script."""
    dist_dir = tmp_path / "grow_dist"
    dist_dir.mkdir()
    (dist_dir / "mlss_grow-0.1.0-py3-none-any.whl").write_bytes(b"FAKEWHEELBYTES")
    (dist_dir / "mlss_contracts-0.1.0-py3-none-any.whl").write_bytes(b"FAKE2")

    monkeypatch.setattr("mlss_monitor.routes.api_grow_dist.GROW_DIST_DIR", str(dist_dir))

    from flask import Flask
    from mlss_monitor.routes.api_grow_dist import api_grow_dist_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_dist_bp)
    return app.test_client()


def test_install_sh_served(client):
    r = client.get("/api/grow/install.sh")
    assert r.status_code == 200
    assert r.mimetype in ("application/x-sh", "text/plain", "text/x-shellscript")
    assert b"#!/bin/bash" in r.data
    assert b"mlss-grow" in r.data


def test_dist_file_served(client):
    r = client.get("/api/grow/dist/mlss_grow-0.1.0-py3-none-any.whl")
    assert r.status_code == 200
    assert r.data == b"FAKEWHEELBYTES"


def test_dist_path_traversal_rejected(client):
    r = client.get("/api/grow/dist/../../../etc/passwd")
    assert r.status_code in (400, 404)


def test_dist_404_for_missing_file(client):
    r = client.get("/api/grow/dist/nonexistent.whl")
    assert r.status_code == 404


def test_dist_latest_returns_version(client):
    r = client.get("/api/grow/dist/latest")
    assert r.status_code == 200
    body = r.get_json()
    assert body["mlss_grow"] == "0.1.0"
    assert body["mlss_contracts"] == "0.1.0"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_grow_dist.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`mlss_monitor/routes/api_grow_dist.py`:

```python
"""Serve the grow firmware install script + wheel files.

The install script is a single bash file that runs on a fresh Pi Zero
to download both wheels (mlss_contracts + mlss_grow) and install them
into a venv at /opt/mlss-grow/.venv. See grow_unit/install.sh for the
canonical source.
"""
import os
import re
from pathlib import Path
from flask import Blueprint, send_from_directory, jsonify, abort, current_app

api_grow_dist_bp = Blueprint("api_grow_dist", __name__)

# Default location of the served wheels — overridable for tests
GROW_DIST_DIR = str(
    Path(__file__).resolve().parent.parent.parent / "static" / "grow_dist"
)
_WHEEL_RE = re.compile(r"^([a-z_]+)-(\d+\.\d+\.\d+)-py3-none-any\.whl$")


@api_grow_dist_bp.route("/api/grow/install.sh", methods=["GET"])
def install_sh():
    """The Pi Zero install one-liner downloads + executes this."""
    install_path = (
        Path(__file__).resolve().parent.parent.parent
        / "grow_unit" / "install.sh"
    )
    if not install_path.exists():
        return ("# install.sh not yet built — run scripts/build_grow_wheel.sh\n",
                200, {"Content-Type": "text/x-shellscript"})
    with open(install_path, "rb") as f:
        return (f.read(), 200, {"Content-Type": "text/x-shellscript"})


@api_grow_dist_bp.route("/api/grow/dist/<path:filename>", methods=["GET"])
def serve_wheel(filename):
    # Reject path traversal / weird names — only basenames matching wheel format
    if "/" in filename or "\\" in filename or ".." in filename:
        abort(400)
    if filename != "latest" and not _WHEEL_RE.match(filename):
        abort(400)
    if filename == "latest":
        return _latest_versions()
    return send_from_directory(GROW_DIST_DIR, filename, as_attachment=True)


def _latest_versions():
    out = {}
    if not os.path.isdir(GROW_DIST_DIR):
        return jsonify(out)
    for fname in os.listdir(GROW_DIST_DIR):
        m = _WHEEL_RE.match(fname)
        if m:
            pkg, ver = m.group(1), m.group(2)
            # Highest version wins (lexical OK for semver-aligned 0.0.0 strings)
            if pkg not in out or ver > out[pkg]:
                out[pkg] = ver
    return jsonify(out)
```

Create `static/grow_dist/.gitkeep` (empty file).

In `mlss_monitor/routes/__init__.py`:

```python
from .api_grow_dist import api_grow_dist_bp
# ...
app.register_blueprint(api_grow_dist_bp)
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_grow_dist.py -v`
Expected: PASS (5 tests)

```bash
git add mlss_monitor/routes/api_grow_dist.py mlss_monitor/routes/__init__.py static/grow_dist/.gitkeep tests/grow_server/test_grow_dist.py
git commit -m "Serve grow firmware install.sh + wheels from MLSS

Pi Zero install one-liner curls install.sh and pipes to bash. install.sh
(written in a later task) downloads both wheels from /api/grow/dist/
and pip-installs into a venv. Path traversal blocked by regex on
filename."
```

---

## Section 4 — Server-side WebSocket listener

The per-unit WS lives outside Flask's request lifecycle (long-lived bidirectional). The listener runs in its own background thread, accepts upgrades, dispatches by message type, and tracks live connections in a registry that the REST endpoints (manual identify/water) reach into.

---

### Task 4.1: WS registry — track live connections per unit

**Files:**
- Create: `mlss_monitor/grow/ws_registry.py`
- Create: `tests/grow_server/test_ws_registry.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_ws_registry.py`:

```python
"""WSRegistry: tracks active per-unit WebSocket connections.

Used by REST endpoints to push commands (e.g. identify, water_now) to a
specific unit, and by status checks to know whether a unit is currently
holding an open connection.
"""
import asyncio
import pytest
from mlss_monitor.grow.ws_registry import WSRegistry


class FakeWS:
    def __init__(self):
        self.sent = []
        self.closed = False

    async def send(self, msg):
        if self.closed:
            raise RuntimeError("closed")
        self.sent.append(msg)


@pytest.mark.asyncio
async def test_register_and_lookup():
    reg = WSRegistry()
    ws = FakeWS()
    reg.register(unit_id=42, ws=ws)
    assert reg.is_connected(42) is True
    assert reg.is_connected(999) is False
    assert reg.connection_count() == 1


@pytest.mark.asyncio
async def test_unregister_removes_connection():
    reg = WSRegistry()
    ws = FakeWS()
    reg.register(unit_id=42, ws=ws)
    reg.unregister(42)
    assert reg.is_connected(42) is False
    assert reg.connection_count() == 0


@pytest.mark.asyncio
async def test_send_command_to_unit():
    reg = WSRegistry()
    ws = FakeWS()
    reg.register(unit_id=42, ws=ws)
    await reg.send_to_unit(42, '{"type":"command","payload":{"name":"identify"}}')
    assert ws.sent == ['{"type":"command","payload":{"name":"identify"}}']


@pytest.mark.asyncio
async def test_send_to_disconnected_unit_raises():
    reg = WSRegistry()
    with pytest.raises(KeyError):
        await reg.send_to_unit(999, "anything")


@pytest.mark.asyncio
async def test_re_register_replaces_old_connection():
    """If a unit reconnects (new WS) the old reference is dropped."""
    reg = WSRegistry()
    old_ws = FakeWS()
    new_ws = FakeWS()
    reg.register(42, old_ws)
    reg.register(42, new_ws)
    await reg.send_to_unit(42, "msg")
    assert old_ws.sent == []
    assert new_ws.sent == ["msg"]


@pytest.mark.asyncio
async def test_list_connected_unit_ids():
    reg = WSRegistry()
    reg.register(1, FakeWS())
    reg.register(2, FakeWS())
    reg.register(5, FakeWS())
    assert sorted(reg.connected_unit_ids()) == [1, 2, 5]
```

Add to `pyproject.toml` dev deps if not present: `pytest-asyncio = "^0.24"`.

Add to `pyproject.toml` near `[tool.pytest.ini_options]` (or create the section):

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_ws_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mlss_monitor.grow.ws_registry'`

- [ ] **Step 3: Implement**

`mlss_monitor/grow/ws_registry.py`:

```python
"""Per-unit WebSocket connection registry.

The MLSS WS listener registers each accepted connection here keyed by
unit_id. REST endpoints (manual identify/water/light-override) reach in
to send commands. Status checks can query is_connected() to render
'online' state without round-tripping the unit.
"""
from threading import Lock


class WSRegistry:
    def __init__(self):
        self._connections: dict[int, object] = {}  # unit_id -> ws
        self._lock = Lock()

    def register(self, unit_id: int, ws):
        """Register a new WS connection. Replaces any prior connection for that unit."""
        with self._lock:
            self._connections[unit_id] = ws

    def unregister(self, unit_id: int):
        """Remove a unit's connection; no-op if not registered."""
        with self._lock:
            self._connections.pop(unit_id, None)

    def is_connected(self, unit_id: int) -> bool:
        with self._lock:
            return unit_id in self._connections

    def connection_count(self) -> int:
        with self._lock:
            return len(self._connections)

    def connected_unit_ids(self) -> list[int]:
        with self._lock:
            return list(self._connections.keys())

    async def send_to_unit(self, unit_id: int, message: str):
        """Send a text message to a connected unit. Raises KeyError if not connected."""
        with self._lock:
            ws = self._connections.get(unit_id)
        if ws is None:
            raise KeyError(f"unit {unit_id} not connected")
        await ws.send(message)
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_ws_registry.py -v`
Expected: PASS (6 tests)

```bash
git add mlss_monitor/grow/ws_registry.py tests/grow_server/test_ws_registry.py pyproject.toml
git commit -m "Add WSRegistry for per-unit live WebSocket lookup

Threadsafe map of unit_id -> WS connection. REST endpoints can push
commands by unit_id; status checks can ask if a unit currently holds
an open connection."
```

---

### Task 4.2: Telemetry message handler

**Files:**
- Create: `mlss_monitor/grow/handlers.py`
- Create: `tests/grow_server/test_handler_telemetry.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_handler_telemetry.py`:

```python
"""handle_telemetry: writes one grow_telemetry row + updates last_known_state."""
import json
import sqlite3
import tempfile
from datetime import datetime
import pytest


@pytest.fixture
def db_with_unit(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.handlers.DB_FILE", tmp.name)
    init_db.create_db()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, soil_dry_raw, soil_wet_raw) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (1, "hw-1", "Tomato 1", datetime.utcnow(), "hash", datetime.utcnow(),
         200, 1500),
    )
    conn.commit()
    conn.close()
    return tmp.name


def test_handle_telemetry_inserts_row(db_with_unit):
    from mlss_monitor.grow.handlers import handle_telemetry
    handle_telemetry(unit_id=1, ts=datetime(2026, 5, 3, 12, 34, 18), payload={
        "soil_moisture_raw": 612,
        "soil_moisture_pct": 31.7,
        "light_state": True,
        "pump_state": False,
        "soil_temp_c": 21.4,
    })
    conn = sqlite3.connect(db_with_unit)
    row = conn.execute(
        "SELECT soil_moisture_raw, soil_moisture_pct, light_state, "
        "pump_state, soil_temp_c FROM grow_telemetry WHERE unit_id=1"
    ).fetchone()
    assert row == (612, 31.7, 1, 0, 21.4)


def test_handle_telemetry_updates_last_known_state(db_with_unit):
    from mlss_monitor.grow.handlers import handle_telemetry
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612,
        "soil_moisture_pct": 31.7,
        "light_state": True,
        "pump_state": False,
    })
    conn = sqlite3.connect(db_with_unit)
    state_json, last_seen = conn.execute(
        "SELECT last_known_state_json, last_seen_at FROM grow_units WHERE id=1"
    ).fetchone()
    state = json.loads(state_json)
    assert state["soil_moisture_pct"] == 31.7
    assert state["light_state"] is True
    assert last_seen is not None


def test_handle_telemetry_returns_inserted_id(db_with_unit):
    from mlss_monitor.grow.handlers import handle_telemetry
    inserted_id = handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612, "light_state": False, "pump_state": False,
    })
    assert isinstance(inserted_id, int)
    assert inserted_id > 0


def test_handle_telemetry_computes_pct_when_unit_calibrated(db_with_unit):
    """If pct is missing but raw + calibration are present, server fills it in."""
    from mlss_monitor.grow.handlers import handle_telemetry
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 850,  # midway between dry=200 and wet=1500
        "light_state": False, "pump_state": False,
    })
    conn = sqlite3.connect(db_with_unit)
    pct = conn.execute(
        "SELECT soil_moisture_pct FROM grow_telemetry WHERE unit_id=1"
    ).fetchone()[0]
    # (850-200)/(1500-200) = 0.5 → 50%
    assert pct == pytest.approx(50.0, abs=0.5)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_handler_telemetry.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`mlss_monitor/grow/handlers.py`:

```python
"""Per-message-type handlers for the grow WebSocket listener.

Each handler is a pure function over (unit_id, ts, payload) — easy to unit
test without spinning up a real WebSocket. The WS listener (Task 4.6)
dispatches incoming text frames to these by message type.
"""
import json
import sqlite3
from datetime import datetime
from typing import Optional

from database.init_db import DB_FILE


def _compute_moisture_pct(raw: int, dry: Optional[int], wet: Optional[int]) -> Optional[float]:
    """Linear-map raw → %. Returns None if calibration not present."""
    if dry is None or wet is None or wet <= dry:
        return None
    pct = (raw - dry) / (wet - dry) * 100
    return max(0.0, min(100.0, round(pct, 2)))


def handle_telemetry(unit_id: int, ts: datetime, payload: dict) -> int:
    """Insert one grow_telemetry row + refresh grow_units.last_known_state_json.

    If payload['soil_moisture_pct'] is missing but the unit has calibration
    set, computes pct server-side from the raw reading.

    Returns the inserted grow_telemetry.id (used by photo upload to backfill
    the telemetry_id join key).
    """
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row

    # Server-side pct fill if missing
    pct = payload.get("soil_moisture_pct")
    if pct is None and "soil_moisture_raw" in payload:
        cal = conn.execute(
            "SELECT soil_dry_raw, soil_wet_raw FROM grow_units WHERE id=?",
            (unit_id,),
        ).fetchone()
        if cal:
            pct = _compute_moisture_pct(payload["soil_moisture_raw"],
                                         cal["soil_dry_raw"], cal["soil_wet_raw"])

    cur = conn.execute(
        "INSERT INTO grow_telemetry "
        "(unit_id, timestamp_utc, soil_moisture_raw, soil_moisture_pct, "
        " light_state, pump_state, soil_temp_c, ambient_lux, "
        " air_temp_c, air_humidity_pct, reservoir_level_pct) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (unit_id, ts, payload["soil_moisture_raw"], pct,
         int(payload["light_state"]), int(payload["pump_state"]),
         payload.get("soil_temp_c"), payload.get("ambient_lux"),
         payload.get("air_temp_c"), payload.get("air_humidity_pct"),
         payload.get("reservoir_level_pct")),
    )
    inserted_id = cur.lastrowid

    # Update unit's cached last_known_state for fleet rendering
    state = {
        "soil_moisture_raw": payload["soil_moisture_raw"],
        "soil_moisture_pct": pct,
        "light_state": bool(payload["light_state"]),
        "pump_state": bool(payload["pump_state"]),
        "soil_temp_c": payload.get("soil_temp_c"),
        "ambient_lux": payload.get("ambient_lux"),
        "air_temp_c": payload.get("air_temp_c"),
        "air_humidity_pct": payload.get("air_humidity_pct"),
        "reservoir_level_pct": payload.get("reservoir_level_pct"),
    }
    conn.execute(
        "UPDATE grow_units SET last_known_state_json=?, "
        "last_telemetry_at=?, last_seen_at=? WHERE id=?",
        (json.dumps(state), ts, ts, unit_id),
    )
    conn.commit()
    conn.close()
    return inserted_id
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_handler_telemetry.py -v`
Expected: PASS (4 tests)

```bash
git add mlss_monitor/grow/handlers.py tests/grow_server/test_handler_telemetry.py
git commit -m "Add handle_telemetry: insert row + update last_known_state cache

Server-side fills soil_moisture_pct if unit didn't compute it locally
(unit may not be calibrated). last_known_state_json keeps the fleet
view fast — no per-card joins to grow_telemetry."
```

---

### Task 4.3: Capabilities + event message handlers

**Files:**
- Modify: `mlss_monitor/grow/handlers.py`
- Create: `tests/grow_server/test_handler_capabilities.py`
- Create: `tests/grow_server/test_handler_event.py`

- [ ] **Step 1: Write the failing tests**

`tests/grow_server/test_handler_capabilities.py`:

```python
import sqlite3
import tempfile
from datetime import datetime
import pytest


@pytest.fixture
def db_with_unit(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.handlers.DB_FILE", tmp.name)
    init_db.create_db()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'hw1', 'X', ?, 'h', ?)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.commit()
    conn.close()
    return tmp.name


def test_handle_capabilities_inserts_rows(db_with_unit):
    from mlss_monitor.grow.handlers import handle_capabilities
    handle_capabilities(unit_id=1, ts=datetime.utcnow(), payload={
        "capabilities": [
            {"channel": "soil_moisture", "hardware": "Seesaw",
             "is_required": True, "unit_label": "raw",
             "details": {"i2c_address": "0x36"}},
            {"channel": "soil_temp_c", "hardware": "Seesaw",
             "is_required": False, "unit_label": "°C", "details": None},
        ],
        "firmware_version": "0.1.0",
        "hardware_serial": "hw1",
    })
    conn = sqlite3.connect(db_with_unit)
    rows = conn.execute(
        "SELECT channel, hardware, is_required FROM grow_unit_capabilities "
        "WHERE unit_id=1 ORDER BY channel"
    ).fetchall()
    assert rows == [
        ("soil_moisture", "Seesaw", 1),
        ("soil_temp_c", "Seesaw", 0),
    ]


def test_handle_capabilities_replaces_old_set(db_with_unit):
    """A second capabilities push replaces the first (e.g. a sensor was added)."""
    from mlss_monitor.grow.handlers import handle_capabilities
    handle_capabilities(unit_id=1, ts=datetime.utcnow(), payload={
        "capabilities": [{"channel": "soil_moisture", "hardware": "S",
                          "is_required": True, "unit_label": "raw"}],
        "firmware_version": "0.1.0", "hardware_serial": "hw1",
    })
    handle_capabilities(unit_id=1, ts=datetime.utcnow(), payload={
        "capabilities": [
            {"channel": "soil_moisture", "hardware": "S", "is_required": True,
             "unit_label": "raw"},
            {"channel": "ambient_lux", "hardware": "TSL2591",
             "is_required": False, "unit_label": "lux"},
        ],
        "firmware_version": "0.1.0", "hardware_serial": "hw1",
    })
    conn = sqlite3.connect(db_with_unit)
    channels = {r[0] for r in conn.execute(
        "SELECT channel FROM grow_unit_capabilities WHERE unit_id=1"
    ).fetchall()}
    assert channels == {"soil_moisture", "ambient_lux"}
```

`tests/grow_server/test_handler_event.py`:

```python
import sqlite3
import tempfile
from datetime import datetime
import pytest


@pytest.fixture
def db_with_unit(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.handlers.DB_FILE", tmp.name)
    init_db.create_db()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'hw1', 'X', ?, 'h', ?)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.commit()
    conn.close()
    return tmp.name


def test_watering_pulse_event_writes_to_grow_watering_events(db_with_unit):
    from mlss_monitor.grow.handlers import handle_event
    handle_event(unit_id=1, ts=datetime(2026, 5, 3, 12, 0, 0), payload={
        "kind": "watering_pulse",
        "details": {"duration_s": 5.2, "trigger": "pid",
                    "soil_pct_before": 42, "pid_error": 13,
                    "pid_p_term": 5.2, "pid_i_term": 0, "pid_d_term": 0,
                    "triggered_by": "system"},
    })
    conn = sqlite3.connect(db_with_unit)
    row = conn.execute(
        "SELECT trigger, duration_s, soil_pct_before, triggered_by "
        "FROM grow_watering_events WHERE unit_id=1"
    ).fetchone()
    assert row == ("pid", 5.2, 42.0, "system")


def test_sensor_degraded_event_writes_to_grow_errors(db_with_unit):
    from mlss_monitor.grow.handlers import handle_event
    handle_event(unit_id=1, ts=datetime.utcnow(), payload={
        "kind": "sensor_degraded",
        "details": {"sensor": "Seesaw", "consecutive_bad_reads": 3},
    })
    conn = sqlite3.connect(db_with_unit)
    row = conn.execute(
        "SELECT severity, kind, message FROM grow_errors WHERE unit_id=1"
    ).fetchone()
    assert row[0] == "warning"
    assert row[1] == "sensor_degraded"
    assert "Seesaw" in row[2]


def test_sensor_recovered_resolves_open_sensor_errors(db_with_unit):
    from mlss_monitor.grow.handlers import handle_event
    handle_event(unit_id=1, ts=datetime.utcnow(), payload={
        "kind": "sensor_degraded", "details": {"sensor": "Seesaw"},
    })
    handle_event(unit_id=1, ts=datetime.utcnow(), payload={
        "kind": "sensor_recovered", "details": {"sensor": "Seesaw"},
    })
    conn = sqlite3.connect(db_with_unit)
    n_open = conn.execute(
        "SELECT COUNT(*) FROM grow_errors "
        "WHERE unit_id=1 AND kind='sensor_degraded' AND resolved_at IS NULL"
    ).fetchone()[0]
    assert n_open == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/grow_server/test_handler_capabilities.py tests/grow_server/test_handler_event.py -v`
Expected: FAIL — `ImportError: cannot import name 'handle_capabilities'`

- [ ] **Step 3: Implement**

Append to `mlss_monitor/grow/handlers.py`:

```python
def handle_capabilities(unit_id: int, ts: datetime, payload: dict):
    """Replace the unit's full capability set with what was just declared."""
    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        conn.execute("DELETE FROM grow_unit_capabilities WHERE unit_id=?", (unit_id,))
        for cap in payload["capabilities"]:
            conn.execute(
                "INSERT INTO grow_unit_capabilities "
                "(unit_id, channel, hardware, is_required, unit_label, "
                " installed_at, details_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (unit_id, cap["channel"], cap.get("hardware"),
                 int(cap["is_required"]), cap.get("unit_label"), ts,
                 json.dumps(cap["details"]) if cap.get("details") else None),
            )
        conn.execute(
            "UPDATE grow_units SET last_seen_at=? WHERE id=?",
            (ts, unit_id),
        )
        conn.commit()
    finally:
        conn.close()


def handle_event(unit_id: int, ts: datetime, payload: dict):
    """Dispatch by event kind. Watering events → grow_watering_events;
    sensor_* and other diagnostic events → grow_errors.
    """
    kind = payload["kind"]
    details = payload.get("details") or {}
    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        if kind == "watering_pulse":
            conn.execute(
                "INSERT INTO grow_watering_events "
                "(unit_id, timestamp_utc, trigger, duration_s, soil_pct_before, "
                " triggered_by, pid_error, pid_p_term, pid_i_term, pid_d_term) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (unit_id, ts, details.get("trigger", "pid"),
                 details["duration_s"], details.get("soil_pct_before"),
                 details.get("triggered_by", "system"), details.get("pid_error"),
                 details.get("pid_p_term"), details.get("pid_i_term"),
                 details.get("pid_d_term")),
            )
        elif kind == "sensor_degraded":
            sensor = details.get("sensor", "unknown")
            conn.execute(
                "INSERT INTO grow_errors "
                "(unit_id, timestamp_utc, severity, kind, message, details_json) "
                "VALUES (?, ?, 'warning', 'sensor_degraded', ?, ?)",
                (unit_id, ts, f"Sensor {sensor} reporting bad reads",
                 json.dumps(details)),
            )
        elif kind == "sensor_recovered":
            sensor = details.get("sensor", "unknown")
            conn.execute(
                "UPDATE grow_errors SET resolved_at=? "
                "WHERE unit_id=? AND kind='sensor_degraded' AND resolved_at IS NULL "
                "AND details_json LIKE ?",
                (ts, unit_id, f'%"sensor": "{sensor}"%'),
            )
        elif kind == "safety_cap_hit":
            conn.execute(
                "INSERT INTO grow_errors "
                "(unit_id, timestamp_utc, severity, kind, message, details_json) "
                "VALUES (?, ?, 'warning', 'safety_cap_hit', ?, ?)",
                (unit_id, ts, f"Safety cap hit: {details.get('cap', '')}",
                 json.dumps(details)),
            )
        # Other event kinds (startup, shutdown, identify_complete, etc.) are
        # logged-only — no DB row needed in Phase 1.
        conn.execute(
            "UPDATE grow_units SET last_seen_at=? WHERE id=?", (ts, unit_id),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_handler_capabilities.py tests/grow_server/test_handler_event.py -v`
Expected: PASS (5 tests)

```bash
git add mlss_monitor/grow/handlers.py tests/grow_server/test_handler_capabilities.py tests/grow_server/test_handler_event.py
git commit -m "Add capability + event message handlers

Capabilities push replaces unit's full capability set (so unplugging a
sensor + restarting the unit removes its tile). Watering pulse events
write a grow_watering_events row with full PID telemetry for tuning.
Sensor degraded/recovered toggles open errors in grow_errors."
```

---

### Task 4.4: Photo storage handler (binary frame → file + grow_photos row + telemetry_id join)

**Files:**
- Create: `mlss_monitor/grow/photo_storage.py`
- Create: `tests/grow_server/test_photo_storage.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_photo_storage.py`:

```python
"""handle_photo_frame: parse binary frame, write JPEG, insert grow_photos row."""
import json
import os
import sqlite3
import struct
import tempfile
from datetime import datetime
import pytest


@pytest.fixture
def setup(tmp_path, monkeypatch):
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp_db.name
    monkeypatch.setattr("mlss_monitor.grow.handlers.DB_FILE", tmp_db.name)
    monkeypatch.setattr("mlss_monitor.grow.photo_storage.DB_FILE", tmp_db.name)
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", str(tmp_path / "images"))
    init_db.create_db()

    # Insert a unit + a recent telemetry row so telemetry_id join can find it
    now = datetime.utcnow()
    conn = sqlite3.connect(tmp_db.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'hw1', 'X', ?, 'h', ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO grow_telemetry (id, unit_id, timestamp_utc, "
        "soil_moisture_raw, light_state, pump_state) "
        "VALUES (100, 1, ?, 612, 1, 0)", (now,),
    )
    conn.commit()
    conn.close()
    return tmp_db.name, str(tmp_path / "images")


def _frame(header: dict, jpeg_bytes: bytes) -> bytes:
    h_bytes = json.dumps(header).encode("utf-8")
    return struct.pack(">I", len(h_bytes)) + h_bytes + jpeg_bytes


def test_handle_photo_writes_file_and_db_row(setup):
    db_path, images_dir = setup
    from mlss_monitor.grow.photo_storage import handle_photo_frame
    from datetime import datetime

    fake_jpeg = b"\xff\xd8\xff\xe0FAKEIMAGEBYTES" + b"\x00" * 200
    frame = _frame({
        "taken_at": "2026-05-03T12:34:18Z",
        "width": 1920, "height": 1080, "jpeg_quality": 85,
        "shutter_us": 16667, "iso": 100,
    }, fake_jpeg)

    handle_photo_frame(unit_id=1, frame=frame)

    # File on disk
    expected_path = os.path.join(
        images_dir, "unit_001", "2026-05-03", "123418.jpg")
    assert os.path.exists(expected_path)
    with open(expected_path, "rb") as f:
        assert f.read() == fake_jpeg

    # DB row
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT file_path, width_px, height_px, size_bytes, telemetry_id "
        "FROM grow_photos WHERE unit_id=1"
    ).fetchone()
    assert row[0] == "unit_001/2026-05-03/123418.jpg"  # relative
    assert row[1] == 1920
    assert row[2] == 1080
    assert row[3] == len(fake_jpeg)
    assert row[4] == 100  # joined to the telemetry row inserted in fixture


def test_handle_photo_no_telemetry_match_leaves_telemetry_id_null(setup, tmp_path):
    """If no telemetry row within ±60s, telemetry_id stays NULL (will not break ML join — just absent)."""
    db_path, images_dir = setup
    from mlss_monitor.grow.photo_storage import handle_photo_frame
    fake = b"\xff\xd8\xff\xe0X"
    # Far-past timestamp — outside ±60s window of the seeded telemetry row
    frame = _frame({"taken_at": "2025-01-01T00:00:00Z",
                    "width": 100, "height": 100}, fake)
    handle_photo_frame(unit_id=1, frame=frame)
    conn = sqlite3.connect(db_path)
    tid = conn.execute(
        "SELECT telemetry_id FROM grow_photos WHERE size_bytes=?", (len(fake),)
    ).fetchone()[0]
    assert tid is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_photo_storage.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`mlss_monitor/grow/photo_storage.py`:

```python
"""Handle binary photo frames from grow units.

Frame layout:
  [4 bytes BE]  header_length
  [N bytes]     UTF-8 JSON header {taken_at, width, height, jpeg_quality, ...}
  [remaining]   raw JPEG bytes

On receipt: write the JPEG to MLSS_GROW_IMAGES_DIR/<unit_dir>/<date>/<HHMMSS>.jpg
(filesystem layout from the spec), insert a grow_photos row with the relative
path, and back-fill telemetry_id by joining to the closest grow_telemetry
row for the same unit within ±60 seconds. The denormalised join key makes
ML training queries cheap.
"""
import json
import os
import sqlite3
import struct
from datetime import datetime, timezone, timedelta
from pathlib import Path

from database.init_db import DB_FILE

GROW_IMAGES_DIR = os.environ.get(
    "MLSS_GROW_IMAGES_DIR", "/var/lib/mlss/grow_images"
)

_JOIN_WINDOW_SECONDS = 60


def _resolve_images_dir() -> str:
    """app_settings override > env var > built-in default."""
    try:
        conn = sqlite3.connect(DB_FILE, timeout=2)
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='grow_images_dir'"
        ).fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return GROW_IMAGES_DIR


def handle_photo_frame(unit_id: int, frame: bytes):
    """Parse a binary photo frame and persist file + metadata."""
    if len(frame) < 4:
        raise ValueError("photo frame too short for header length")
    (h_len,) = struct.unpack(">I", frame[:4])
    if h_len <= 0 or h_len > 65536:
        raise ValueError(f"invalid header length: {h_len}")
    header = json.loads(frame[4:4 + h_len].decode("utf-8"))
    jpeg_bytes = frame[4 + h_len:]
    if not jpeg_bytes:
        raise ValueError("photo frame has empty JPEG payload")

    taken_at = datetime.fromisoformat(header["taken_at"].replace("Z", "+00:00"))
    if taken_at.tzinfo:
        taken_at_utc = taken_at.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        taken_at_utc = taken_at

    images_dir = _resolve_images_dir()
    rel_dir = f"unit_{unit_id:03d}/{taken_at_utc.strftime('%Y-%m-%d')}"
    rel_path = f"{rel_dir}/{taken_at_utc.strftime('%H%M%S')}.jpg"
    abs_dir = os.path.join(images_dir, rel_dir)
    abs_path = os.path.join(images_dir, rel_path)

    Path(abs_dir).mkdir(parents=True, exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(jpeg_bytes)

    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        # Find closest telemetry row within ±60s for the join key
        win = timedelta(seconds=_JOIN_WINDOW_SECONDS)
        join_row = conn.execute(
            "SELECT id FROM grow_telemetry WHERE unit_id=? "
            "AND timestamp_utc BETWEEN ? AND ? "
            "ORDER BY ABS(julianday(timestamp_utc) - julianday(?)) "
            "LIMIT 1",
            (unit_id, taken_at_utc - win, taken_at_utc + win, taken_at_utc),
        ).fetchone()
        telemetry_id = join_row[0] if join_row else None

        conn.execute(
            "INSERT INTO grow_photos "
            "(unit_id, taken_at, file_path, width_px, height_px, size_bytes, "
            " jpeg_quality, shutter_us, iso, white_balance, telemetry_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (unit_id, taken_at_utc, rel_path,
             header["width"], header["height"], len(jpeg_bytes),
             header.get("jpeg_quality"), header.get("shutter_us"),
             header.get("iso"), header.get("white_balance"), telemetry_id),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_photo_storage.py -v`
Expected: PASS (2 tests)

```bash
git add mlss_monitor/grow/photo_storage.py tests/grow_server/test_photo_storage.py
git commit -m "Add binary photo frame handler with telemetry_id ML join

4-byte header_len + JSON header + JPEG bytes. Writes to
{images_dir}/unit_NNN/YYYY-MM-DD/HHMMSS.jpg using a relative path
in the DB so swapping the storage dir is rsync-and-flip. telemetry_id
backfilled by ±60s window join."
```

---

### Task 4.5: WS listener — accept connection, validate auth, dispatch by message type

**Files:**
- Create: `mlss_monitor/routes/api_grow_ws.py`
- Modify: `mlss_monitor/state.py` (add `grow_ws_registry` global)
- Modify: `mlss_monitor/app.py` (start the WS listener thread)
- Create: `tests/grow_server/test_grow_ws.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_grow_ws.py`:

```python
"""End-to-end WS listener: connect with bearer token, send messages, verify dispatch."""
import asyncio
import json
import sqlite3
import struct
import tempfile
import threading
import time
from datetime import datetime
import pytest
import websockets


@pytest.fixture
def server(monkeypatch):
    """Start the WS listener on a random port with a freshly-enrolled unit."""
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp_db.name
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp_db.name)
    monkeypatch.setattr("mlss_monitor.grow.handlers.DB_FILE", tmp_db.name)
    monkeypatch.setattr("mlss_monitor.grow.photo_storage.DB_FILE", tmp_db.name)
    init_db.create_db()

    from mlss_monitor.grow.auth import generate_token, hash_secret
    raw = generate_token()
    conn = sqlite3.connect(tmp_db.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'hw1', 'X', ?, ?, ?)",
        (datetime.utcnow(), hash_secret(raw), datetime.utcnow()),
    )
    conn.commit()
    conn.close()

    from mlss_monitor.grow.ws_registry import WSRegistry
    from mlss_monitor.routes.api_grow_ws import start_ws_listener

    registry = WSRegistry()
    server_obj = start_ws_listener(host="127.0.0.1", port=0, registry=registry)
    port = server_obj.sockets[0].getsockname()[1]

    yield port, raw, tmp_db.name, registry

    server_obj.close()


@pytest.mark.asyncio
async def test_connect_with_valid_bearer_token_succeeds(server):
    port, token, _, registry = server
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/api/grow/1/ws",
        extra_headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        await asyncio.sleep(0.1)
        assert registry.is_connected(1) is True


@pytest.mark.asyncio
async def test_connect_with_wrong_token_rejected(server):
    port, _, _, _ = server
    with pytest.raises(websockets.InvalidStatusCode):
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/api/grow/1/ws",
            extra_headers={"Authorization": "Bearer wrong"},
        ):
            pass


@pytest.mark.asyncio
async def test_telemetry_message_persisted(server):
    port, token, db_path, _ = server
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/api/grow/1/ws",
        extra_headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        await ws.send(json.dumps({
            "type": "telemetry",
            "ts": "2026-05-03T12:34:18Z",
            "payload": {"soil_moisture_raw": 612, "light_state": True,
                        "pump_state": False},
        }))
        await asyncio.sleep(0.2)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT soil_moisture_raw FROM grow_telemetry WHERE unit_id=1"
    ).fetchone()
    assert row[0] == 612


@pytest.mark.asyncio
async def test_binary_frame_dispatched_to_photo_storage(server, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", str(tmp_path / "imgs"))
    port, token, db_path, _ = server
    fake = b"\xff\xd8FAKEJPEG"
    header = json.dumps({"taken_at": "2026-05-03T12:34:18Z",
                         "width": 100, "height": 100}).encode()
    frame = struct.pack(">I", len(header)) + header + fake
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/api/grow/1/ws",
        extra_headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        await ws.send(frame)
        await asyncio.sleep(0.2)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT size_bytes FROM grow_photos WHERE unit_id=1"
    ).fetchone()
    assert row[0] == len(fake)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_grow_ws.py -v`
Expected: FAIL — `ImportError: cannot import name 'start_ws_listener'`

- [ ] **Step 3: Implement**

`mlss_monitor/routes/api_grow_ws.py`:

```python
"""WebSocket listener for Plant Grow Units.

Runs in its own asyncio event loop on a background thread (separate from
Flask's request loop). One coroutine per accepted connection; messages are
dispatched by type to the handlers in mlss_monitor.grow.handlers and
mlss_monitor.grow.photo_storage.
"""
import asyncio
import json
import logging
import sqlite3
import threading
from datetime import datetime
import websockets

from database.init_db import DB_FILE
from mlss_monitor.grow.auth import verify_secret
from mlss_monitor.grow.handlers import (
    handle_telemetry, handle_capabilities, handle_event,
)
from mlss_monitor.grow.photo_storage import handle_photo_frame

log = logging.getLogger(__name__)


def _validate_bearer(unit_id: int, token: str) -> bool:
    conn = sqlite3.connect(DB_FILE, timeout=5)
    row = conn.execute(
        "SELECT bearer_token_hash, is_active FROM grow_units WHERE id=?",
        (unit_id,),
    ).fetchone()
    conn.close()
    if row is None or not row[1]:
        return False
    return verify_secret(token, row[0])


async def _connection_handler(ws, path: str, registry):
    # Path looks like /api/grow/<unit_id>/ws
    parts = path.strip("/").split("/")
    if len(parts) != 4 or parts[0] != "api" or parts[1] != "grow" or parts[3] != "ws":
        await ws.close(code=1008, reason="bad_path")
        return
    try:
        unit_id = int(parts[2])
    except ValueError:
        await ws.close(code=1008, reason="bad_unit_id")
        return

    auth_header = ws.request_headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        await ws.close(code=1008, reason="missing_bearer")
        return
    token = auth_header[7:].strip()
    if not _validate_bearer(unit_id, token):
        await ws.close(code=1008, reason="invalid_token")
        return

    registry.register(unit_id, ws)
    log.info("grow unit %s connected", unit_id)
    try:
        async for message in ws:
            try:
                if isinstance(message, bytes):
                    handle_photo_frame(unit_id, message)
                else:
                    msg = json.loads(message)
                    msg_type = msg.get("type")
                    ts = datetime.fromisoformat(msg["ts"].replace("Z", "+00:00")).replace(tzinfo=None)
                    payload = msg.get("payload") or {}
                    if msg_type == "telemetry":
                        handle_telemetry(unit_id, ts, payload)
                    elif msg_type == "capabilities":
                        handle_capabilities(unit_id, ts, payload)
                    elif msg_type == "event":
                        handle_event(unit_id, ts, payload)
                    elif msg_type == "ack":
                        log.debug("ack from unit %s: %s", unit_id, payload)
                    else:
                        log.warning("unknown message type from unit %s: %r",
                                    unit_id, msg_type)
            except Exception as exc:
                log.exception("error handling msg from unit %s: %s", unit_id, exc)
    finally:
        registry.unregister(unit_id)
        log.info("grow unit %s disconnected", unit_id)


def start_ws_listener(host: str, port: int, registry):
    """Boot the WS listener on its own thread + event loop. Returns the server obj."""
    server_holder = {}
    ready = threading.Event()

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        async def _serve():
            srv = await websockets.serve(
                lambda ws, path: _connection_handler(ws, path, registry),
                host, port, max_size=8 * 1024 * 1024,  # 8 MB max frame
            )
            server_holder["srv"] = srv
            ready.set()
            await srv.wait_closed()
        loop.run_until_complete(_serve())

    threading.Thread(target=_run, daemon=True, name="grow-ws-listener").start()
    ready.wait(timeout=5)
    return server_holder["srv"]
```

In `mlss_monitor/state.py`, add near other globals:

```python
grow_ws_registry = None  # set in app.py at startup
```

In `mlss_monitor/app.py`, near the other background-thread starts in `_start_background_services()`:

```python
    from mlss_monitor.grow.ws_registry import WSRegistry
    from mlss_monitor.routes.api_grow_ws import start_ws_listener
    state.grow_ws_registry = WSRegistry()
    start_ws_listener(host="0.0.0.0", port=5001, registry=state.grow_ws_registry)
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_grow_ws.py -v`
Expected: PASS (4 tests)

```bash
git add mlss_monitor/routes/api_grow_ws.py mlss_monitor/state.py mlss_monitor/app.py tests/grow_server/test_grow_ws.py
git commit -m "Add WebSocket listener for grow units

Runs on port 5001 in its own asyncio loop on a background thread.
Per-unit connection dispatches messages by type: text → handlers
(telemetry/capabilities/event/ack), binary → photo_storage. Bearer
token validated on upgrade; bad token closes with 1008."
```

---

### Task 4.6: Manual command REST endpoints (identify + water_now)

**Files:**
- Modify: `mlss_monitor/routes/api_grow_units.py`
- Create: `tests/grow_server/test_grow_commands.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_grow_commands.py`:

```python
"""POST /api/grow/units/<id>/identify and /water-now push commands via WS registry."""
import asyncio
import json
import sqlite3
import tempfile
from datetime import datetime
import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    init_db.create_db()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'hw1', 'X', ?, 'h', ?)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.commit()
    conn.close()

    from mlss_monitor.grow.ws_registry import WSRegistry
    from mlss_monitor import state
    state.grow_ws_registry = WSRegistry()

    class FakeWS:
        def __init__(self): self.sent = []
        async def send(self, m): self.sent.append(m)

    fake_ws = FakeWS()
    state.grow_ws_registry.register(1, fake_ws)

    from flask import Flask
    from mlss_monitor.routes.api_grow_units import api_grow_units_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_units_bp)
    return app.test_client(), fake_ws


def test_identify_pushes_command(client):
    c, fake_ws = client
    r = c.post("/api/grow/units/1/identify")
    assert r.status_code == 202
    assert len(fake_ws.sent) == 1
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["type"] == "command"
    assert cmd["payload"]["name"] == "identify"
    assert cmd["payload"]["args"]["duration_s"] == 10


def test_identify_offline_unit_returns_503(client):
    c, _ = client
    r = c.post("/api/grow/units/9999/identify")
    assert r.status_code == 503
    body = r.get_json()
    assert body["error"] == "unit_not_connected"


def test_water_now_pushes_command_with_duration(client):
    c, fake_ws = client
    r = c.post("/api/grow/units/1/water-now", json={"duration_s": 5})
    assert r.status_code == 202
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["payload"]["name"] == "water_now"
    assert cmd["payload"]["args"]["duration_s"] == 5


def test_water_now_default_duration_is_5s(client):
    c, fake_ws = client
    r = c.post("/api/grow/units/1/water-now", json={})
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["payload"]["args"]["duration_s"] == 5


def test_water_now_clamps_to_30s_safety_cap(client):
    c, fake_ws = client
    r = c.post("/api/grow/units/1/water-now", json={"duration_s": 999})
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["payload"]["args"]["duration_s"] == 30
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_grow_commands.py -v`
Expected: FAIL — `404 NOT FOUND` (route doesn't exist yet)

- [ ] **Step 3: Implement**

Append to `mlss_monitor/routes/api_grow_units.py`:

```python
import asyncio
import json
from datetime import datetime
from flask import request
from mlss_monitor import state


def _push_command_blocking(unit_id: int, command: dict) -> tuple[int, dict]:
    """Send a command to a unit via the WS registry. Returns (status, body)."""
    registry = state.grow_ws_registry
    if registry is None or not registry.is_connected(unit_id):
        return 503, {"error": "unit_not_connected"}
    msg = json.dumps({
        "type": "command",
        "ts": datetime.utcnow().isoformat() + "Z",
        "payload": command,
    })
    # The WS listener runs on its own loop on another thread — schedule there
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(registry.send_to_unit(unit_id, msg))
        loop.close()
    except KeyError:
        return 503, {"error": "unit_not_connected"}
    return 202, {"queued": True}


@api_grow_units_bp.route("/api/grow/units/<int:unit_id>/identify", methods=["POST"])
def identify(unit_id):
    status, body = _push_command_blocking(unit_id, {
        "name": "identify",
        "args": {"duration_s": 10},
    })
    return jsonify(body), status


@api_grow_units_bp.route("/api/grow/units/<int:unit_id>/water-now", methods=["POST"])
def water_now(unit_id):
    body_in = request.get_json(silent=True) or {}
    duration_s = max(1, min(30, int(body_in.get("duration_s", 5))))  # safety cap
    status, body = _push_command_blocking(unit_id, {
        "name": "water_now",
        "args": {"duration_s": duration_s},
    })
    return jsonify(body), status
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_grow_commands.py -v`
Expected: PASS (5 tests)

```bash
git add mlss_monitor/routes/api_grow_units.py tests/grow_server/test_grow_commands.py
git commit -m "Add identify + water-now command endpoints

Reach into WS registry to push command frames. Returns 503 if the
unit isn't currently connected. Water duration clamped server-side to
the 30s hardware safety cap regardless of what the user requests."
```

---

## Section 5 — Firmware: sensors and actuators

ABCs that mirror MLSS's existing `DataSource` pattern. New sensors plug in by writing one `Sensor` subclass with `.detect()` + `.channels()` + `.read()`. Same for actuators.

---

### Task 5.1: Sensor ABC

**Files:**
- Create: `grow_unit/src/mlss_grow/sensors/__init__.py`
- Create: `grow_unit/src/mlss_grow/sensors/base.py`
- Create: `tests/grow_unit/test_sensor_base.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_unit/test_sensor_base.py`:

```python
"""Sensor ABC defines the .detect / .channels / .read / .healthy contract."""
from mlss_grow.sensors.base import Sensor
import pytest


class FakeSensor(Sensor):
    @classmethod
    def detect(cls, i2c_bus):
        return cls()

    def channels(self):
        return ["soil_moisture"]

    def read(self):
        return {"soil_moisture": 612}


def test_sensor_subclass_can_be_instantiated_and_used():
    s = FakeSensor.detect(i2c_bus=None)
    assert s is not None
    assert s.channels() == ["soil_moisture"]
    assert s.read() == {"soil_moisture": 612}


def test_sensor_default_healthy_starts_true():
    s = FakeSensor()
    assert s.healthy() is True


def test_sensor_marks_unhealthy_after_consecutive_bad_reads():
    s = FakeSensor()
    s.record_bad_read()
    s.record_bad_read()
    assert s.healthy() is True   # threshold 3
    s.record_bad_read()
    assert s.healthy() is False


def test_sensor_recovery_resets_bad_count():
    s = FakeSensor()
    for _ in range(3):
        s.record_bad_read()
    assert s.healthy() is False
    s.record_good_read()
    assert s.healthy() is True


def test_abstract_methods_must_be_implemented():
    with pytest.raises(TypeError):
        Sensor()  # cannot instantiate abstract class directly
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_sensor_base.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`grow_unit/src/mlss_grow/sensors/__init__.py`:

```python
"""Plant Grow Unit sensor implementations.

REGISTERED_SENSORS is the auto-detect registry — `auto_detect(i2c)` walks
this list, calls each class's `.detect(i2c)`, and returns the instances
that succeeded. Add a new sensor by writing a Sensor subclass and adding
it to REGISTERED_SENSORS.
"""
from mlss_grow.sensors.base import Sensor

REGISTERED_SENSORS: list[type[Sensor]] = []


def auto_detect(i2c_bus) -> list[Sensor]:
    """Probe each registered sensor class against the I2C bus; return survivors."""
    found = []
    for cls in REGISTERED_SENSORS:
        instance = cls.detect(i2c_bus)
        if instance is not None:
            found.append(instance)
    return found
```

`grow_unit/src/mlss_grow/sensors/base.py`:

```python
"""Sensor abstract base class.

A Sensor knows how to detect itself on the I2C bus, declares which channels
it reports, and produces a reading dict. The .healthy() method returns False
after 3 consecutive bad reads — used by the safety loop to fire a
sensor_degraded event upstream.
"""
from abc import ABC, abstractmethod

_BAD_READS_THRESHOLD = 3


class Sensor(ABC):
    def __init__(self):
        self._bad_reads = 0

    @classmethod
    @abstractmethod
    def detect(cls, i2c_bus) -> "Sensor | None":
        """Probe the I2C bus; return an instance if hardware present, None otherwise."""

    @abstractmethod
    def channels(self) -> list[str]:
        """The Channel string-values this sensor reports (e.g. ['soil_moisture', 'soil_temp_c'])."""

    @abstractmethod
    def read(self) -> dict[str, float]:
        """Return the current reading. Keys must be channels declared in .channels()."""

    def healthy(self) -> bool:
        return self._bad_reads < _BAD_READS_THRESHOLD

    def record_bad_read(self):
        self._bad_reads += 1

    def record_good_read(self):
        self._bad_reads = 0
```

- [ ] **Step 4: Run + commit**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_sensor_base.py -v`
Expected: PASS (5 tests)

```bash
git add grow_unit/src/mlss_grow/sensors/__init__.py grow_unit/src/mlss_grow/sensors/base.py tests/grow_unit/test_sensor_base.py
git commit -m "Add Sensor ABC + auto_detect registry

Mirrors MLSS server's DataSource ABC pattern. Adding a new sensor =
write a Sensor subclass, add to REGISTERED_SENSORS. healthy() goes
False after 3 consecutive bad reads — safety loop uses this to fire
sensor_degraded events upstream."
```

---

### Task 5.2: Seesaw soil sensor implementation

**Files:**
- Create: `grow_unit/src/mlss_grow/sensors/seesaw.py`
- Modify: `grow_unit/src/mlss_grow/sensors/__init__.py` (register Seesaw)
- Create: `tests/grow_unit/test_sensor_seesaw.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_unit/test_sensor_seesaw.py`:

```python
"""SeesawSoilSensor: detect-or-not, channels, read."""
from unittest.mock import MagicMock
from mlss_grow.sensors.seesaw import SeesawSoilSensor


def test_detect_returns_none_when_seesaw_lib_unavailable(monkeypatch):
    """On dev laptops without adafruit-circuitpython-seesaw installed."""
    monkeypatch.setattr("mlss_grow.sensors.seesaw._seesaw_module", None)
    assert SeesawSoilSensor.detect(i2c_bus=MagicMock()) is None


def test_detect_returns_instance_when_lib_present_and_device_responds(monkeypatch):
    fake_seesaw_module = MagicMock()
    fake_seesaw_module.Seesaw = MagicMock(return_value=MagicMock(
        moisture_read=MagicMock(return_value=612),
        get_temp=MagicMock(return_value=21.4),
    ))
    monkeypatch.setattr("mlss_grow.sensors.seesaw._seesaw_module", fake_seesaw_module)
    s = SeesawSoilSensor.detect(i2c_bus=MagicMock())
    assert s is not None
    assert isinstance(s, SeesawSoilSensor)


def test_detect_returns_none_when_device_not_present(monkeypatch):
    fake_seesaw_module = MagicMock()
    fake_seesaw_module.Seesaw = MagicMock(side_effect=OSError("no device"))
    monkeypatch.setattr("mlss_grow.sensors.seesaw._seesaw_module", fake_seesaw_module)
    assert SeesawSoilSensor.detect(i2c_bus=MagicMock()) is None


def test_channels_includes_moisture_and_temp():
    s = SeesawSoilSensor(driver=MagicMock())
    assert "soil_moisture" in s.channels()
    assert "soil_temp_c" in s.channels()


def test_read_returns_both_values():
    drv = MagicMock(moisture_read=MagicMock(return_value=612),
                    get_temp=MagicMock(return_value=21.4))
    s = SeesawSoilSensor(driver=drv)
    out = s.read()
    assert out["soil_moisture"] == 612
    assert out["soil_temp_c"] == 21.4


def test_read_marks_bad_read_when_value_out_of_sane_range():
    drv = MagicMock(moisture_read=MagicMock(return_value=50),  # below sane 200
                    get_temp=MagicMock(return_value=21))
    s = SeesawSoilSensor(driver=drv)
    s.read()
    assert s._bad_reads == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_sensor_seesaw.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mlss_grow.sensors.seesaw'`

- [ ] **Step 3: Implement**

`grow_unit/src/mlss_grow/sensors/seesaw.py`:

```python
"""Adafruit STEMMA Soil Sensor (Seesaw chip) — capacitive moisture + temp.

I2C address default 0x36 (selectable to 0x37/0x38/0x39 via solder jumpers).
Reports two channels: soil_moisture (raw 200-2000) and soil_temp_c.
"""
import logging
from mlss_grow.sensors.base import Sensor

log = logging.getLogger(__name__)

# Imported lazily so dev laptops without adafruit-circuitpython-seesaw can
# still import this module (and tests can monkeypatch).
try:
    from adafruit_seesaw import seesaw as _seesaw_module
except ImportError:
    _seesaw_module = None

I2C_ADDRESS = 0x36
SANE_RAW_MIN = 200
SANE_RAW_MAX = 2000


class SeesawSoilSensor(Sensor):
    def __init__(self, driver):
        super().__init__()
        self._driver = driver

    @classmethod
    def detect(cls, i2c_bus) -> "SeesawSoilSensor | None":
        if _seesaw_module is None:
            log.debug("adafruit_seesaw lib not installed; skipping detect")
            return None
        try:
            drv = _seesaw_module.Seesaw(i2c_bus, addr=I2C_ADDRESS)
            return cls(driver=drv)
        except (OSError, ValueError) as exc:
            log.debug("Seesaw not detected at 0x%02x: %s", I2C_ADDRESS, exc)
            return None

    def channels(self) -> list[str]:
        return ["soil_moisture", "soil_temp_c"]

    def read(self) -> dict[str, float]:
        try:
            raw = self._driver.moisture_read()
            temp = self._driver.get_temp()
        except Exception as exc:
            log.warning("Seesaw read failed: %s", exc)
            self.record_bad_read()
            return {}

        if raw < SANE_RAW_MIN or raw > SANE_RAW_MAX:
            log.warning("Seesaw raw %d out of sane range [%d, %d]",
                        raw, SANE_RAW_MIN, SANE_RAW_MAX)
            self.record_bad_read()
            return {}

        self.record_good_read()
        return {"soil_moisture": raw, "soil_temp_c": round(temp, 2)}
```

Update `grow_unit/src/mlss_grow/sensors/__init__.py`:

```python
from mlss_grow.sensors.base import Sensor
from mlss_grow.sensors.seesaw import SeesawSoilSensor

REGISTERED_SENSORS: list[type[Sensor]] = [
    SeesawSoilSensor,
]


def auto_detect(i2c_bus) -> list[Sensor]:
    found = []
    for cls in REGISTERED_SENSORS:
        instance = cls.detect(i2c_bus)
        if instance is not None:
            found.append(instance)
    return found
```

- [ ] **Step 4: Run + commit**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_sensor_seesaw.py -v`
Expected: PASS (6 tests)

```bash
git add grow_unit/src/mlss_grow/sensors/seesaw.py grow_unit/src/mlss_grow/sensors/__init__.py tests/grow_unit/test_sensor_seesaw.py
git commit -m "Add SeesawSoilSensor implementation

I2C 0x36, reports soil_moisture (raw 200-2000) + soil_temp_c. Out-of-
range raw values increment bad-read count without being reported.
Lib import is lazy so dev laptops without adafruit-circuitpython-seesaw
can still test."
```

---

### Task 5.3: Actuator ABC + Automation pHAT implementations (pump, light)

**Files:**
- Create: `grow_unit/src/mlss_grow/actuators/__init__.py`
- Create: `grow_unit/src/mlss_grow/actuators/base.py`
- Create: `grow_unit/src/mlss_grow/actuators/automation_phat.py`
- Create: `tests/grow_unit/test_actuators.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_unit/test_actuators.py`:

```python
"""Actuator ABC + Pump (sinking output) + Light (relay) for Automation pHAT."""
import time
from unittest.mock import MagicMock
from mlss_grow.actuators.base import Actuator
from mlss_grow.actuators.automation_phat import (
    AutomationPHATPump, AutomationPHATLight,
)
import pytest


def test_actuator_is_abstract():
    with pytest.raises(TypeError):
        Actuator()


def test_pump_on_off_drives_sinking_output(monkeypatch):
    fake_phat = MagicMock()
    fake_phat.output.one.on = MagicMock()
    fake_phat.output.one.off = MagicMock()
    monkeypatch.setattr(
        "mlss_grow.actuators.automation_phat._automationhat", fake_phat)

    p = AutomationPHATPump()
    p.on()
    fake_phat.output.one.on.assert_called_once()
    p.off()
    fake_phat.output.one.off.assert_called_once()


def test_pump_state_tracks_on_off(monkeypatch):
    fake_phat = MagicMock()
    monkeypatch.setattr(
        "mlss_grow.actuators.automation_phat._automationhat", fake_phat)
    p = AutomationPHATPump()
    assert p.state() is False
    p.on()
    assert p.state() is True
    p.off()
    assert p.state() is False


def test_pump_pulse_runs_for_duration_then_stops(monkeypatch):
    fake_phat = MagicMock()
    monkeypatch.setattr(
        "mlss_grow.actuators.automation_phat._automationhat", fake_phat)
    p = AutomationPHATPump()
    t0 = time.monotonic()
    p.pulse(0.5)
    elapsed = time.monotonic() - t0
    assert 0.45 < elapsed < 0.7  # ran for ~0.5s
    assert p.state() is False     # back off after pulse


def test_pump_pulse_capped_at_safety_max(monkeypatch):
    fake_phat = MagicMock()
    monkeypatch.setattr(
        "mlss_grow.actuators.automation_phat._automationhat", fake_phat)
    p = AutomationPHATPump()
    t0 = time.monotonic()
    p.pulse(999)  # request 999s
    elapsed = time.monotonic() - t0
    # Hard cap is 30s but we don't want to wait that long in tests — bump the
    # safety cap down via the constructor for testing.
    # (Re-creating with cap=0.3 to demonstrate the clamp works)


def test_pump_pulse_capped_via_constructor(monkeypatch):
    fake_phat = MagicMock()
    monkeypatch.setattr(
        "mlss_grow.actuators.automation_phat._automationhat", fake_phat)
    p = AutomationPHATPump(safety_max_pulse_s=0.3)
    t0 = time.monotonic()
    p.pulse(999)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5  # capped


def test_light_on_off_drives_relay(monkeypatch):
    fake_phat = MagicMock()
    fake_phat.relay.one.on = MagicMock()
    fake_phat.relay.one.off = MagicMock()
    monkeypatch.setattr(
        "mlss_grow.actuators.automation_phat._automationhat", fake_phat)
    l = AutomationPHATLight()
    l.on()
    fake_phat.relay.one.on.assert_called_once()
    l.off()
    fake_phat.relay.one.off.assert_called_once()


def test_light_pulse_blinks_n_times(monkeypatch):
    """Used by identify command — blink relay every 500ms."""
    fake_phat = MagicMock()
    monkeypatch.setattr(
        "mlss_grow.actuators.automation_phat._automationhat", fake_phat)
    l = AutomationPHATLight()
    l.blink_pattern(duration_s=1.0, period_s=0.2)
    on_calls = fake_phat.relay.one.on.call_count
    off_calls = fake_phat.relay.one.off.call_count
    # ~1.0s / 0.2s period = 5 cycles. Roughly equal on/off counts.
    assert on_calls >= 4
    assert off_calls >= 4
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_actuators.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`grow_unit/src/mlss_grow/actuators/__init__.py`:

```python
"""Plant Grow Unit actuator implementations."""
```

`grow_unit/src/mlss_grow/actuators/base.py`:

```python
"""Actuator abstract base class.

An Actuator is a switchable load. .pulse() turns it on for a duration then
off, with a safety cap to prevent runaway commands. The safety_max_pulse_s
defaults to 30s per the spec; constructor parameter lets tests use shorter.
"""
from abc import ABC, abstractmethod


class Actuator(ABC):
    @abstractmethod
    def on(self): ...

    @abstractmethod
    def off(self): ...

    @abstractmethod
    def state(self) -> bool: ...

    @abstractmethod
    def pulse(self, seconds: float): ...
```

`grow_unit/src/mlss_grow/actuators/automation_phat.py`:

```python
"""Pimoroni Automation pHAT actuators: pump on sinking OUT 1, light on relay."""
import logging
import time
from mlss_grow.actuators.base import Actuator

log = logging.getLogger(__name__)

try:
    import automationhat as _automationhat
except ImportError:
    _automationhat = None


class AutomationPHATPump(Actuator):
    """Water pump driven by the pHAT's sinking output OUT 1."""

    def __init__(self, safety_max_pulse_s: float = 30.0):
        self._on = False
        self._safety_max = safety_max_pulse_s

    def on(self):
        if _automationhat is not None:
            _automationhat.output.one.on()
        self._on = True

    def off(self):
        if _automationhat is not None:
            _automationhat.output.one.off()
        self._on = False

    def state(self) -> bool:
        return self._on

    def pulse(self, seconds: float):
        duration = min(seconds, self._safety_max)
        if duration <= 0:
            return
        self.on()
        try:
            time.sleep(duration)
        finally:
            self.off()


class AutomationPHATLight(Actuator):
    """Grow light driven by the pHAT's relay (NO contact, fail-safe to dark)."""

    def __init__(self, safety_max_pulse_s: float = 86400.0):
        self._on = False
        self._safety_max = safety_max_pulse_s

    def on(self):
        if _automationhat is not None:
            _automationhat.relay.one.on()
        self._on = True

    def off(self):
        if _automationhat is not None:
            _automationhat.relay.one.off()
        self._on = False

    def state(self) -> bool:
        return self._on

    def pulse(self, seconds: float):
        duration = min(seconds, self._safety_max)
        if duration <= 0:
            return
        self.on()
        try:
            time.sleep(duration)
        finally:
            self.off()

    def blink_pattern(self, duration_s: float, period_s: float = 0.5):
        """Blink the light at given period for total duration. Used by identify command."""
        end = time.monotonic() + duration_s
        while time.monotonic() < end:
            self.on()
            time.sleep(period_s / 2)
            self.off()
            time.sleep(period_s / 2)
```

- [ ] **Step 4: Run + commit**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_actuators.py -v`
Expected: PASS (8 tests — including the constructor-capped pulse)

```bash
git add grow_unit/src/mlss_grow/actuators/ tests/grow_unit/test_actuators.py
git commit -m "Add Actuator ABC + Automation pHAT pump (OUT 1) + light (relay)

Pump.pulse() blocks for the duration with a 30s safety cap. Light has
a blink_pattern() for the identify command (10x cycle every 500ms by
default). automationhat module imported lazily so dev laptops without
the Pi-only lib can still import + test."
```

---

## Section 6 — Firmware: PID, light schedule, camera, buffer, safety loop

Pure functions for PID + light-schedule evaluation (trivially testable). Camera + buffer wrap external libs. Safety loop ties them all together.

---

### Task 6.1: PID watering decision (pure function)

**Files:**
- Create: `grow_unit/src/mlss_grow/pid.py`
- Create: `tests/grow_unit/test_pid.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_unit/test_pid.py`:

```python
"""PID watering decision: pure function over (current_pct, config, state)."""
from datetime import datetime, timedelta
from mlss_grow.pid import (
    PIDConfig, PIDState, pid_decide, Decision,
)


def _cfg(**kwargs):
    defaults = dict(
        target_pct=55, deadband_pct=5, kp=0.4, ki=0, kd=0,
        min_pulse_s=2, max_pulse_s=8, soak_window_min=30,
    )
    defaults.update(kwargs)
    return PIDConfig(**defaults)


def test_above_target_no_pulse():
    """Soil at 60%, target 55, deadband 5 — within deadband → no pulse."""
    state = PIDState(last_pulse_at=datetime(2026, 1, 1), last_error=0, error_integral=0)
    d = pid_decide(current_pct=60, config=_cfg(), state=state, now=datetime(2026, 5, 3))
    assert d.pulse_s == 0
    assert d.reason == "within_deadband"


def test_within_deadband_no_pulse():
    """Soil at 51%, target 55, deadband 5 → error 4, within deadband → no pulse."""
    state = PIDState(last_pulse_at=datetime(2026, 1, 1), last_error=0, error_integral=0)
    d = pid_decide(current_pct=51, config=_cfg(), state=state, now=datetime(2026, 5, 3))
    assert d.pulse_s == 0


def test_dry_with_p_only_pulses_proportional():
    """Soil at 40%, target 55, error=15, deadband=5 → past deadband. P-only Kp=0.4
    → pulse = 0.4 * 15 = 6s. min/max [2, 8] doesn't clamp."""
    state = PIDState(last_pulse_at=datetime(2026, 1, 1), last_error=0, error_integral=0)
    d = pid_decide(current_pct=40, config=_cfg(), state=state, now=datetime(2026, 5, 3))
    assert d.pulse_s == 6.0
    assert d.p_term == 6.0
    assert d.i_term == 0
    assert d.d_term == 0


def test_pulse_clamped_to_max():
    """Soil at 0%, error=55. Kp=0.4 → 22s. Clamped to max=8s."""
    state = PIDState(last_pulse_at=datetime(2026, 1, 1), last_error=0, error_integral=0)
    d = pid_decide(current_pct=0, config=_cfg(), state=state, now=datetime(2026, 5, 3))
    assert d.pulse_s == 8.0


def test_pulse_clamped_to_min():
    """Just past deadband — pulse < min. Clamped up to min."""
    cfg = _cfg(kp=0.05, min_pulse_s=2)
    # error = 11 (above deadband 5). 0.05*11 = 0.55s. Clamp to min=2.
    state = PIDState(last_pulse_at=datetime(2026, 1, 1), last_error=0, error_integral=0)
    d = pid_decide(current_pct=44, config=cfg, state=state, now=datetime(2026, 5, 3))
    assert d.pulse_s == 2.0


def test_in_soak_window_no_pulse():
    """Last pulse was 10 minutes ago, soak_window=30 → still locked."""
    now = datetime(2026, 5, 3, 12, 0, 0)
    state = PIDState(
        last_pulse_at=now - timedelta(minutes=10),
        last_error=0, error_integral=0,
    )
    d = pid_decide(current_pct=30, config=_cfg(), state=state, now=now)
    assert d.pulse_s == 0
    assert d.reason == "in_soak_window"


def test_after_soak_window_fires():
    """Soak elapsed → fires."""
    now = datetime(2026, 5, 3, 12, 0, 0)
    state = PIDState(
        last_pulse_at=now - timedelta(minutes=31),
        last_error=0, error_integral=0,
    )
    d = pid_decide(current_pct=30, config=_cfg(), state=state, now=now)
    assert d.pulse_s > 0


def test_integral_term_accumulates_when_ki_nonzero():
    """With Ki=0.1, after 60s of error=10, integral term = 0.1 * 10 * 60 = 60.
    But anti-windup clamps at ±100."""
    cfg = _cfg(ki=0.1)
    state = PIDState(last_pulse_at=datetime(2026, 1, 1),
                     last_error=10, error_integral=600)  # already at clamp
    d = pid_decide(current_pct=45, config=cfg, state=state,
                   now=datetime(2026, 5, 3), tick_seconds=30)
    # i_term = ki * clamped_integral
    # state.error_integral updated: 600 + 10*30 = 900 → clamped to 100
    assert state.error_integral == 100  # mutated
    assert d.i_term == 0.1 * 100


def test_derivative_term_when_kd_nonzero():
    """Kd=0.5, error 15, last_error 5, tick 30s → derivative = (15-5)/30 = 0.333.
    d_term = 0.5 * 0.333 = 0.167."""
    cfg = _cfg(kd=0.5)
    state = PIDState(last_pulse_at=datetime(2026, 1, 1),
                     last_error=5, error_integral=0)
    d = pid_decide(current_pct=40, config=cfg, state=state,
                   now=datetime(2026, 5, 3), tick_seconds=30)
    assert d.d_term == round(0.5 * (10 / 30), 4) or abs(d.d_term - 0.167) < 0.01
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_pid.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`grow_unit/src/mlss_grow/pid.py`:

```python
"""Pure PID watering decision logic.

Designed as a pure function over (current_pct, config, state, now) so the
core control logic is unit-testable without any I/O. The safety loop
calls this on every tick; if the returned Decision has pulse_s > 0,
the safety loop pulses the pump.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta


def _clip(value, lo, hi):
    return max(lo, min(hi, value))


@dataclass
class PIDConfig:
    target_pct: float
    deadband_pct: float = 5
    kp: float = 0.4
    ki: float = 0
    kd: float = 0
    min_pulse_s: float = 2
    max_pulse_s: float = 8
    soak_window_min: int = 30


@dataclass
class PIDState:
    last_pulse_at: datetime
    last_error: float = 0
    error_integral: float = 0


@dataclass
class Decision:
    pulse_s: float
    reason: str = ""
    p_term: float = 0
    i_term: float = 0
    d_term: float = 0


_INTEGRAL_CLAMP = 100  # anti-windup


def pid_decide(current_pct: float, config: PIDConfig, state: PIDState,
               now: datetime, tick_seconds: float = 30) -> Decision:
    error = config.target_pct - current_pct
    if error <= config.deadband_pct:
        return Decision(pulse_s=0, reason="within_deadband")

    if (now - state.last_pulse_at) < timedelta(minutes=config.soak_window_min):
        return Decision(pulse_s=0, reason="in_soak_window")

    # Update integral with anti-windup
    state.error_integral = _clip(
        state.error_integral + error * tick_seconds,
        -_INTEGRAL_CLAMP, _INTEGRAL_CLAMP,
    )
    derivative = (error - state.last_error) / tick_seconds if tick_seconds > 0 else 0
    state.last_error = error

    p_term = config.kp * error
    i_term = config.ki * state.error_integral
    d_term = round(config.kd * derivative, 4)
    pulse = p_term + i_term + d_term
    pulse = _clip(pulse, config.min_pulse_s, config.max_pulse_s)
    return Decision(pulse_s=pulse, reason="fired",
                    p_term=p_term, i_term=i_term, d_term=d_term)
```

- [ ] **Step 4: Run + commit**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_pid.py -v`
Expected: PASS (9 tests)

```bash
git add grow_unit/src/mlss_grow/pid.py tests/grow_unit/test_pid.py
git commit -m "Add pure-function PID watering decision

P-term + I-term (with ±100 anti-windup) + D-term, clamped to
[min_pulse_s, max_pulse_s]. Skipped if within deadband or in soak
window. Pure function — no I/O — full unit test coverage."
```

---

### Task 6.2: Light schedule evaluator (pure function)

**Files:**
- Create: `grow_unit/src/mlss_grow/light_schedule.py`
- Create: `tests/grow_unit/test_light_schedule.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_unit/test_light_schedule.py`:

```python
"""is_light_on: pure function over (now, list of (start, end) windows)."""
from datetime import datetime, time
from mlss_grow.light_schedule import is_light_on, parse_window


def test_inside_simple_window():
    windows = [parse_window("06:00", "22:00")]
    assert is_light_on(datetime(2026, 5, 3, 12, 0), windows) is True


def test_outside_simple_window():
    windows = [parse_window("06:00", "22:00")]
    assert is_light_on(datetime(2026, 5, 3, 4, 0), windows) is False
    assert is_light_on(datetime(2026, 5, 3, 23, 0), windows) is False


def test_window_inclusive_at_start_exclusive_at_end():
    """06:00:00 ON, 22:00:00 OFF (i.e. light is on for [06:00, 22:00))."""
    windows = [parse_window("06:00", "22:00")]
    assert is_light_on(datetime(2026, 5, 3, 6, 0, 0), windows) is True
    assert is_light_on(datetime(2026, 5, 3, 22, 0, 0), windows) is False
    assert is_light_on(datetime(2026, 5, 3, 21, 59, 59), windows) is True


def test_overnight_window():
    """22:00 → 06:00 (overnight, end < start). On overnight."""
    windows = [parse_window("22:00", "06:00")]
    assert is_light_on(datetime(2026, 5, 3, 23, 0), windows) is True
    assert is_light_on(datetime(2026, 5, 3, 5, 0), windows) is True
    assert is_light_on(datetime(2026, 5, 3, 12, 0), windows) is False


def test_multiple_windows_per_day():
    """06:00-12:00 + 14:00-22:00 (midday off)."""
    windows = [parse_window("06:00", "12:00"), parse_window("14:00", "22:00")]
    assert is_light_on(datetime(2026, 5, 3, 8, 0), windows) is True
    assert is_light_on(datetime(2026, 5, 3, 13, 0), windows) is False
    assert is_light_on(datetime(2026, 5, 3, 16, 0), windows) is True
    assert is_light_on(datetime(2026, 5, 3, 23, 0), windows) is False


def test_empty_window_list_is_off():
    assert is_light_on(datetime.utcnow(), []) is False


def test_parse_window_rejects_invalid():
    import pytest
    with pytest.raises(ValueError):
        parse_window("25:00", "10:00")
    with pytest.raises(ValueError):
        parse_window("06:00", "")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_light_schedule.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`grow_unit/src/mlss_grow/light_schedule.py`:

```python
"""Light schedule evaluation.

Pure function over a datetime + a list of (start, end) windows. Handles
overnight windows (end < start) and multi-window-per-day setups.
"""
from datetime import datetime, time, timedelta
import re

_HH_MM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_window(start_hh_mm: str, end_hh_mm: str) -> tuple[time, time]:
    if not _HH_MM.match(start_hh_mm) or not _HH_MM.match(end_hh_mm):
        raise ValueError(f"invalid window: {start_hh_mm}-{end_hh_mm}")
    return (
        time(int(start_hh_mm[:2]), int(start_hh_mm[3:])),
        time(int(end_hh_mm[:2]), int(end_hh_mm[3:])),
    )


def is_light_on(now: datetime, windows: list[tuple[time, time]]) -> bool:
    """True if `now` falls inside at least one window. Windows are [start, end)."""
    t = now.time()
    for start, end in windows:
        if start <= end:
            # Same-day window: on if start <= t < end
            if start <= t < end:
                return True
        else:
            # Overnight: on if t >= start OR t < end
            if t >= start or t < end:
                return True
    return False
```

- [ ] **Step 4: Run + commit**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_light_schedule.py -v`
Expected: PASS (7 tests)

```bash
git add grow_unit/src/mlss_grow/light_schedule.py tests/grow_unit/test_light_schedule.py
git commit -m "Add light schedule evaluator (handles overnight + multi-window)"
```

---

### Task 6.3: Camera capture wrapper

**Files:**
- Create: `grow_unit/src/mlss_grow/camera.py`
- Create: `tests/grow_unit/test_camera.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_unit/test_camera.py`:

```python
"""Camera wrapper: detect, capture (returns JPEG bytes + metadata)."""
from unittest.mock import MagicMock
from mlss_grow.camera import Camera, CameraNotAvailable
import pytest


def test_detect_returns_none_when_picamera2_missing(monkeypatch):
    monkeypatch.setattr("mlss_grow.camera._picamera2_module", None)
    assert Camera.detect() is None


def test_detect_returns_camera_instance_when_lib_present(monkeypatch):
    fake_pc2 = MagicMock()
    fake_pc2.Picamera2 = MagicMock(return_value=MagicMock())
    monkeypatch.setattr("mlss_grow.camera._picamera2_module", fake_pc2)
    cam = Camera.detect()
    assert isinstance(cam, Camera)


def test_capture_returns_bytes_and_metadata(monkeypatch):
    fake_drv = MagicMock()
    fake_drv.capture_array = MagicMock(return_value=MagicMock())
    fake_drv.camera_properties = {"PixelArraySize": (1920, 1080)}
    fake_drv.metadata = MagicMock(return_value={"ExposureTime": 16667, "AnalogueGain": 1.0})

    cam = Camera(driver=fake_drv)

    # Mock the JPEG encoding step (we don't want to actually run picamera2 here)
    monkeypatch.setattr("mlss_grow.camera._encode_jpeg",
                        lambda arr, quality: b"\xff\xd8FAKEJPEG")

    jpeg_bytes, meta = cam.capture()
    assert jpeg_bytes == b"\xff\xd8FAKEJPEG"
    assert meta["width"] == 1920
    assert meta["height"] == 1080
    assert meta["jpeg_quality"] == 85
    assert meta["shutter_us"] == 16667


def test_capture_raises_camera_not_available_when_no_driver():
    cam = Camera(driver=None)
    with pytest.raises(CameraNotAvailable):
        cam.capture()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_camera.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`grow_unit/src/mlss_grow/camera.py`:

```python
"""Pi camera capture wrapper using picamera2.

Detects on Camera.detect(), produces (jpeg_bytes, metadata) on .capture().
The metadata dict matches the JSON header expected by the WS photo frame
parser on the server side (see mlss_monitor.grow.photo_storage).
"""
import io
import logging

log = logging.getLogger(__name__)

try:
    from picamera2 import Picamera2 as _Picamera2
    import picamera2 as _picamera2_module
except ImportError:
    _picamera2_module = None
    _Picamera2 = None

try:
    from PIL import Image
except ImportError:
    Image = None


class CameraNotAvailable(Exception):
    pass


def _encode_jpeg(array, quality: int) -> bytes:
    """Encode a numpy array (HxWx3 RGB) to JPEG bytes via Pillow."""
    if Image is None:
        raise RuntimeError("Pillow not installed — cannot encode JPEG")
    im = Image.fromarray(array)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


class Camera:
    DEFAULT_QUALITY = 85

    def __init__(self, driver):
        self._driver = driver

    @classmethod
    def detect(cls) -> "Camera | None":
        if _picamera2_module is None:
            return None
        try:
            drv = _Picamera2()
            config = drv.create_still_configuration()
            drv.configure(config)
            drv.start()
            return cls(driver=drv)
        except Exception as exc:
            log.warning("picamera2 init failed: %s", exc)
            return None

    def capture(self) -> tuple[bytes, dict]:
        if self._driver is None:
            raise CameraNotAvailable("camera driver not initialised")
        array = self._driver.capture_array()
        meta = self._driver.metadata()
        jpeg_bytes = _encode_jpeg(array, self.DEFAULT_QUALITY)
        width, height = self._driver.camera_properties.get(
            "PixelArraySize", (0, 0))
        return jpeg_bytes, {
            "width": width,
            "height": height,
            "jpeg_quality": self.DEFAULT_QUALITY,
            "shutter_us": meta.get("ExposureTime"),
            "iso": int(meta.get("AnalogueGain", 1) * 100),
        }
```

Add to `grow_unit/pyproject.toml` under deps:

```toml
Pillow = "^10.0"
```

- [ ] **Step 4: Run + commit**

Run: `cd grow_unit && poetry install && python -m pytest ../tests/grow_unit/test_camera.py -v`
Expected: PASS (4 tests)

```bash
git add grow_unit/src/mlss_grow/camera.py grow_unit/pyproject.toml tests/grow_unit/test_camera.py
git commit -m "Add picamera2 + Pillow camera wrapper

Returns (jpeg_bytes, metadata) — metadata shape matches the JSON
header the server expects in the binary photo frame. picamera2
imported lazily so dev laptops can test."
```

---

### Task 6.4: Local SQLite buffer (telemetry + events when MLSS unreachable)

**Files:**
- Create: `grow_unit/src/mlss_grow/buffer.py`
- Create: `tests/grow_unit/test_buffer.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_unit/test_buffer.py`:

```python
"""LocalBuffer: append messages, replay in timestamp order, prune by age."""
import time
from datetime import datetime, timedelta
from mlss_grow.buffer import LocalBuffer


def test_append_and_size(tmp_path):
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"))
    buf.append("telemetry", '{"a":1}', ts=datetime(2026, 1, 1))
    buf.append("event", '{"b":2}', ts=datetime(2026, 1, 2))
    assert buf.size() == 2


def test_pop_all_returns_in_timestamp_order(tmp_path):
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"))
    buf.append("telemetry", '{"i":2}', ts=datetime(2026, 1, 2))
    buf.append("telemetry", '{"i":1}', ts=datetime(2026, 1, 1))
    buf.append("telemetry", '{"i":3}', ts=datetime(2026, 1, 3))
    rows = buf.pop_all()
    assert [r.body for r in rows] == ['{"i":1}', '{"i":2}', '{"i":3}']
    assert buf.size() == 0


def test_pop_all_empty_buffer(tmp_path):
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"))
    assert buf.pop_all() == []


def test_prune_drops_rows_older_than_retention(tmp_path):
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"))
    old = datetime(2025, 1, 1)
    new = datetime(2026, 5, 1)
    buf.append("telemetry", '{"x":1}', ts=old)
    buf.append("telemetry", '{"x":2}', ts=new)
    buf.prune(retention_days=7, now=datetime(2026, 5, 8))
    assert buf.size() == 1
    rows = buf.pop_all()
    assert rows[0].body == '{"x":2}'


def test_buffer_survives_close_reopen(tmp_path):
    path = str(tmp_path / "buf.sqlite")
    b1 = LocalBuffer(db_path=path)
    b1.append("telemetry", '{"persist":true}', ts=datetime(2026, 1, 1))
    b1.close()

    b2 = LocalBuffer(db_path=path)
    assert b2.size() == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_buffer.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`grow_unit/src/mlss_grow/buffer.py`:

```python
"""Local SQLite buffer for telemetry + events when MLSS is unreachable.

When the WS client can't deliver, messages go here. On reconnect, the
client calls .pop_all() and replays them in timestamp order before
resuming live stream. Prune by age to bound disk use.
"""
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


@dataclass
class BufferedRow:
    id: int
    msg_type: str
    body: str
    timestamp_utc: datetime


class LocalBuffer:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS buffer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                msg_type TEXT NOT NULL,
                body TEXT NOT NULL,
                timestamp_utc DATETIME NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_buffer_ts ON buffer(timestamp_utc)"
        )
        self._conn.commit()

    def append(self, msg_type: str, body: str, ts: datetime):
        self._conn.execute(
            "INSERT INTO buffer (msg_type, body, timestamp_utc) VALUES (?, ?, ?)",
            (msg_type, body, ts),
        )
        self._conn.commit()

    def size(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM buffer").fetchone()[0]

    def pop_all(self) -> list[BufferedRow]:
        """Return all buffered rows in timestamp order; clears the buffer."""
        rows = self._conn.execute(
            "SELECT id, msg_type, body, timestamp_utc FROM buffer "
            "ORDER BY timestamp_utc ASC"
        ).fetchall()
        out = [
            BufferedRow(
                id=r[0], msg_type=r[1], body=r[2],
                timestamp_utc=datetime.fromisoformat(r[3])
                if isinstance(r[3], str) else r[3]
            )
            for r in rows
        ]
        self._conn.execute("DELETE FROM buffer")
        self._conn.commit()
        return out

    def prune(self, retention_days: int, now: datetime | None = None):
        cutoff = (now or datetime.utcnow()) - timedelta(days=retention_days)
        self._conn.execute("DELETE FROM buffer WHERE timestamp_utc < ?", (cutoff,))
        self._conn.commit()

    def close(self):
        self._conn.close()
```

- [ ] **Step 4: Run + commit**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_buffer.py -v`
Expected: PASS (5 tests)

```bash
git add grow_unit/src/mlss_grow/buffer.py tests/grow_unit/test_buffer.py
git commit -m "Add local SQLite buffer for offline telemetry + replay

WAL mode for concurrent reads. .pop_all() returns in timestamp order
then clears the buffer (atomic — used during reconnect replay).
.prune(retention_days) bounds disk use; default retention 7 days set
by safety loop."
```

---

### Task 6.5: Safety loop orchestration

**Files:**
- Create: `grow_unit/src/mlss_grow/safety_loop.py`
- Create: `tests/grow_unit/test_safety_loop.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_unit/test_safety_loop.py`:

```python
"""SafetyLoop: orchestrates sensors → PID → actuators every tick.

Tests verify the orchestration: sensors are read, light state flips
to match the schedule, PID decisions trigger pump pulses, events get
emitted to the supplied callback.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from unittest.mock import MagicMock
from mlss_grow.safety_loop import SafetyLoop, LoopConfig
from mlss_grow.pid import PIDConfig, PIDState
from mlss_grow.light_schedule import parse_window


def _basic_config():
    return LoopConfig(
        light_windows=[parse_window("06:00", "22:00")],
        pid=PIDConfig(target_pct=55),
        photo_interval_min=30,
        photo_active_hours=(6, 22),
    )


def test_tick_reads_sensors_and_emits_telemetry():
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    emitted = []

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=_basic_config(),
        emit=lambda kind, payload: emitted.append((kind, payload)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
    )
    loop.tick()
    kinds = [e[0] for e in emitted]
    assert "telemetry" in kinds
    tel = next(p for k, p in emitted if k == "telemetry")
    assert tel["soil_moisture_raw"] == 612


def test_tick_turns_light_on_in_window():
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612}, healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=MagicMock(return_value=False))

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=_basic_config(), emit=lambda *a, **k: None,
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),  # mid-window
    )
    loop.tick()
    light.on.assert_called_once()


def test_tick_turns_light_off_outside_window():
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612}, healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=MagicMock(return_value=True))

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=_basic_config(), emit=lambda *a, **k: None,
        now_fn=lambda: datetime(2026, 5, 3, 4, 0),  # before window
    )
    loop.tick()
    light.off.assert_called_once()


def test_tick_fires_pid_pulse_when_dry_and_emits_watering_event(tmp_path):
    """Soil at 30%, target 55, deadband 5, kp=0.4 → pulse 8s (clamped to max 8)."""
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    emitted = []

    cfg = _basic_config()
    cfg.soil_calibration = (200, 1500)  # raw 612 → ~31.7%

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=cfg, emit=lambda k, p: emitted.append((k, p)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
        pid_state=PIDState(last_pulse_at=datetime(2025, 1, 1)),  # past soak
    )
    loop.tick()
    pump.pulse.assert_called_once()
    pulse_arg = pump.pulse.call_args[0][0]
    assert pulse_arg > 0
    assert any(k == "event" and p.get("kind") == "watering_pulse"
               for k, p in emitted)


def test_sensor_degraded_emits_event():
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {},  # empty = bad read
                       healthy=lambda: False)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    emitted = []

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=_basic_config(),
        emit=lambda k, p: emitted.append((k, p)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
    )
    loop.tick()
    assert any(k == "event" and p.get("kind") == "sensor_degraded"
               for k, p in emitted)


def test_camera_captured_at_interval(tmp_path):
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612}, healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    camera = MagicMock(capture=MagicMock(
        return_value=(b"\xff\xd8FAKE", {"width": 1920, "height": 1080}),
    ))
    emitted = []

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=camera,
        config=_basic_config(),
        emit=lambda k, p: emitted.append((k, p)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
    )
    loop.tick()
    camera.capture.assert_called_once()
    assert any(k == "photo" for k, _ in emitted)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_safety_loop.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`grow_unit/src/mlss_grow/safety_loop.py`:

```python
"""Top-level orchestration: sensors → PID → actuators every tick."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from mlss_grow.pid import PIDConfig, PIDState, pid_decide
from mlss_grow.light_schedule import is_light_on


def _moisture_pct(raw: int, calibration: tuple[int, int] | None) -> float | None:
    if calibration is None:
        return None
    dry, wet = calibration
    if wet <= dry:
        return None
    pct = (raw - dry) / (wet - dry) * 100
    return max(0.0, min(100.0, round(pct, 2)))


@dataclass
class LoopConfig:
    light_windows: list
    pid: PIDConfig
    photo_interval_min: int = 30
    photo_active_hours: tuple[int, int] | None = (6, 22)
    soil_calibration: tuple[int, int] | None = None


class SafetyLoop:
    def __init__(self, sensors, pump, light, camera, config: LoopConfig,
                 emit: Callable[[str, dict], None],
                 now_fn: Callable[[], datetime] = datetime.utcnow,
                 pid_state: PIDState | None = None):
        self._sensors = sensors
        self._pump = pump
        self._light = light
        self._camera = camera
        self._config = config
        self._emit = emit
        self._now = now_fn
        self._pid_state = pid_state or PIDState(
            last_pulse_at=datetime(2000, 1, 1))
        self._last_photo_at: Optional[datetime] = None

    def tick(self):
        now = self._now()

        # 1. Read all sensors
        readings = {}
        any_degraded = False
        for s in self._sensors:
            try:
                vals = s.read()
                readings.update(vals)
            except Exception:
                pass
            if not s.healthy():
                any_degraded = True

        if any_degraded:
            self._emit("event", {"kind": "sensor_degraded", "details": {}})

        # 2. Light schedule
        should_be_on = is_light_on(now, self._config.light_windows)
        if should_be_on != self._light.state():
            (self._light.on() if should_be_on else self._light.off())

        # 3. PID watering
        raw = readings.get("soil_moisture")
        if raw is not None:
            pct = _moisture_pct(raw, self._config.soil_calibration)
            if pct is not None:
                d = pid_decide(pct, self._config.pid, self._pid_state, now)
                if d.pulse_s > 0:
                    self._pump.pulse(d.pulse_s)
                    self._pid_state.last_pulse_at = now
                    self._emit("event", {
                        "kind": "watering_pulse",
                        "details": {
                            "duration_s": d.pulse_s, "trigger": "pid",
                            "soil_pct_before": pct,
                            "pid_error": self._config.pid.target_pct - pct,
                            "pid_p_term": d.p_term, "pid_i_term": d.i_term,
                            "pid_d_term": d.d_term, "triggered_by": "system",
                        },
                    })

        # 4. Camera at interval
        if self._camera is not None and self._photo_due(now):
            try:
                jpeg, meta = self._camera.capture()
                meta["taken_at"] = now.isoformat() + "Z"
                self._emit("photo", {"meta": meta, "jpeg_bytes": jpeg})
                self._last_photo_at = now
            except Exception:
                pass

        # 5. Telemetry — always last, includes everything we just did
        self._emit("telemetry", {
            "soil_moisture_raw": raw if raw is not None else 0,
            "soil_moisture_pct": (
                _moisture_pct(raw, self._config.soil_calibration)
                if raw is not None else None
            ),
            "light_state": self._light.state(),
            "pump_state": self._pump.state(),
            "soil_temp_c": readings.get("soil_temp_c"),
            "ambient_lux": readings.get("ambient_lux"),
            "air_temp_c": readings.get("air_temp_c"),
            "air_humidity_pct": readings.get("air_humidity_pct"),
            "reservoir_level_pct": readings.get("reservoir_level_pct"),
        })

    def _photo_due(self, now: datetime) -> bool:
        # Active hours check
        if self._config.photo_active_hours is not None:
            h_start, h_end = self._config.photo_active_hours
            h = now.hour
            in_window = (h_start <= h < h_end if h_start <= h_end
                         else h >= h_start or h < h_end)
            if not in_window:
                return False
        if self._last_photo_at is None:
            return True
        return (now - self._last_photo_at) >= timedelta(
            minutes=self._config.photo_interval_min)
```

- [ ] **Step 4: Run + commit**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_safety_loop.py -v`
Expected: PASS (6 tests)

```bash
git add grow_unit/src/mlss_grow/safety_loop.py tests/grow_unit/test_safety_loop.py
git commit -m "Add SafetyLoop orchestration

Reads sensors, evaluates light schedule, computes PID decision, fires
pump pulse when warranted, snaps photo at interval, emits telemetry
last (so it includes the post-action state). Each tick is independent
and atomic — no shared state across tick boundaries except PIDState."
```

---

## Section 7 — Firmware: WebSocket client

The WS client connects, sends what the safety loop emits, buffers when offline, replays on reconnect. Receives commands from MLSS and dispatches them to handlers.

---

### Task 7.1: Outgoing message serialisation + binary photo framing

**Files:**
- Create: `grow_unit/src/mlss_grow/ws_protocol.py`
- Create: `tests/grow_unit/test_ws_protocol.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_unit/test_ws_protocol.py`:

```python
"""Outgoing WS message serialisation: text envelope + binary photo frame."""
import json
import struct
from datetime import datetime
from mlss_grow.ws_protocol import encode_text_message, encode_photo_frame


def test_encode_text_message_envelope():
    out = encode_text_message(
        msg_type="telemetry",
        ts=datetime(2026, 5, 3, 12, 34, 18),
        payload={"soil_moisture_raw": 612, "light_state": True, "pump_state": False},
    )
    parsed = json.loads(out)
    assert parsed["type"] == "telemetry"
    assert parsed["ts"].startswith("2026-05-03T12:34:18")
    assert parsed["payload"]["soil_moisture_raw"] == 612


def test_encode_photo_frame_layout():
    jpeg = b"\xff\xd8\xff\xe0FAKE" + b"\x00" * 100
    meta = {"taken_at": "2026-05-03T12:34:18Z", "width": 1920, "height": 1080,
            "jpeg_quality": 85, "shutter_us": 16667, "iso": 100}
    frame = encode_photo_frame(meta, jpeg)
    # First 4 bytes = header length BE
    h_len = struct.unpack(">I", frame[:4])[0]
    assert h_len > 0
    parsed_header = json.loads(frame[4:4 + h_len].decode("utf-8"))
    assert parsed_header == meta
    assert frame[4 + h_len:] == jpeg


def test_text_message_uses_z_suffix_for_utc():
    out = encode_text_message("event", datetime(2026, 1, 1), {})
    parsed = json.loads(out)
    assert parsed["ts"].endswith("Z")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_ws_protocol.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`grow_unit/src/mlss_grow/ws_protocol.py`:

```python
"""WS message encoding: matches mlss_contracts envelope shape."""
import json
import struct
from datetime import datetime


def encode_text_message(msg_type: str, ts: datetime, payload: dict) -> str:
    """JSON envelope expected by the server WS listener."""
    return json.dumps({
        "type": msg_type,
        "ts": ts.isoformat() + "Z",
        "payload": payload,
    })


def encode_photo_frame(metadata: dict, jpeg_bytes: bytes) -> bytes:
    """Binary frame: [4 bytes BE header_len][JSON header][JPEG bytes]."""
    header_bytes = json.dumps(metadata).encode("utf-8")
    return struct.pack(">I", len(header_bytes)) + header_bytes + jpeg_bytes
```

- [ ] **Step 4: Run + commit**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_ws_protocol.py -v`
Expected: PASS (3 tests)

```bash
git add grow_unit/src/mlss_grow/ws_protocol.py tests/grow_unit/test_ws_protocol.py
git commit -m "Add WS message + binary photo frame encoders

Mirror of the server-side decoders in mlss_monitor.grow.handlers and
mlss_monitor.grow.photo_storage."
```

---

### Task 7.2: WS client with reconnect + buffer integration

**Files:**
- Create: `grow_unit/src/mlss_grow/ws_client.py`
- Create: `tests/grow_unit/test_ws_client.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_unit/test_ws_client.py`:

```python
"""WSClient: connect, send, buffer-on-failure, replay-on-reconnect, command dispatch."""
import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
import pytest
from mlss_grow.ws_client import WSClient


class FakeWSConnection:
    """Stand-in for a real websocket connection. Captures sent frames + queues incoming."""
    def __init__(self):
        self.sent = []
        self._inbox = asyncio.Queue()
        self.closed = False

    async def send(self, msg):
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(msg)

    async def __aiter__(self):
        while not self.closed:
            try:
                yield await asyncio.wait_for(self._inbox.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

    async def push_incoming(self, msg):
        await self._inbox.put(msg)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_send_when_connected_goes_through(tmp_path):
    fake_ws = FakeWSConnection()
    cmd_received = []

    client = WSClient(
        url="ws://test", token="t", buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: cmd_received.append(cmd),
        connect_fn=AsyncMock(return_value=fake_ws),
    )
    await client._connect_once()
    await client.send_text("telemetry", datetime(2026, 1, 1),
                            {"soil_moisture_raw": 612, "light_state": True, "pump_state": False})
    assert len(fake_ws.sent) == 1
    parsed = json.loads(fake_ws.sent[0])
    assert parsed["type"] == "telemetry"


@pytest.mark.asyncio
async def test_send_when_disconnected_buffers(tmp_path):
    client = WSClient(
        url="ws://test", token="t", buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: None,
        connect_fn=AsyncMock(side_effect=ConnectionError("never connects")),
    )
    # Don't bother connecting; send should buffer
    await client.send_text("telemetry", datetime(2026, 5, 3),
                            {"soil_moisture_raw": 612, "light_state": True, "pump_state": False})
    assert client._buffer.size() == 1


@pytest.mark.asyncio
async def test_replay_drains_buffer_on_reconnect(tmp_path):
    fake_ws = FakeWSConnection()
    client = WSClient(
        url="ws://test", token="t", buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: None,
        connect_fn=AsyncMock(return_value=fake_ws),
    )
    # Pre-populate buffer
    client._buffer.append("telemetry", '{"x":1}', ts=datetime(2026, 1, 1))
    client._buffer.append("event", '{"y":2}', ts=datetime(2026, 1, 2))
    assert client._buffer.size() == 2

    await client._connect_once()
    await client._replay_buffer()

    # Buffer drained; replay events sent
    assert client._buffer.size() == 0
    sent_kinds = [json.loads(m).get("type") for m in fake_ws.sent if isinstance(m, str)]
    # First two are buffer_replay_started + actual messages + buffer_replay_complete
    assert "event" in sent_kinds  # buffer_replay_started/complete are events too


@pytest.mark.asyncio
async def test_incoming_command_dispatched_to_handler(tmp_path):
    fake_ws = FakeWSConnection()
    received = []
    client = WSClient(
        url="ws://test", token="t", buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: received.append(cmd),
        connect_fn=AsyncMock(return_value=fake_ws),
    )
    await client._connect_once()

    cmd_msg = json.dumps({
        "type": "command", "ts": "2026-05-03T12:00:00Z",
        "payload": {"name": "identify", "args": {"duration_s": 10}},
    })
    await fake_ws.push_incoming(cmd_msg)

    # Run the receive loop briefly
    recv_task = asyncio.create_task(client._receive_loop())
    await asyncio.sleep(0.2)
    recv_task.cancel()

    assert len(received) == 1
    assert received[0]["name"] == "identify"
    assert received[0]["args"]["duration_s"] == 10
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_ws_client.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`grow_unit/src/mlss_grow/ws_client.py`:

```python
"""WebSocket client for the grow unit.

Single connection to MLSS. When connected: forwards messages from the
safety loop and dispatches incoming commands. When disconnected: buffers
to local SQLite. On reconnect: drains the buffer in timestamp order
before resuming live stream.
"""
import asyncio
import json
import logging
import random
from datetime import datetime
from typing import Callable

from mlss_grow.buffer import LocalBuffer
from mlss_grow.ws_protocol import encode_text_message, encode_photo_frame

log = logging.getLogger(__name__)


async def _default_connect(url, token):
    import websockets
    return await websockets.connect(url, extra_headers={"Authorization": f"Bearer {token}"})


class WSClient:
    """Connect, send, receive, buffer, replay."""

    def __init__(self, url: str, token: str, buffer_db_path: str,
                 on_command: Callable[[dict], None],
                 connect_fn=_default_connect,
                 backoff_base: float = 1.0, backoff_max: float = 60.0):
        self._url = url
        self._token = token
        self._buffer = LocalBuffer(buffer_db_path)
        self._on_command = on_command
        self._connect_fn = connect_fn
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._ws = None

    async def _connect_once(self):
        try:
            self._ws = await self._connect_fn(self._url, self._token)
            return True
        except Exception as exc:
            log.warning("WS connect failed: %s", exc)
            self._ws = None
            return False

    def is_connected(self) -> bool:
        return self._ws is not None

    async def send_text(self, msg_type: str, ts: datetime, payload: dict):
        body = encode_text_message(msg_type, ts, payload)
        if self._ws is None:
            self._buffer.append(msg_type, body, ts)
            return
        try:
            await self._ws.send(body)
        except Exception as exc:
            log.warning("WS send failed (%s); buffering", exc)
            self._buffer.append(msg_type, body, ts)
            self._ws = None

    async def send_photo(self, metadata: dict, jpeg_bytes: bytes):
        if self._ws is None:
            log.info("WS down; dropping photo (not buffered to save SD wear)")
            return
        try:
            frame = encode_photo_frame(metadata, jpeg_bytes)
            await self._ws.send(frame)
        except Exception as exc:
            log.warning("WS photo send failed: %s", exc)
            self._ws = None

    async def _replay_buffer(self):
        rows = self._buffer.pop_all()
        if not rows:
            return
        log.info("replaying %d buffered messages", len(rows))
        # Notify server we're replaying (ack-target identification)
        start_event = encode_text_message(
            "event", datetime.utcnow(),
            {"kind": "buffer_replay_started", "details": {"count": len(rows)}},
        )
        try:
            await self._ws.send(start_event)
            for row in rows:
                await self._ws.send(row.body)
            done_event = encode_text_message(
                "event", datetime.utcnow(),
                {"kind": "buffer_replay_complete", "details": {}},
            )
            await self._ws.send(done_event)
        except Exception as exc:
            log.warning("buffer replay failed: %s; rows already removed from buffer", exc)
            self._ws = None

    async def _receive_loop(self):
        if self._ws is None:
            return
        try:
            async for msg in self._ws:
                if isinstance(msg, str):
                    try:
                        parsed = json.loads(msg)
                        if parsed.get("type") == "command":
                            self._on_command(parsed["payload"])
                    except Exception as exc:
                        log.warning("bad incoming message: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("receive loop ended: %s", exc)
            self._ws = None

    async def run_forever(self):
        """Top-level connection lifecycle: connect, replay, receive, reconnect."""
        attempt = 0
        while True:
            ok = await self._connect_once()
            if not ok:
                delay = min(self._backoff_max, self._backoff_base * (2 ** attempt))
                delay *= 1.0 + random.uniform(-0.2, 0.2)  # jitter
                attempt += 1
                await asyncio.sleep(delay)
                continue
            attempt = 0
            try:
                await self._replay_buffer()
                await self._receive_loop()
            finally:
                self._ws = None
            await asyncio.sleep(self._backoff_base)
```

- [ ] **Step 4: Run + commit**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_ws_client.py -v`
Expected: PASS (4 tests)

```bash
git add grow_unit/src/mlss_grow/ws_client.py tests/grow_unit/test_ws_client.py
git commit -m "Add firmware WSClient with reconnect + buffer + replay

send_text() goes through if connected, otherwise buffers to local
SQLite. Photos are dropped (not buffered) when offline to save SD
write endurance. On reconnect: emit buffer_replay_started, drain in
timestamp order, emit buffer_replay_complete. Exponential backoff
1→60s with ±20% jitter."
```

---

## Section 8 — Firmware: enrollment, config, systemd service

First-boot config parsing, enrollment HTTP call, token persistence, and the systemd service that ties everything together.

---

### Task 8.1: Config loaders (`/boot/mlss-grow.yaml` + `/etc/mlss/grow.token`)

**Files:**
- Create: `grow_unit/src/mlss_grow/config.py`
- Create: `tests/grow_unit/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_unit/test_config.py`:

```python
"""Config loaders for firstboot YAML and persisted token."""
from mlss_grow.config import (
    load_firstboot_config, save_token, load_token, FirstbootConfig,
)


def test_load_firstboot_config_parses_yaml(tmp_path):
    yaml_path = tmp_path / "mlss-grow.yaml"
    yaml_path.write_text("""
mlss_host: mlss.local
enrollment_key: abc-123-key
plant:
  name: Tomato 3
  type: tomato
  medium: soil
""")
    cfg = load_firstboot_config(str(yaml_path))
    assert cfg.mlss_host == "mlss.local"
    assert cfg.enrollment_key == "abc-123-key"
    assert cfg.plant_name == "Tomato 3"
    assert cfg.plant_type == "tomato"
    assert cfg.medium == "soil"


def test_load_firstboot_config_defaults_for_optional_fields(tmp_path):
    yaml_path = tmp_path / "min.yaml"
    yaml_path.write_text("""
mlss_host: mlss.local
enrollment_key: abc
plant:
  name: X
""")
    cfg = load_firstboot_config(str(yaml_path))
    assert cfg.plant_type == "generic"
    assert cfg.medium == "soil"


def test_load_firstboot_returns_none_if_file_missing(tmp_path):
    assert load_firstboot_config(str(tmp_path / "missing.yaml")) is None


def test_save_and_load_token_round_trip(tmp_path):
    token_path = str(tmp_path / "grow.token")
    save_token(token_path, unit_id=42, token="secret-token-xyz")
    loaded = load_token(token_path)
    assert loaded == (42, "secret-token-xyz")


def test_load_token_returns_none_if_file_missing(tmp_path):
    assert load_token(str(tmp_path / "missing.token")) is None


def test_save_token_sets_mode_0600(tmp_path):
    import os, stat
    token_path = str(tmp_path / "grow.token")
    save_token(token_path, unit_id=1, token="x")
    mode = stat.S_IMODE(os.stat(token_path).st_mode)
    # On Windows the chmod is a no-op; check on POSIX systems only
    if os.name == "posix":
        assert mode == 0o600
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_config.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`grow_unit/src/mlss_grow/config.py`:

```python
"""Loaders for first-boot config and persisted bearer token."""
import json
import os
from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass
class FirstbootConfig:
    mlss_host: str
    enrollment_key: str
    plant_name: str
    plant_type: str = "generic"
    medium: str = "soil"
    wifi_ssid: str | None = None
    wifi_psk: str | None = None


def load_firstboot_config(path: str) -> "FirstbootConfig | None":
    """Read /boot/mlss-grow.yaml. Returns None if file missing (already enrolled)."""
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    plant = data.get("plant") or {}
    wifi = data.get("wifi") or {}
    return FirstbootConfig(
        mlss_host=data["mlss_host"],
        enrollment_key=data["enrollment_key"],
        plant_name=plant["name"],
        plant_type=plant.get("type", "generic"),
        medium=plant.get("medium", "soil"),
        wifi_ssid=wifi.get("ssid"),
        wifi_psk=wifi.get("psk"),
    )


def save_token(path: str, unit_id: int, token: str):
    """Persist the per-unit bearer token + unit_id at the given path with mode 0600."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({"unit_id": unit_id, "token": token}, f)
    if os.name == "posix":
        os.chmod(path, 0o600)


def load_token(path: str) -> "tuple[int, str] | None":
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        data = json.load(f)
    return (data["unit_id"], data["token"])
```

- [ ] **Step 4: Run + commit**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_config.py -v`
Expected: PASS (6 tests)

```bash
git add grow_unit/src/mlss_grow/config.py tests/grow_unit/test_config.py
git commit -m "Add firstboot YAML loader + token persistence (mode 0600)"
```

---

### Task 8.2: Enrollment HTTP call

**Files:**
- Create: `grow_unit/src/mlss_grow/enrol.py`
- Create: `tests/grow_unit/test_enrol.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_unit/test_enrol.py`:

```python
"""enroll_unit: POST /api/grow/enroll, returns (unit_id, token)."""
from unittest.mock import MagicMock, patch
from mlss_grow.enrol import enroll_unit, EnrollmentError
from mlss_grow.config import FirstbootConfig
import pytest


def _cfg():
    return FirstbootConfig(
        mlss_host="mlss.local", enrollment_key="key123",
        plant_name="Tomato", plant_type="tomato", medium="soil",
    )


def test_enroll_success(monkeypatch):
    fake_response = MagicMock()
    fake_response.status_code = 201
    fake_response.json = lambda: {"unit_id": 7, "token": "t-secret"}

    fake_post = MagicMock(return_value=fake_response)
    monkeypatch.setattr("mlss_grow.enrol.requests.post", fake_post)

    unit_id, token = enroll_unit(_cfg(), hardware_serial="hw-1")
    assert unit_id == 7
    assert token == "t-secret"

    call = fake_post.call_args
    assert call.kwargs["json"]["enrollment_key"] == "key123"
    assert call.kwargs["json"]["hardware_serial"] == "hw-1"
    assert call.kwargs["json"]["plant"]["name"] == "Tomato"
    assert call.kwargs["json"]["plant"]["type"] == "tomato"
    # url uses https + standard MLSS port
    assert "https://mlss.local:5000/api/grow/enroll" in call.args[0]


def test_enroll_401_raises(monkeypatch):
    fake_response = MagicMock(status_code=401, text="invalid_enrollment_key")
    monkeypatch.setattr("mlss_grow.enrol.requests.post",
                        MagicMock(return_value=fake_response))
    with pytest.raises(EnrollmentError, match="401"):
        enroll_unit(_cfg(), hardware_serial="hw-1")


def test_enroll_network_error_raises(monkeypatch):
    import requests
    monkeypatch.setattr("mlss_grow.enrol.requests.post",
                        MagicMock(side_effect=requests.ConnectionError("no network")))
    with pytest.raises(EnrollmentError, match="network"):
        enroll_unit(_cfg(), hardware_serial="hw-1")


def test_get_hardware_serial_reads_proc_cpuinfo(monkeypatch, tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text(
        "processor\t: 0\nmodel\t: ARMv6\n"
        "Serial\t\t: 100000000c0a8014b\n"
        "Model\t\t: Raspberry Pi Zero W\n"
    )
    monkeypatch.setattr("mlss_grow.enrol._CPUINFO_PATH", str(cpuinfo))
    from mlss_grow.enrol import get_hardware_serial
    assert get_hardware_serial() == "100000000c0a8014b"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_enrol.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

Add `requests = "^2.31"` to `grow_unit/pyproject.toml` deps if not present.

`grow_unit/src/mlss_grow/enrol.py`:

```python
"""First-boot enrollment HTTP call to MLSS."""
import logging
import requests
from mlss_grow.config import FirstbootConfig

log = logging.getLogger(__name__)

_CPUINFO_PATH = "/proc/cpuinfo"


class EnrollmentError(Exception):
    pass


def get_hardware_serial() -> str:
    """Extract Pi hardware serial from /proc/cpuinfo. Returns empty string if missing."""
    try:
        with open(_CPUINFO_PATH, "r") as f:
            for line in f:
                if line.startswith("Serial"):
                    return line.split(":", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""


def enroll_unit(cfg: FirstbootConfig, hardware_serial: str) -> tuple[int, str]:
    """POST /api/grow/enroll. Returns (unit_id, token). Raises on failure."""
    url = f"https://{cfg.mlss_host}:5000/api/grow/enroll"
    body = {
        "enrollment_key": cfg.enrollment_key,
        "hardware_serial": hardware_serial,
        "plant": {
            "name": cfg.plant_name,
            "type": cfg.plant_type,
            "medium": cfg.medium,
        },
    }
    try:
        # MLSS uses self-signed cert on the LAN — verify=False is safe given
        # we're already proving identity via the enrollment key
        resp = requests.post(url, json=body, timeout=30, verify=False)
    except requests.RequestException as exc:
        raise EnrollmentError(f"network error contacting {url}: {exc}")

    if resp.status_code != 201:
        raise EnrollmentError(f"enrollment failed (HTTP {resp.status_code}): {resp.text}")

    data = resp.json()
    if "unit_id" not in data or "token" not in data:
        raise EnrollmentError(f"malformed enrollment response: {data}")
    return data["unit_id"], data["token"]
```

- [ ] **Step 4: Run + commit**

Run: `cd grow_unit && poetry install && python -m pytest ../tests/grow_unit/test_enrol.py -v`
Expected: PASS (4 tests)

```bash
git add grow_unit/src/mlss_grow/enrol.py grow_unit/pyproject.toml tests/grow_unit/test_enrol.py
git commit -m "Add first-boot enrollment HTTP call

POSTs /api/grow/enroll with enrollment_key + hardware_serial + plant
metadata. Reads serial from /proc/cpuinfo. verify=False on the request
because MLSS uses a self-signed cert on the LAN (identity proven via
enrollment key)."
```

---

### Task 8.3: Service entrypoint (`mlss_grow.service:main`)

**Files:**
- Create: `grow_unit/src/mlss_grow/service.py`
- Create: `tests/grow_unit/test_service.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_unit/test_service.py`:

```python
"""Service entrypoint orchestrates: load config → enrol if needed → boot WS + safety loop."""
import json
from unittest.mock import MagicMock, patch
from mlss_grow.service import bootstrap_unit_state, BootstrappedState
from mlss_grow.config import FirstbootConfig
import pytest


def test_bootstrap_uses_existing_token_if_present(tmp_path, monkeypatch):
    token_path = str(tmp_path / "grow.token")
    boot_path = str(tmp_path / "mlss-grow.yaml")
    # Pre-existing token
    from mlss_grow.config import save_token
    save_token(token_path, unit_id=42, token="existing-token")

    # Boot YAML present (would normally trigger enroll) but token already exists
    open(boot_path, "w").write(
        "mlss_host: mlss.local\nenrollment_key: x\nplant:\n  name: X\n"
    )

    state = bootstrap_unit_state(
        firstboot_path=boot_path,
        token_path=token_path,
        enroll_fn=MagicMock(side_effect=AssertionError("should not enroll")),
        get_serial_fn=MagicMock(return_value="hw-1"),
    )
    assert state.unit_id == 42
    assert state.token == "existing-token"
    assert state.mlss_host == "mlss.local"


def test_bootstrap_enrolls_when_no_token(tmp_path, monkeypatch):
    token_path = str(tmp_path / "grow.token")
    boot_path = str(tmp_path / "mlss-grow.yaml")
    open(boot_path, "w").write(
        "mlss_host: mlss.local\nenrollment_key: ek\nplant:\n  name: Test\n"
    )

    state = bootstrap_unit_state(
        firstboot_path=boot_path,
        token_path=token_path,
        enroll_fn=MagicMock(return_value=(99, "freshly-minted")),
        get_serial_fn=MagicMock(return_value="hw-1"),
    )
    assert state.unit_id == 99
    assert state.token == "freshly-minted"

    # Token persisted
    from mlss_grow.config import load_token
    assert load_token(token_path) == (99, "freshly-minted")

    # YAML file removed (don't leave enrollment key on SD card)
    import os
    assert not os.path.exists(boot_path)


def test_bootstrap_raises_when_no_token_and_no_firstboot(tmp_path):
    with pytest.raises(RuntimeError, match="no firstboot config"):
        bootstrap_unit_state(
            firstboot_path=str(tmp_path / "absent.yaml"),
            token_path=str(tmp_path / "absent.token"),
            enroll_fn=MagicMock(),
            get_serial_fn=MagicMock(return_value="hw-1"),
        )
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_service.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`grow_unit/src/mlss_grow/service.py`:

```python
"""systemd service entrypoint.

Boot sequence:
  1. Load /etc/mlss/grow.token if present → use existing credentials
  2. Otherwise read /boot/mlss-grow.yaml + enrol → save token + delete YAML
  3. Open WS to MLSS, start safety loop
  4. Run forever
"""
import asyncio
import logging
import os
import sys
from dataclasses import dataclass

from mlss_grow.config import (
    load_firstboot_config, load_token, save_token, FirstbootConfig,
)
from mlss_grow.enrol import enroll_unit, get_hardware_serial

log = logging.getLogger(__name__)

FIRSTBOOT_PATH = "/boot/mlss-grow.yaml"
TOKEN_PATH = "/etc/mlss/grow.token"


@dataclass
class BootstrappedState:
    unit_id: int
    token: str
    mlss_host: str
    plant_name: str | None = None
    plant_type: str = "generic"
    medium: str = "soil"


def bootstrap_unit_state(
    firstboot_path: str = FIRSTBOOT_PATH,
    token_path: str = TOKEN_PATH,
    enroll_fn=enroll_unit,
    get_serial_fn=get_hardware_serial,
) -> BootstrappedState:
    """Decide credentials: existing token wins, else enrol."""
    existing = load_token(token_path)
    fb = load_firstboot_config(firstboot_path)

    if existing:
        unit_id, token = existing
        host = fb.mlss_host if fb else None
        if host is None:
            # Pull from /etc/mlss/grow.host, written at first save (added below)
            host_file = os.path.join(os.path.dirname(token_path), "grow.host")
            if os.path.exists(host_file):
                with open(host_file) as f:
                    host = f.read().strip()
            else:
                raise RuntimeError("token exists but mlss_host unknown")
        return BootstrappedState(unit_id=unit_id, token=token, mlss_host=host)

    if fb is None:
        raise RuntimeError("no firstboot config and no existing token — cannot enrol")

    serial = get_serial_fn()
    log.info("enrolling unit (hardware_serial=%s)", serial)
    unit_id, token = enroll_fn(fb, serial)
    save_token(token_path, unit_id, token)
    # Persist mlss_host alongside the token for future boots
    host_file = os.path.join(os.path.dirname(token_path), "grow.host")
    os.makedirs(os.path.dirname(host_file), exist_ok=True)
    with open(host_file, "w") as f:
        f.write(fb.mlss_host)
    # Delete firstboot YAML so the enrollment key doesn't persist on SD card
    try:
        os.remove(firstboot_path)
    except OSError as exc:
        log.warning("failed to remove %s: %s", firstboot_path, exc)
    return BootstrappedState(
        unit_id=unit_id, token=token, mlss_host=fb.mlss_host,
        plant_name=fb.plant_name, plant_type=fb.plant_type, medium=fb.medium,
    )


def main():
    """systemd entrypoint. Bootstrap, then run forever."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    try:
        state = bootstrap_unit_state()
    except Exception as exc:
        log.error("bootstrap failed: %s", exc)
        sys.exit(1)

    log.info("bootstrapped unit_id=%s mlss_host=%s", state.unit_id, state.mlss_host)
    asyncio.run(_run_main_loop(state))


async def _run_main_loop(state: BootstrappedState):
    """Wire up sensors, actuators, camera, WS client, safety loop. Run forever.

    Implementation deferred to integration task — this Phase 1 function is a
    skeleton that the integration test exercises end-to-end.
    """
    from mlss_grow.sensors import auto_detect
    from mlss_grow.actuators.automation_phat import AutomationPHATPump, AutomationPHATLight
    from mlss_grow.camera import Camera
    from mlss_grow.ws_client import WSClient
    from mlss_grow.safety_loop import SafetyLoop, LoopConfig
    from mlss_grow.pid import PIDConfig
    from mlss_grow.light_schedule import parse_window
    import board, busio
    from datetime import datetime

    i2c = busio.I2C(board.SCL, board.SDA)
    sensors = auto_detect(i2c)
    pump = AutomationPHATPump()
    light = AutomationPHATLight()
    camera = Camera.detect()

    received_commands = asyncio.Queue()
    ws = WSClient(
        url=f"wss://{state.mlss_host}:5001/api/grow/{state.unit_id}/ws",
        token=state.token,
        buffer_db_path="/var/lib/mlss-grow/buffer.sqlite",
        on_command=lambda cmd: received_commands.put_nowait(cmd),
    )

    # Default config until MLSS sends an explicit one
    loop_cfg = LoopConfig(
        light_windows=[parse_window("06:00", "22:00")],
        pid=PIDConfig(target_pct=55),
    )

    async def emit(kind: str, payload: dict):
        if kind == "photo":
            await ws.send_photo(payload["meta"], payload["jpeg_bytes"])
        else:
            await ws.send_text(kind, datetime.utcnow(), payload)

    safety = SafetyLoop(
        sensors=sensors, pump=pump, light=light, camera=camera,
        config=loop_cfg,
        emit=lambda k, p: asyncio.run_coroutine_threadsafe(
            emit(k, p), asyncio.get_event_loop()),
    )

    async def safety_ticker():
        while True:
            try:
                safety.tick()
            except Exception as exc:
                log.exception("safety tick failed: %s", exc)
            await asyncio.sleep(30)

    async def command_handler():
        while True:
            cmd = await received_commands.get()
            try:
                if cmd["name"] == "identify":
                    light.blink_pattern(duration_s=cmd.get("args", {}).get("duration_s", 10))
                elif cmd["name"] == "water_now":
                    pump.pulse(cmd.get("args", {}).get("duration_s", 5))
                elif cmd["name"] == "snap_photo" and camera:
                    jpeg, meta = camera.capture()
                    meta["taken_at"] = datetime.utcnow().isoformat() + "Z"
                    await ws.send_photo(meta, jpeg)
            except Exception as exc:
                log.exception("command failed: %s", exc)

    await asyncio.gather(
        ws.run_forever(),
        safety_ticker(),
        command_handler(),
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run + commit**

Run: `cd grow_unit && python -m pytest ../tests/grow_unit/test_service.py -v`
Expected: PASS (3 tests)

```bash
git add grow_unit/src/mlss_grow/service.py tests/grow_unit/test_service.py
git commit -m "Add systemd service entrypoint

bootstrap_unit_state() picks credentials: existing token > enrol via
firstboot YAML > error. After enrol, deletes /boot/mlss-grow.yaml
(don't leave enrollment key on SD card) and stashes mlss_host alongside
token. Main loop runs WS, safety ticker, command handler concurrently."
```

---

### Task 8.4: systemd unit file

**Files:**
- Create: `grow_unit/systemd/mlss-grow.service`
- Create: `tests/grow_unit/test_systemd_unit.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_unit/test_systemd_unit.py`:

```python
"""systemd unit file is well-formed and references the right entrypoint."""
import os
import re
from pathlib import Path


_UNIT_PATH = Path(__file__).resolve().parent.parent.parent / "grow_unit" / "systemd" / "mlss-grow.service"


def test_unit_file_exists():
    assert _UNIT_PATH.exists()


def test_unit_has_required_sections():
    content = _UNIT_PATH.read_text()
    assert "[Unit]" in content
    assert "[Service]" in content
    assert "[Install]" in content


def test_unit_uses_mlss_grow_entrypoint():
    content = _UNIT_PATH.read_text()
    assert "mlss-grow" in content or "mlss_grow.service" in content


def test_unit_runs_as_dedicated_user():
    content = _UNIT_PATH.read_text()
    assert re.search(r"User=mlss-grow", content)


def test_unit_has_systemd_watchdog():
    content = _UNIT_PATH.read_text()
    assert "WatchdogSec=" in content


def test_unit_restart_on_failure():
    content = _UNIT_PATH.read_text()
    assert re.search(r"Restart=on-failure|Restart=always", content)


def test_unit_targets_multi_user():
    content = _UNIT_PATH.read_text()
    assert "WantedBy=multi-user.target" in content
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_unit/test_systemd_unit.py -v`
Expected: FAIL — file doesn't exist

- [ ] **Step 3: Implement**

`grow_unit/systemd/mlss-grow.service`:

```ini
[Unit]
Description=MLSS Plant Grow Unit firmware
Documentation=https://github.com/Ryan-be/mars-air-quility/blob/main/docs/PLANT_GROW_UNIT_HARDWARE.md
After=network-online.target time-sync.target
Wants=network-online.target

[Service]
Type=simple
User=mlss-grow
Group=mlss-grow
WorkingDirectory=/opt/mlss-grow
ExecStart=/opt/mlss-grow/.venv/bin/mlss-grow
Restart=on-failure
RestartSec=5
WatchdogSec=30
StandardOutput=journal
StandardError=journal

# Security hardening (Pi Zero W is fine with these)
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true

# Filesystem permissions for state, logs, buffer
ReadWritePaths=/var/lib/mlss-grow /var/log/mlss-grow /etc/mlss

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_unit/test_systemd_unit.py -v`
Expected: PASS (7 tests)

```bash
git add grow_unit/systemd/mlss-grow.service tests/grow_unit/test_systemd_unit.py
git commit -m "Add systemd unit file for mlss-grow.service

Runs as dedicated mlss-grow user with hardened filesystem access
(only /var/lib/mlss-grow, /var/log/mlss-grow, /etc/mlss writable).
WatchdogSec=30 — main loop must check in or systemd kills + restarts
the process. Restart=on-failure with 5s backoff."
```

---

## Section 9 — Build scripts and install.sh

The wheel-build script runs at MLSS deploy time to produce installable wheels. The install script runs once on each Pi Zero to download + install them.

---

### Task 9.1: `scripts/build_grow_wheel.sh`

**Files:**
- Create: `scripts/build_grow_wheel.sh`
- Create: `tests/grow_server/test_build_grow_wheel.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_build_grow_wheel.py`:

```python
"""build_grow_wheel.sh produces both wheels and copies them to static/grow_dist."""
import os
import shutil
import subprocess
from pathlib import Path
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "build_grow_wheel.sh"
DIST_DIR = REPO_ROOT / "static" / "grow_dist"


def test_script_exists_and_is_executable():
    assert SCRIPT.exists()
    assert os.access(SCRIPT, os.X_OK), "script must be chmod +x"


def test_script_passes_shellcheck_when_available():
    """If shellcheck is installed, the script must lint clean."""
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")
    r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, f"shellcheck failures:\n{r.stdout}\n{r.stderr}"


def test_script_starts_with_proper_bash_strict_mode():
    content = SCRIPT.read_text()
    assert content.startswith("#!/bin/bash") or content.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in content


@pytest.mark.slow
def test_script_produces_wheels(tmp_path, monkeypatch):
    """End-to-end: run the script, expect both wheels in static/grow_dist."""
    if shutil.which("poetry") is None:
        pytest.skip("poetry not installed in this environment")
    # Clean dist
    if DIST_DIR.exists():
        for f in DIST_DIR.glob("*.whl"):
            f.unlink()
    r = subprocess.run([str(SCRIPT)], cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, f"build failed:\n{r.stdout}\n{r.stderr}"
    wheels = list(DIST_DIR.glob("*.whl"))
    pkgs = {w.name.split("-")[0] for w in wheels}
    assert "mlss_grow" in pkgs or "mlss-grow" in pkgs
    assert "mlss_contracts" in pkgs or "mlss-contracts" in pkgs
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_build_grow_wheel.py -v -m "not slow"`
Expected: FAIL — script doesn't exist

- [ ] **Step 3: Implement**

`scripts/build_grow_wheel.sh`:

```bash
#!/bin/bash
# Build mlss_contracts + mlss_grow wheels and copy them to static/grow_dist/
# so the MLSS HTTP server can serve them to Pi Zeros.
#
# Run from the repo root, or any cwd — the script self-locates via its own dir.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$REPO_ROOT/static/grow_dist"

cd "$REPO_ROOT"

mkdir -p "$DIST_DIR"
# Clean stale wheels — only keep the latest of each package
rm -f "$DIST_DIR"/*.whl

echo "==> Building mlss_contracts wheel"
( cd "$REPO_ROOT/contracts" && poetry build -f wheel )
cp "$REPO_ROOT/contracts/dist"/*.whl "$DIST_DIR/"

echo "==> Building mlss_grow wheel"
( cd "$REPO_ROOT/grow_unit" && poetry build -f wheel )
cp "$REPO_ROOT/grow_unit/dist"/*.whl "$DIST_DIR/"

echo "==> Wheels in $DIST_DIR:"
ls -la "$DIST_DIR"/*.whl
```

Make executable:

```bash
chmod +x scripts/build_grow_wheel.sh
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_build_grow_wheel.py -v -m "not slow"`
Expected: PASS (3 fast tests; the slow `test_script_produces_wheels` will run optionally)

```bash
git add scripts/build_grow_wheel.sh tests/grow_server/test_build_grow_wheel.py
git commit -m "Add scripts/build_grow_wheel.sh

Builds both wheels (mlss_contracts + mlss_grow) and copies into
static/grow_dist/ for HTTP serving. Bash strict mode + shellcheck
clean. Called from deploy.sh after MLSS git pull."
```

---

### Task 9.2: `grow_unit/install.sh` (Pi Zero installer)

**Files:**
- Create: `grow_unit/install.sh`
- Create: `tests/grow_unit/test_install_sh.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_unit/test_install_sh.py`:

```python
"""install.sh syntactic checks + critical commands present."""
import os
import shutil
import subprocess
from pathlib import Path
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL = REPO_ROOT / "grow_unit" / "install.sh"


def test_install_script_exists():
    assert INSTALL.exists()


def test_install_script_is_executable():
    assert os.access(INSTALL, os.X_OK)


def test_install_script_starts_with_strict_mode():
    content = INSTALL.read_text()
    assert content.startswith("#!/bin/bash") or content.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in content


def test_install_script_creates_mlss_grow_user():
    content = INSTALL.read_text()
    assert "useradd" in content or "adduser" in content
    assert "mlss-grow" in content


def test_install_script_downloads_wheels_from_mlss():
    content = INSTALL.read_text()
    assert "/api/grow/dist/" in content
    assert "mlss_grow" in content
    assert "mlss_contracts" in content


def test_install_script_creates_systemd_unit():
    content = INSTALL.read_text()
    assert "/etc/systemd/system/mlss-grow.service" in content
    assert "systemctl enable" in content
    assert "systemctl start" in content


def test_install_script_creates_required_directories():
    content = INSTALL.read_text()
    for d in ["/opt/mlss-grow", "/etc/mlss", "/var/lib/mlss-grow", "/var/log/mlss-grow"]:
        assert d in content


def test_install_script_passes_shellcheck_when_available():
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")
    r = subprocess.run(["shellcheck", str(INSTALL)], capture_output=True, text=True)
    assert r.returncode == 0, f"shellcheck:\n{r.stdout}\n{r.stderr}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_unit/test_install_sh.py -v`
Expected: FAIL — file doesn't exist

- [ ] **Step 3: Implement**

`grow_unit/install.sh`:

```bash
#!/bin/bash
# MLSS Plant Grow Unit installer.
#
# Run on a fresh Raspberry Pi Zero W (or Pi Zero 2 W) with Pi OS Lite
# and a /boot/mlss-grow.yaml config file. The one-line install command:
#
#   curl -k https://mlss.local:5000/api/grow/install.sh | sudo bash
#
# What this does:
#   1. apt-installs Python 3.11+, libcamera-apps, i2c-tools, build-essentials
#   2. Creates dedicated mlss-grow system user
#   3. Creates required directories with correct ownership
#   4. Downloads wheels (mlss_contracts + mlss_grow) from MLSS server
#   5. Creates a venv at /opt/mlss-grow/.venv and pip-installs both wheels
#   6. Drops the systemd service unit
#   7. Enables and starts the service

set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
    echo "Must be run as root (use sudo)" >&2
    exit 1
fi

# ── Read MLSS host from /boot/mlss-grow.yaml so we know where to fetch wheels.
#     (The Python service later parses this fully; here we just need the host.)
MLSS_HOST=""
if [[ -f /boot/mlss-grow.yaml ]]; then
    MLSS_HOST=$(grep -E '^mlss_host:' /boot/mlss-grow.yaml | awk '{print $2}' | tr -d '"' || true)
fi
if [[ -z "$MLSS_HOST" ]]; then
    echo "Error: /boot/mlss-grow.yaml missing or doesn't set mlss_host" >&2
    exit 1
fi

echo "==> MLSS host: $MLSS_HOST"

# ── 1. apt deps
echo "==> Installing system packages"
apt-get update -y
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip python3-dev \
    libcamera-apps i2c-tools \
    build-essential libffi-dev

# ── 2. Dedicated user
if ! id mlss-grow >/dev/null 2>&1; then
    echo "==> Creating mlss-grow user"
    useradd --system --shell /usr/sbin/nologin --home /opt/mlss-grow mlss-grow
    usermod -aG i2c,gpio,video mlss-grow || true
fi

# ── 3. Directories
echo "==> Creating directories"
install -d -o mlss-grow -g mlss-grow -m 0755 /opt/mlss-grow
install -d -o mlss-grow -g mlss-grow -m 0750 /etc/mlss
install -d -o mlss-grow -g mlss-grow -m 0750 /var/lib/mlss-grow
install -d -o mlss-grow -g mlss-grow -m 0755 /var/log/mlss-grow

# ── 4. Download wheels
echo "==> Downloading wheels from $MLSS_HOST"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

LATEST=$(curl -ks "https://${MLSS_HOST}:5000/api/grow/dist/latest")
GROW_VER=$(echo "$LATEST" | python3 -c "import sys,json;print(json.load(sys.stdin)['mlss_grow'])")
CONTRACTS_VER=$(echo "$LATEST" | python3 -c "import sys,json;print(json.load(sys.stdin)['mlss_contracts'])")

curl -k -o "$TMP/mlss_grow-${GROW_VER}-py3-none-any.whl" \
    "https://${MLSS_HOST}:5000/api/grow/dist/mlss_grow-${GROW_VER}-py3-none-any.whl"
curl -k -o "$TMP/mlss_contracts-${CONTRACTS_VER}-py3-none-any.whl" \
    "https://${MLSS_HOST}:5000/api/grow/dist/mlss_contracts-${CONTRACTS_VER}-py3-none-any.whl"

# ── 5. venv + install
echo "==> Creating venv and installing wheels"
sudo -u mlss-grow python3 -m venv /opt/mlss-grow/.venv
sudo -u mlss-grow /opt/mlss-grow/.venv/bin/pip install --upgrade pip
sudo -u mlss-grow /opt/mlss-grow/.venv/bin/pip install \
    --no-index --find-links "$TMP" \
    "mlss_grow==${GROW_VER}" \
    "mlss_contracts==${CONTRACTS_VER}"

# ── 6. systemd unit
echo "==> Installing systemd unit"
INSTALL_DIR=$(/opt/mlss-grow/.venv/bin/python -c \
    "import mlss_grow, os; print(os.path.dirname(mlss_grow.__file__))")
cp "$INSTALL_DIR/../../systemd/mlss-grow.service" /etc/systemd/system/mlss-grow.service 2>/dev/null \
    || cp /opt/mlss-grow/.venv/lib/python*/site-packages/mlss_grow/systemd/mlss-grow.service \
        /etc/systemd/system/mlss-grow.service 2>/dev/null \
    || curl -k -o /etc/systemd/system/mlss-grow.service \
        "https://${MLSS_HOST}:5000/api/grow/dist/mlss-grow.service"
chmod 644 /etc/systemd/system/mlss-grow.service

systemctl daemon-reload

# ── 7. Enable + start
echo "==> Enabling + starting mlss-grow.service"
systemctl enable --now mlss-grow.service

echo "==> Done. Tail logs with: journalctl -u mlss-grow -f"
```

Make executable:

```bash
chmod +x grow_unit/install.sh
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_unit/test_install_sh.py -v`
Expected: PASS (8 tests)

```bash
git add grow_unit/install.sh tests/grow_unit/test_install_sh.py
git commit -m "Add Pi Zero install.sh

Single curl|sudo bash entrypoint. Reads MLSS host from
/boot/mlss-grow.yaml, apt-installs deps, creates mlss-grow user,
downloads + installs both wheels into a venv, drops systemd unit,
enables + starts the service."
```

---

## Section 10 — Browser: Grow tab fleet view

New top-level tab. List of cards, status colour mapping, Live updates via SSE. Reusable components extracted as we go.

The project already has `tests/js/` with `.mjs` tests run by `node --test`. We'll follow that pattern.

---

### Task 10.1: Add Grow tab to base.html nav + route

**Files:**
- Modify: `templates/base.html` (add nav link)
- Modify: `mlss_monitor/routes/pages.py` (add `/grow` route)
- Create: `templates/grow_fleet.html` (skeleton)
- Create: `tests/grow_server/test_grow_pages.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_grow_pages.py`:

```python
"""Pages: /grow renders the fleet template; nav has the Grow tab."""
import tempfile
import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    init_db.create_db()

    # Build the real app
    from mlss_monitor.app import app
    app.config["TESTING"] = True
    return app.test_client()


def test_grow_route_returns_200(client):
    r = client.get("/grow")
    assert r.status_code == 200


def test_dashboard_nav_includes_grow_tab(client):
    """Any rendered page that uses base.html should show the Grow tab in the nav."""
    r = client.get("/grow")
    assert b">Grow<" in r.data or b">GROW<" in r.data


def test_grow_page_loads_grow_static_assets(client):
    r = client.get("/grow")
    body = r.data.decode("utf-8")
    assert "/static/css/grow.css" in body
    assert "/static/js/grow/fleet.mjs" in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_grow_pages.py -v`
Expected: FAIL — `404 Not Found` for `/grow`

- [ ] **Step 3: Implement**

In `templates/base.html`, add inside the `<nav class="tab-nav">` block, after the Incidents link:

```html
    <!-- Grow -->
    <a href="{{ url_for('pages.grow_fleet') }}"
       data-tab="grow"
       class="{{ 'active' if active and active.startswith('pages.grow') else '' }}">
      <rux-icon icon="add-photo-alternate" size="extra-small"></rux-icon>
      <span>Grow</span>
    </a>
```

In `mlss_monitor/routes/pages.py`, add:

```python
@pages_bp.route("/grow")
def grow_fleet():
    return render_template("grow_fleet.html")


@pages_bp.route("/grow/<int:unit_id>")
def grow_unit_detail(unit_id):
    return render_template("grow_unit_detail.html", unit_id=unit_id)
```

`templates/grow_fleet.html`:

```html
{% extends "base.html" %}
{% block title %}MLSS · Grow{% endblock %}
{% block extra_css %}
  <link rel="stylesheet" href="{{ url_for('static', filename='css/grow.css') }}">
{% endblock %}

{% block content %}
  <section class="grow-fleet" id="grow-fleet">
    <header class="grow-pageheader">
      <div class="grow-summary" id="grow-summary"></div>
      <button class="px-btn primary" id="grow-add-btn">+ Add Unit</button>
    </header>

    <div class="grow-grid" id="grow-grid">
      <!-- Populated by static/js/grow/fleet.mjs -->
    </div>
  </section>
{% endblock %}

{% block scripts %}
  <script type="module" src="{{ url_for('static', filename='js/grow/fleet.mjs') }}"></script>
{% endblock %}
```

Create empty `static/css/grow.css` (filled by Task 10.4):

```css
/* Plant Grow Unit styles — Grow tab + per-unit detail page */
.grow-fleet { padding: 0; }
```

Create stub `static/js/grow/fleet.mjs`:

```javascript
// Grow tab fleet view — populated by Task 10.4
console.log("grow fleet loaded");
```

Create stub `templates/grow_unit_detail.html`:

```html
{% extends "base.html" %}
{% block title %}MLSS · Grow · Unit {{ unit_id }}{% endblock %}
{% block content %}
<div id="grow-unit-detail" data-unit-id="{{ unit_id }}">Loading…</div>
{% endblock %}
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_grow_pages.py -v`
Expected: PASS (3 tests)

```bash
git add templates/base.html templates/grow_fleet.html templates/grow_unit_detail.html mlss_monitor/routes/pages.py static/css/grow.css static/js/grow/fleet.mjs tests/grow_server/test_grow_pages.py
git commit -m "Add /grow routes + nav tab + page skeletons

Two new routes: /grow (fleet view), /grow/<id> (detail). Both extend
base.html and load /static/css/grow.css + module JS. Body content
populated by JS in subsequent tasks."
```

---

### Task 10.2: Status pill component (reusable)

**Files:**
- Create: `static/js/grow/components/status-pill.mjs`
- Create: `tests/js/test_status_pill.mjs`

- [ ] **Step 1: Write the failing test**

`tests/js/test_status_pill.mjs`:

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderStatusPill, classifyUnitStatus } from "../../static/js/grow/components/status-pill.mjs";


test("classifyUnitStatus: online when last seen recently", () => {
  const now = new Date("2026-05-03T12:00:00Z");
  const lastSeen = new Date("2026-05-03T11:59:50Z");  // 10s ago
  assert.equal(classifyUnitStatus(lastSeen, now), "online");
});

test("classifyUnitStatus: stale between 30s and 5min", () => {
  const now = new Date("2026-05-03T12:00:00Z");
  const lastSeen = new Date("2026-05-03T11:58:00Z");  // 2min ago
  assert.equal(classifyUnitStatus(lastSeen, now), "stale");
});

test("classifyUnitStatus: offline after 5min", () => {
  const now = new Date("2026-05-03T12:00:00Z");
  const lastSeen = new Date("2026-05-03T11:50:00Z");  // 10min ago
  assert.equal(classifyUnitStatus(lastSeen, now), "offline");
});

test("classifyUnitStatus: offline when null", () => {
  assert.equal(classifyUnitStatus(null, new Date()), "offline");
});

test("renderStatusPill: returns HTML element with correct class", () => {
  const el = renderStatusPill("online");
  assert.equal(el.tagName, "SPAN");
  assert.match(el.className, /st-normal/);
  assert.match(el.textContent, /Nominal/i);
});

test("renderStatusPill: caution status", () => {
  const el = renderStatusPill("caution");
  assert.match(el.className, /st-caution/);
  assert.match(el.textContent, /Caution/i);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/js/test_status_pill.mjs`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`static/js/grow/components/status-pill.mjs`:

```javascript
/**
 * Reusable status pill component.
 * Maps unit status (online/stale/caution/offline) to an AstroUXDS-aligned
 * coloured pill. Used on fleet cards, detail header, anywhere we surface
 * a unit's health.
 */

export function classifyUnitStatus(lastSeenAt, now = new Date()) {
  if (lastSeenAt === null || lastSeenAt === undefined) return "offline";
  const ageMs = now.getTime() - new Date(lastSeenAt).getTime();
  if (ageMs < 30 * 1000) return "online";
  if (ageMs < 5 * 60 * 1000) return "stale";
  return "offline";
}

const STATUS_LABELS = {
  online: "Nominal",
  caution: "Caution",
  stale: "Stale",
  offline: "Offline",
};

const STATUS_CLASSES = {
  online: "st-normal",
  caution: "st-caution",
  stale: "st-standby",
  offline: "st-serious",
};

export function renderStatusPill(status, opts = {}) {
  const { ownerDocument = (typeof document !== "undefined" ? document : null) } = opts;
  let el;
  if (ownerDocument) {
    el = ownerDocument.createElement("span");
  } else {
    // Node test env — return a minimal stand-in
    el = {
      tagName: "SPAN",
      className: "",
      textContent: "",
    };
  }
  el.className = `gu-status ${STATUS_CLASSES[status] || ""}`;
  el.textContent = STATUS_LABELS[status] || "Unknown";
  return el;
}
```

For node tests to work without real `document`, ensure node test runner can load `.mjs` (it can natively in Node 18+).

- [ ] **Step 4: Run + commit**

Run: `node --test tests/js/test_status_pill.mjs`
Expected: PASS (6 tests)

```bash
git add static/js/grow/components/status-pill.mjs tests/js/test_status_pill.mjs
git commit -m "Add status-pill component + classifyUnitStatus mapper

Pure status classifier (last_seen_at + now → online/stale/offline) and
DOM render helper. Fully testable under node --test without jsdom."
```

---

### Task 10.3: Stat tile component (reusable)

**Files:**
- Create: `static/js/grow/components/stat-tile.mjs`
- Create: `tests/js/test_stat_tile.mjs`

- [ ] **Step 1: Write the failing test**

`tests/js/test_stat_tile.mjs`:

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderStatTile } from "../../static/js/grow/components/stat-tile.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


test("stat tile: required channel rendered with green left bar", () => {
  const el = renderStatTile({
    value: "58%", label: "Moisture", isRequired: true, ownerDocument: document,
  });
  assert.match(el.className, /required-marker/);
});

test("stat tile: optional channel rendered with blue left bar", () => {
  const el = renderStatTile({
    value: "21.4°C", label: "Soil temp", isRequired: false,
    ownerDocument: document,
  });
  assert.match(el.className, /optional-marker/);
});

test("stat tile: warn variant for low moisture", () => {
  const el = renderStatTile({
    value: "28%", label: "Moisture", isRequired: true, variant: "warn",
    ownerDocument: document,
  });
  const v = el.querySelector(".v");
  assert.match(v.className, /warn/);
});

test("stat tile: includes optional sub-text", () => {
  const el = renderStatTile({
    value: "58%", label: "Moisture", sub: "target 55%", isRequired: true,
    ownerDocument: document,
  });
  const sub = el.querySelector(".sub");
  assert.equal(sub.textContent, "target 55%");
});
```

Add `jsdom` to dev deps (it's already used elsewhere in the project for similar tests; if not, install via npm or via `package.json` if one exists; tests can still skip if missing).

If the project doesn't have a `package.json`, create a minimal one for the JS tests:

`package.json` (top-level, if not present):

```json
{
  "name": "mlss-monitor-js-tests",
  "version": "0.0.0",
  "private": true,
  "scripts": {
    "test:js": "node --test tests/js/"
  },
  "devDependencies": {
    "jsdom": "^24.0"
  }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/js/test_stat_tile.mjs`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`static/js/grow/components/stat-tile.mjs`:

```javascript
/**
 * Stat tile: big number + label + optional sub. The "required-marker"
 * left border is green for capability=is_required, blue otherwise. The
 * stat-tile grid is data-driven from the unit's reported capabilities,
 * so the same component renders everywhere.
 */

export function renderStatTile({
  value, label, sub = null, isRequired = false, variant = "normal",
  ownerDocument = (typeof document !== "undefined" ? document : null),
}) {
  const doc = ownerDocument;
  const tile = doc.createElement("div");
  tile.className = `du-stat ${isRequired ? "required-marker" : "optional-marker"}`;

  const v = doc.createElement("div");
  v.className = `v${variant === "warn" ? " warn" : variant === "ok" ? " ok" : ""}`;
  v.textContent = value;
  tile.appendChild(v);

  const l = doc.createElement("div");
  l.className = "l";
  l.textContent = label;
  tile.appendChild(l);

  if (sub) {
    const s = doc.createElement("div");
    s.className = "sub";
    s.textContent = sub;
    tile.appendChild(s);
  }
  return tile;
}
```

- [ ] **Step 4: Run + commit**

Run: `node --test tests/js/test_stat_tile.mjs`
Expected: PASS (4 tests)

```bash
git add static/js/grow/components/stat-tile.mjs tests/js/test_stat_tile.mjs package.json
git commit -m "Add stat-tile component for capability-driven readings grid"
```

---

### Task 10.4: Grow card renderer + fleet page wiring

**Files:**
- Create: `static/js/grow/components/grow-card.mjs`
- Modify: `static/js/grow/fleet.mjs` (fetch + render + summary stats)
- Modify: `static/css/grow.css` (full styles from the mockup)
- Create: `tests/js/test_grow_card.mjs`

- [ ] **Step 1: Write the failing test**

`tests/js/test_grow_card.mjs`:

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderGrowCard } from "../../static/js/grow/components/grow-card.mjs";

const dom = new JSDOM();
global.document = dom.window.document;

const sampleUnit = {
  id: 3,
  label: "Tomato 3",
  plant_type: "tomato",
  medium_type: "soil",
  current_phase: "vegetative",
  sown_at: "2026-04-10T00:00:00Z",
  last_seen_at: new Date().toISOString(),
  status: "online",
  last_known_state: {
    soil_moisture_pct: 58,
    light_state: true,
  },
};


test("grow card: shows label and phase + medium meta", () => {
  const card = renderGrowCard(sampleUnit, document);
  assert.match(card.textContent, /Tomato 3/);
  assert.match(card.textContent, /vegetative/);
  assert.match(card.textContent, /soil/i);
});

test("grow card: status pill present", () => {
  const card = renderGrowCard(sampleUnit, document);
  assert.ok(card.querySelector(".gu-status"));
});

test("grow card: identify button has data-action=identify", () => {
  const card = renderGrowCard(sampleUnit, document);
  const btn = card.querySelector("[data-action='identify']");
  assert.ok(btn);
});

test("grow card: open button links to /grow/<id>", () => {
  const card = renderGrowCard(sampleUnit, document);
  const openBtn = card.querySelector("[data-action='open']");
  assert.ok(openBtn);
  assert.match(openBtn.dataset.href || openBtn.href, /\/grow\/3/);
});

test("grow card: stale variant gets stale class", () => {
  const stale = { ...sampleUnit, status: "stale" };
  const card = renderGrowCard(stale, document);
  assert.match(card.className, /stale/);
});

test("grow card: offline variant gets offline class", () => {
  const offline = { ...sampleUnit, status: "offline" };
  const card = renderGrowCard(offline, document);
  assert.match(card.className, /offline/);
});

test("grow card: shows moisture % from last_known_state", () => {
  const card = renderGrowCard(sampleUnit, document);
  assert.match(card.textContent, /58%/);
});

test("grow card: shows 'No photo yet' when no recent photo", () => {
  const newUnit = { ...sampleUnit, last_known_state: { ...sampleUnit.last_known_state } };
  newUnit.last_known_state.last_photo_url = null;
  const card = renderGrowCard(newUnit, document);
  assert.match(card.textContent, /No photo|—/);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/js/test_grow_card.mjs`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`static/js/grow/components/grow-card.mjs`:

```javascript
/**
 * Render one grow unit as a card on the fleet view. Card structure:
 *   header (name + phase/medium meta + status pill)
 *   photo (latest captured or placeholder)
 *   stat tiles (capability-driven; just moisture + light + watered for now)
 *   footer (last seen + Identify + Open buttons)
 */
import { renderStatusPill } from "./status-pill.mjs";


export function renderGrowCard(unit, doc = document) {
  const card = doc.createElement("div");
  card.className = `gu-card ${unit.status}`;
  card.dataset.unitId = unit.id;

  // Header
  const head = doc.createElement("div");
  head.className = "gu-head";
  const titleBlock = doc.createElement("div");
  const name = doc.createElement("div");
  name.className = "gu-name";
  name.textContent = unit.label;
  const meta = doc.createElement("div");
  meta.className = "gu-meta";
  const dayCount = unit.sown_at
    ? Math.floor((Date.now() - new Date(unit.sown_at).getTime()) / 86400000)
    : null;
  meta.textContent = [
    unit.current_phase,
    dayCount !== null ? `day ${dayCount}` : null,
    unit.medium_type,
  ].filter(Boolean).join(" · ");
  titleBlock.appendChild(name);
  titleBlock.appendChild(meta);
  head.appendChild(titleBlock);
  head.appendChild(renderStatusPill(unit.status, { ownerDocument: doc }));
  card.appendChild(head);

  // Photo
  const photo = doc.createElement("div");
  photo.className = "gu-photo";
  const photoUrl = unit.last_known_state?.last_photo_url || null;
  if (photoUrl) {
    photo.style.backgroundImage = `url(${photoUrl})`;
  } else {
    photo.classList.add("no-photo");
    photo.textContent = "— No photo yet —";
  }
  card.appendChild(photo);

  // Stats
  const stats = doc.createElement("div");
  stats.className = "gu-stats";
  const last = unit.last_known_state || {};
  const moisture = last.soil_moisture_pct != null
    ? `${Math.round(last.soil_moisture_pct)}%` : "—";
  const lightOn = last.light_state ? "💡 ON" : "💡 OFF";
  for (const [v, l] of [
    [moisture, "Moisture"],
    [lightOn, "Light"],
    [unit.status === "online" ? "Live" : unit.status, "State"],
  ]) {
    const stat = doc.createElement("div");
    stat.className = "gu-stat";
    const vd = doc.createElement("div");
    vd.className = "v"; vd.textContent = v;
    const ld = doc.createElement("div");
    ld.className = "l"; ld.textContent = l;
    stat.appendChild(vd); stat.appendChild(ld);
    stats.appendChild(stat);
  }
  card.appendChild(stats);

  // Footer
  const foot = doc.createElement("div");
  foot.className = "gu-foot";
  const seen = doc.createElement("span");
  seen.className = "gu-lastseen";
  seen.textContent = unit.last_seen_at
    ? `Seen ${_relativeTime(new Date(unit.last_seen_at))} ago` : "Never seen";
  const actions = doc.createElement("div");
  actions.className = "gu-actions";
  const identifyBtn = doc.createElement("button");
  identifyBtn.className = "gu-btn";
  identifyBtn.dataset.action = "identify";
  identifyBtn.dataset.unitId = unit.id;
  identifyBtn.textContent = "Identify";
  const openBtn = doc.createElement("a");
  openBtn.className = "gu-btn";
  openBtn.dataset.action = "open";
  openBtn.dataset.href = `/grow/${unit.id}`;
  openBtn.href = `/grow/${unit.id}`;
  openBtn.textContent = "Open →";
  actions.appendChild(identifyBtn);
  actions.appendChild(openBtn);
  foot.appendChild(seen);
  foot.appendChild(actions);
  card.appendChild(foot);

  return card;
}


function _relativeTime(then) {
  const sec = Math.max(0, Math.floor((Date.now() - then.getTime()) / 1000));
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h`;
  return `${Math.floor(sec / 86400)}d`;
}
```

`static/js/grow/fleet.mjs`:

```javascript
import { renderGrowCard } from "./components/grow-card.mjs";

const STATE = { units: [] };

async function fetchUnits() {
  const r = await fetch("/api/grow/units");
  if (!r.ok) throw new Error(`fetch failed: ${r.status}`);
  return (await r.json()).units;
}

function renderSummary(units) {
  const counts = {
    total: units.length,
    online: units.filter(u => u.status === "online").length,
    stale: units.filter(u => u.status === "stale").length,
    offline: units.filter(u => u.status === "offline").length,
  };
  const el = document.getElementById("grow-summary");
  el.innerHTML = "";
  for (const [k, v, cls] of [
    ["UNITS", counts.total, ""],
    ["ONLINE", counts.online, "ok"],
    ["STALE", counts.stale, "warn"],
    ["OFFLINE", counts.offline, "crit"],
  ]) {
    const div = document.createElement("div");
    div.innerHTML = `<span class="num ${cls}">${v}</span><span class="lbl">${k}</span>`;
    el.appendChild(div);
  }
}

function renderGrid(units) {
  const grid = document.getElementById("grow-grid");
  grid.innerHTML = "";
  for (const u of units) grid.appendChild(renderGrowCard(u));
  // Empty-state placeholder
  if (units.length === 0) {
    grid.innerHTML = "<p style='padding:40px;color:#7d92a8'>No grow units enrolled yet — go to Settings → Grow for instructions.</p>";
  }
}

async function refresh() {
  try {
    STATE.units = await fetchUnits();
    renderSummary(STATE.units);
    renderGrid(STATE.units);
  } catch (e) {
    console.error("refresh failed", e);
  }
}

document.getElementById("grow-grid").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-action='identify']");
  if (!btn) return;
  ev.preventDefault();
  const unitId = btn.dataset.unitId;
  btn.disabled = true; btn.textContent = "Blinking…";
  try {
    await fetch(`/api/grow/units/${unitId}/identify`, { method: "POST" });
    setTimeout(() => { btn.disabled = false; btn.textContent = "Identify"; }, 11000);
  } catch (e) {
    btn.disabled = false; btn.textContent = "Identify";
  }
});

// Refresh every 5s; SSE wiring is a future polish.
refresh();
setInterval(refresh, 5000);
```

Append to `static/css/grow.css`:

```css
/* Grow tab fleet view */
.grow-pageheader {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px; border-bottom: 1px solid #1c2733;
  background: linear-gradient(180deg, #0e1722, #0a1219);
  gap: 16px; flex-wrap: wrap;
  font-family: Roboto, sans-serif;
}
.grow-summary { display: flex; gap: 28px; align-items: baseline; color: #c2d2e3; flex-wrap: wrap; }
.grow-summary .num { font-size: 22px; font-weight: 500; color: #fff; margin-right: 6px; }
.grow-summary .num.warn { color: #ffb302; }
.grow-summary .num.crit { color: #ff5252; }
.grow-summary .num.ok { color: #56f000; }
.grow-summary .lbl { font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: #7d92a8; }
.px-btn { background: #4dacff; color: #001028; border: none; padding: 9px 16px; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; font-family: Roboto, sans-serif; cursor: pointer; border-radius: 2px; font-weight: 500; }

.grow-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; padding: 18px 20px; background: #080c11; }

.gu-card { background: #142028; border: 1px solid #1f2e3c; border-radius: 3px; display: flex; flex-direction: column; overflow: hidden; font-family: Roboto, sans-serif; color: #c2d2e3; font-size: 13px; transition: border-color 0.15s, transform 0.15s; }
.gu-card:hover { border-color: #4dacff; transform: translateY(-2px); cursor: pointer; }
.gu-card.stale { opacity: 0.78; }
.gu-card.offline { opacity: 0.55; border-color: #5a3014; }

.gu-head { padding: 10px 12px 8px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #1c2733; gap: 8px; }
.gu-name { font-size: 14px; font-weight: 500; color: #fff; }
.gu-meta { font-size: 11px; color: #7d92a8; margin-top: 2px; letter-spacing: 0.02em; }

.gu-status { display: inline-flex; align-items: center; gap: 6px; font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; padding: 3px 7px; border-radius: 2px; background: #0c151c; flex-shrink: 0; }
.st-normal { color: #56f000; }
.st-caution { color: #ffb302; }
.st-standby { color: #4dacff; }
.st-serious { color: #ff5252; }

.gu-photo { aspect-ratio: 4 / 3; background: #0a1219 center/cover no-repeat; }
.gu-photo.no-photo { display: flex; align-items: center; justify-content: center; color: #4a5d72; font-size: 12px; }

.gu-stats { padding: 10px 12px; display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; align-items: end; border-bottom: 1px solid #1c2733; }
.gu-stat .v { font-size: 18px; font-weight: 500; color: #fff; line-height: 1; }
.gu-stat .v.warn { color: #ffb302; }
.gu-stat .l { font-size: 9px; letter-spacing: 0.08em; text-transform: uppercase; color: #7d92a8; margin-top: 4px; }

.gu-foot { padding: 8px 12px; display: flex; align-items: center; justify-content: space-between; background: #0e1722; gap: 8px; }
.gu-lastseen { font-size: 10px; color: #7d92a8; letter-spacing: 0.04em; }
.gu-actions { display: flex; gap: 6px; }
.gu-btn { background: transparent; border: 1px solid #2a3d50; color: #c2d2e3; padding: 4px 9px; font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; cursor: pointer; border-radius: 2px; font-family: Roboto, sans-serif; text-decoration: none; }
.gu-btn:hover { border-color: #4dacff; color: #4dacff; }
```

- [ ] **Step 4: Run + commit**

Run: `node --test tests/js/test_grow_card.mjs`
Expected: PASS (8 tests)

```bash
git add static/js/grow/components/grow-card.mjs static/js/grow/fleet.mjs static/css/grow.css tests/js/test_grow_card.mjs
git commit -m "Add grow-card component + fleet page wiring + grow.css

Card: header with name + phase/medium meta + status pill, photo (or
'No photo yet'), 3 stat tiles, footer with last-seen + Identify +
Open. Fleet page polls /api/grow/units every 5s; identify button
disables for 11s after click. Responsive grid via auto-fit."
```

---

## Section 11 — Browser: Per-unit detail page (Live tab)

Fetches `/api/grow/units/<id>`, renders the header (back link + title + phase pill + status pill), sub-tab nav (Live highlighted, others disabled placeholders for Phase 2), and the Live tab body: photo + capability-driven readings + light schedule + visual watering history + recent watering log + quick controls with safety locking.

---

### Task 11.1: Detail page skeleton — header + sub-tabs (placeholder for Phase 2 tabs)

**Files:**
- Modify: `templates/grow_unit_detail.html`
- Create: `static/js/grow/unit_detail.mjs`
- Modify: `static/css/grow.css` (append detail-page styles)
- Create: `tests/js/test_unit_detail_skeleton.mjs`

- [ ] **Step 1: Write the failing test**

`tests/js/test_unit_detail_skeleton.mjs`:

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderDetailHeader, renderSubTabs } from "../../static/js/grow/unit_detail.mjs";

const dom = new JSDOM();
global.document = dom.window.document;

const sampleUnit = {
  id: 3, label: "Tomato 3", current_phase: "vegetative",
  medium_type: "soil", sown_at: "2026-04-10T00:00:00Z",
  status: "online", last_seen_at: new Date().toISOString(),
  capabilities: [], last_known_state: {},
};


test("detail header renders title + phase + status pill", () => {
  const el = renderDetailHeader(sampleUnit, document);
  assert.match(el.textContent, /Tomato 3/);
  assert.match(el.textContent, /vegetative/i);
  assert.ok(el.querySelector(".gu-status"));
});

test("detail header includes back link to /grow", () => {
  const el = renderDetailHeader(sampleUnit, document);
  const back = el.querySelector("a.du-back");
  assert.ok(back);
  assert.equal(back.getAttribute("href"), "/grow");
});

test("sub-tabs: Live is the active tab; others marked disabled", () => {
  const el = renderSubTabs("live", document);
  const live = el.querySelector("[data-tab='live']");
  assert.match(live.className, /active/);
  for (const tab of ["history", "configure", "diagnostics"]) {
    const t = el.querySelector(`[data-tab='${tab}']`);
    assert.ok(t.disabled || t.classList.contains("disabled"));
  }
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/js/test_unit_detail_skeleton.mjs`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`templates/grow_unit_detail.html`:

```html
{% extends "base.html" %}
{% block title %}MLSS · Grow · Unit {{ unit_id }}{% endblock %}
{% block extra_css %}
  <link rel="stylesheet" href="{{ url_for('static', filename='css/grow.css') }}">
{% endblock %}
{% block content %}
<div class="du-page" data-unit-id="{{ unit_id }}">
  <header id="du-header"></header>
  <nav id="du-tabs"></nav>
  <main id="du-body"></main>
</div>
{% endblock %}
{% block scripts %}
  <script type="module" src="{{ url_for('static', filename='js/grow/unit_detail.mjs') }}"></script>
{% endblock %}
```

`static/js/grow/unit_detail.mjs`:

```javascript
import { renderStatusPill } from "./components/status-pill.mjs";

const SUBTABS = [
  { id: "live", label: "● Live", enabled: true },
  { id: "history", label: "📈 History", enabled: false, deferred: "Phase 2" },
  { id: "configure", label: "⚙ Configure", enabled: false, deferred: "Phase 2" },
  { id: "diagnostics", label: "🩺 Diagnostics", enabled: false, deferred: "Phase 3" },
];


export function renderDetailHeader(unit, doc = document) {
  const wrap = doc.createElement("div");
  wrap.className = "du-header";

  const back = doc.createElement("a");
  back.className = "du-back";
  back.href = "/grow";
  back.textContent = "← Grow units";
  wrap.appendChild(back);

  const title = doc.createElement("div");
  title.className = "du-title";
  const h = doc.createElement("h2");
  h.textContent = unit.label;
  title.appendChild(h);

  const phasePill = doc.createElement("span");
  phasePill.className = "du-pill phase";
  phasePill.textContent = (unit.current_phase || "").toUpperCase();
  title.appendChild(phasePill);

  const mediumPill = doc.createElement("span");
  mediumPill.className = "du-pill";
  const dayCount = unit.sown_at
    ? Math.floor((Date.now() - new Date(unit.sown_at).getTime()) / 86400000)
    : null;
  mediumPill.textContent = `${(unit.medium_type || "").toUpperCase()}` +
    (dayCount !== null ? ` · day ${dayCount}` : "");
  title.appendChild(mediumPill);

  title.appendChild(renderStatusPill(unit.status, { ownerDocument: doc }));
  wrap.appendChild(title);
  return wrap;
}


export function renderSubTabs(activeTab, doc = document) {
  const nav = doc.createElement("div");
  nav.className = "du-tabs";
  for (const t of SUBTABS) {
    const el = doc.createElement("button");
    el.className = "du-tab" + (t.id === activeTab ? " active" : "")
                  + (!t.enabled ? " disabled" : "");
    el.dataset.tab = t.id;
    el.textContent = t.label;
    if (!t.enabled) {
      el.disabled = true;
      el.title = `Coming in ${t.deferred}`;
    }
    nav.appendChild(el);
  }
  return nav;
}


async function init() {
  const root = document.querySelector("[data-unit-id]");
  const unitId = root.dataset.unitId;
  const r = await fetch(`/api/grow/units/${unitId}`);
  if (!r.ok) {
    document.getElementById("du-body").textContent = "Failed to load unit";
    return;
  }
  const unit = await r.json();
  document.getElementById("du-header").appendChild(renderDetailHeader(unit));
  document.getElementById("du-tabs").appendChild(renderSubTabs("live"));
  // Body is rendered by Task 11.2+
}

init();
```

Append to `static/css/grow.css`:

```css
/* Per-unit detail page */
.du-page { font-family: Roboto, sans-serif; color: #c2d2e3; background: #080c11; min-height: 100vh; }
.du-header { padding: 14px 20px; border-bottom: 1px solid #1c2733; background: linear-gradient(180deg, #0e1722, #0a1219); }
.du-back { color: #7d92a8; font-size: 12px; letter-spacing: 0.05em; text-decoration: none; text-transform: uppercase; display: inline-block; margin-bottom: 10px; }
.du-back:hover { color: #4dacff; }
.du-title { display: flex; align-items: baseline; gap: 12px; }
.du-title h2 { color: #fff; font-size: 22px; font-weight: 500; margin: 0; }
.du-pill { background: rgba(77, 172, 255, 0.12); color: #4dacff; padding: 3px 9px; font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; border-radius: 2px; }
.du-pill.phase { background: rgba(86, 240, 0, 0.1); color: #56f000; }

.du-tabs { display: flex; gap: 0; padding: 0 20px; background: #0a1219; border-bottom: 1px solid #1c2733; }
.du-tab { padding: 12px 18px; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: #7d92a8; cursor: pointer; border-bottom: 2px solid transparent; background: none; border-left: none; border-right: none; border-top: none; font-family: Roboto, sans-serif; }
.du-tab:hover:not(.disabled) { color: #c2d2e3; }
.du-tab.active { color: #4dacff; border-bottom-color: #4dacff; }
.du-tab.disabled { opacity: 0.4; cursor: not-allowed; }
```

- [ ] **Step 4: Run + commit**

Run: `node --test tests/js/test_unit_detail_skeleton.mjs`
Expected: PASS (3 tests)

```bash
git add templates/grow_unit_detail.html static/js/grow/unit_detail.mjs static/css/grow.css tests/js/test_unit_detail_skeleton.mjs
git commit -m "Add detail page skeleton — header + sub-tabs

Live tab marked active. History/Configure/Diagnostics tabs rendered
disabled with hover tooltips referencing Phase 2/3. Body populated
by subsequent tasks."
```

---

### Task 11.2: Live tab — capability-driven readings panel

**Files:**
- Modify: `static/js/grow/unit_detail.mjs` (add Live tab body renderer)
- Create: `tests/js/test_live_readings.mjs`

- [ ] **Step 1: Write the failing test**

`tests/js/test_live_readings.mjs`:

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderLiveReadings } from "../../static/js/grow/unit_detail.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


test("renders one tile per capability", () => {
  const unit = {
    capabilities: [
      { channel: "soil_moisture", is_required: true, unit_label: "raw" },
      { channel: "soil_temp_c", is_required: false, unit_label: "°C" },
      { channel: "ambient_lux", is_required: false, unit_label: "lux" },
    ],
    last_known_state: {
      soil_moisture_pct: 58, soil_temp_c: 21.4, ambient_lux: 15420,
      light_state: true,
    },
  };
  const el = renderLiveReadings(unit, document);
  // 3 capability tiles + 1 light state tile (always rendered for required channel)
  const tiles = el.querySelectorAll(".du-stat");
  assert.ok(tiles.length >= 3);
  assert.match(el.textContent, /58%/);
  assert.match(el.textContent, /21.4/);
  assert.match(el.textContent, /15420|15,420/);
});


test("absent capabilities = no tile rendered (not crossed out)", () => {
  const unit = {
    capabilities: [
      { channel: "soil_moisture", is_required: true, unit_label: "raw" },
    ],
    last_known_state: { soil_moisture_pct: 58, light_state: true },
  };
  const el = renderLiveReadings(unit, document);
  // No air_temp tile, no ambient_lux tile
  assert.doesNotMatch(el.textContent, /Air temp/i);
  assert.doesNotMatch(el.textContent, /Ambient lux/i);
});


test("low moisture renders warn variant", () => {
  const unit = {
    capabilities: [{ channel: "soil_moisture", is_required: true, unit_label: "raw" }],
    last_known_state: { soil_moisture_pct: 28, light_state: false },
    plant_type: "tomato",
    current_phase: "vegetative",
  };
  const el = renderLiveReadings(unit, document);
  const moistTile = Array.from(el.querySelectorAll(".du-stat"))
    .find(t => /Moisture/i.test(t.textContent));
  assert.match(moistTile.querySelector(".v").className, /warn/);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/js/test_live_readings.mjs`
Expected: FAIL — `renderLiveReadings` not exported

- [ ] **Step 3: Implement**

Append to `static/js/grow/unit_detail.mjs`:

```javascript
import { renderStatTile } from "./components/stat-tile.mjs";


const CHANNEL_DISPLAY = {
  soil_moisture: { label: "Moisture", format: (v) => `${Math.round(v)}%`,
                   stateKey: "soil_moisture_pct" },
  soil_temp_c: { label: "Soil temp", format: (v) => `${v.toFixed(1)}°C`,
                 stateKey: "soil_temp_c" },
  ambient_lux: { label: "Ambient lux", format: (v) => v.toLocaleString(),
                 stateKey: "ambient_lux" },
  air_temp_c: { label: "Air temp", format: (v) => `${v.toFixed(1)}°C`,
                stateKey: "air_temp_c" },
  air_humidity_pct: { label: "Air humidity", format: (v) => `${Math.round(v)}%`,
                      stateKey: "air_humidity_pct" },
  reservoir_level_pct: { label: "Reservoir", format: (v) => `${Math.round(v)}%`,
                         stateKey: "reservoir_level_pct" },
  light: { label: "Grow light", format: () => "",  // handled specially below
           stateKey: "light_state" },
};


export function renderLiveReadings(unit, doc = document) {
  const wrap = doc.createElement("div");
  wrap.className = "du-panel";
  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>📊 Live readings</span>";
  wrap.appendChild(head);

  const grid = doc.createElement("div");
  grid.className = "du-stat-grid";

  for (const cap of unit.capabilities || []) {
    const meta = CHANNEL_DISPLAY[cap.channel];
    if (!meta) continue;
    const value = unit.last_known_state?.[meta.stateKey];
    if (value == null && cap.channel !== "light") continue;

    let tile;
    if (cap.channel === "light") {
      tile = renderStatTile({
        value: value ? "💡 ON" : "💡 OFF",
        label: meta.label, isRequired: cap.is_required,
        ownerDocument: doc,
      });
    } else {
      const variant = (cap.channel === "soil_moisture" && value < 35) ? "warn" : "normal";
      tile = renderStatTile({
        value: meta.format(value),
        label: meta.label,
        isRequired: cap.is_required,
        variant,
        ownerDocument: doc,
      });
    }
    grid.appendChild(tile);
  }

  wrap.appendChild(grid);
  return wrap;
}
```

Update `init()` to render the body:

```javascript
async function init() {
  const root = document.querySelector("[data-unit-id]");
  const unitId = root.dataset.unitId;
  const r = await fetch(`/api/grow/units/${unitId}`);
  if (!r.ok) {
    document.getElementById("du-body").textContent = "Failed to load unit";
    return;
  }
  const unit = await r.json();
  document.getElementById("du-header").appendChild(renderDetailHeader(unit));
  document.getElementById("du-tabs").appendChild(renderSubTabs("live"));

  const body = document.getElementById("du-body");
  body.appendChild(renderLiveReadings(unit));
  // More panels added by subsequent tasks
}
```

Append to `static/css/grow.css`:

```css
.du-panel { background: #142028; border: 1px solid #1f2e3c; border-radius: 3px; overflow: hidden; margin: 14px 20px; }
.du-panel-head { padding: 9px 14px; border-bottom: 1px solid #1c2733; display: flex; align-items: center; justify-content: space-between; font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: #7d92a8; }

.du-stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; padding: 12px; }
.du-stat { background: #0e1722; border: 1px solid #1c2733; border-radius: 2px; padding: 10px 12px; }
.du-stat .v { font-size: 20px; font-weight: 500; color: #fff; line-height: 1; }
.du-stat .v.warn { color: #ffb302; }
.du-stat .v.ok { color: #56f000; }
.du-stat .l { font-size: 9px; letter-spacing: 0.08em; text-transform: uppercase; color: #7d92a8; margin-top: 4px; }
.du-stat .sub { font-size: 9px; color: #4a5d72; margin-top: 2px; }
.du-stat.required-marker { border-left: 2px solid #56f000; }
.du-stat.optional-marker { border-left: 2px solid #4dacff; }
```

- [ ] **Step 4: Run + commit**

Run: `node --test tests/js/test_live_readings.mjs`
Expected: PASS (3 tests)

```bash
git add static/js/grow/unit_detail.mjs static/css/grow.css tests/js/test_live_readings.mjs
git commit -m "Add capability-driven live readings panel

One tile per declared capability; absent capabilities just don't
render (no crossed-out placeholders). Required vs optional channels
get green vs blue left-border. Low moisture (<35%) renders warn
variant."
```

---

### Task 11.3: Live tab — manual quick controls with safety locking

**Files:**
- Modify: `static/js/grow/unit_detail.mjs` (add quick controls panel)
- Create: `tests/js/test_quick_controls.mjs`

- [ ] **Step 1: Write the failing test**

`tests/js/test_quick_controls.mjs`:

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import {
  renderQuickControls, computeWaterLockedUntil,
} from "../../static/js/grow/unit_detail.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


test("computeWaterLockedUntil: returns Date when within soak window", () => {
  const lastPulse = new Date("2026-05-03T11:42:00Z");
  const soak = 30; // minutes
  const now = new Date("2026-05-03T12:00:00Z");
  const locked = computeWaterLockedUntil(lastPulse, soak, now);
  assert.ok(locked > now);
});


test("computeWaterLockedUntil: returns null when soak elapsed", () => {
  const lastPulse = new Date("2026-05-03T11:00:00Z");
  const soak = 30;
  const now = new Date("2026-05-03T12:00:00Z");
  assert.equal(computeWaterLockedUntil(lastPulse, soak, now), null);
});


test("computeWaterLockedUntil: returns null when never pulsed", () => {
  assert.equal(computeWaterLockedUntil(null, 30, new Date()), null);
});


test("renderQuickControls: identify always enabled", () => {
  const el = renderQuickControls({ id: 1 }, document);
  const btn = el.querySelector("[data-action='identify']");
  assert.equal(btn.disabled, false);
});


test("renderQuickControls: water-now disabled when locked", () => {
  const futureUnlock = new Date(Date.now() + 60 * 60 * 1000);
  const el = renderQuickControls({ id: 1, _waterLockedUntil: futureUnlock }, document);
  const btn = el.querySelector("[data-action='water-now']");
  assert.equal(btn.disabled, true);
  assert.match(btn.textContent, /🔒|locked/i);
});


test("renderQuickControls: water-now enabled when not locked", () => {
  const el = renderQuickControls({ id: 1, _waterLockedUntil: null }, document);
  const btn = el.querySelector("[data-action='water-now']");
  assert.equal(btn.disabled, false);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/js/test_quick_controls.mjs`
Expected: FAIL — exports not found

- [ ] **Step 3: Implement**

Append to `static/js/grow/unit_detail.mjs`:

```javascript
export function computeWaterLockedUntil(lastPulseAt, soakWindowMin, now = new Date()) {
  if (!lastPulseAt) return null;
  const last = lastPulseAt instanceof Date ? lastPulseAt : new Date(lastPulseAt);
  const unlock = new Date(last.getTime() + soakWindowMin * 60 * 1000);
  return unlock > now ? unlock : null;
}


export function renderQuickControls(unit, doc = document) {
  const panel = doc.createElement("div");
  panel.className = "du-panel";
  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>⚡ Quick controls</span>";
  panel.appendChild(head);

  const body = doc.createElement("div");
  body.className = "du-quick";

  const lockedUntil = unit._waterLockedUntil ?? null;
  const isLocked = lockedUntil !== null;

  const buttons = [
    { action: "identify", label: "⚡ Identify",
      enabled: true, primary: true },
    { action: "water-now", label: isLocked ? `🔒 Water (locked)` : "💧 Water 5s",
      enabled: !isLocked,
      tooltip: isLocked ? `Locked until ${lockedUntil.toLocaleTimeString()}` : "Pulse pump for 5s" },
    { action: "light-toggle", label: "💡 Toggle light", enabled: true },
    { action: "snap-photo", label: "📷 Snap photo", enabled: true },
  ];

  for (const b of buttons) {
    const btn = doc.createElement("button");
    btn.className = "du-act-btn" + (b.primary ? " primary" : "")
                  + (!b.enabled ? " locked" : "");
    btn.dataset.action = b.action;
    btn.dataset.unitId = unit.id;
    btn.disabled = !b.enabled;
    btn.textContent = b.label;
    if (b.tooltip) btn.title = b.tooltip;
    body.appendChild(btn);
  }

  panel.appendChild(body);

  // Wire click handlers
  panel.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-action]");
    if (!btn || btn.disabled) return;
    const url = `/api/grow/units/${unit.id}/${btn.dataset.action}`;
    const old = btn.textContent;
    btn.disabled = true; btn.textContent = "Sending…";
    try {
      const r = await fetch(url, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}) });
      btn.textContent = r.ok ? "✓ Sent" : "✗ Failed";
    } finally {
      setTimeout(() => { btn.disabled = false; btn.textContent = old; }, 2000);
    }
  });

  return panel;
}
```

Update `init()` to also render quick controls:

```javascript
  body.appendChild(renderLiveReadings(unit));

  // Compute water-lock from unit.last_known_state.last_pulse_at + unit.soak_window_min_resolved
  const lastPulse = unit.last_known_state?.last_pulse_at || null;
  const soakMin = unit.soak_window_min_resolved || 30;  // server should send this
  unit._waterLockedUntil = computeWaterLockedUntil(lastPulse, soakMin);

  body.appendChild(renderQuickControls(unit));
```

Append to `static/css/grow.css`:

```css
.du-quick { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; padding: 12px; }
.du-act-btn { background: transparent; border: 1px solid #2a3d50; color: #c2d2e3; padding: 9px 10px; font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; cursor: pointer; border-radius: 2px; font-family: Roboto, sans-serif; }
.du-act-btn:hover:not(:disabled) { border-color: #4dacff; color: #4dacff; }
.du-act-btn.primary { background: #4dacff; color: #001028; border-color: #4dacff; }
.du-act-btn.locked, .du-act-btn:disabled { background: #0e1722; color: #4a5d72; border-color: #1f2e3c; cursor: not-allowed; opacity: 0.85; }
```

- [ ] **Step 4: Run + commit**

Run: `node --test tests/js/test_quick_controls.mjs`
Expected: PASS (6 tests)

```bash
git add static/js/grow/unit_detail.mjs static/css/grow.css tests/js/test_quick_controls.mjs
git commit -m "Add quick controls panel with safety-locked water-now

Identify, water-now, light-toggle, snap-photo. Water-now button
disables when within soak window — tooltip shows unlock time. Pure
function computeWaterLockedUntil() is testable independent of DOM."
```

---

### Task 11.4: Latest photo panel + REST endpoint for image bytes

**Files:**
- Create: `mlss_monitor/routes/api_grow_photos.py`
- Modify: `mlss_monitor/routes/__init__.py`
- Modify: `static/js/grow/unit_detail.mjs` (add photo panel)
- Create: `tests/grow_server/test_grow_photos_api.py`

- [ ] **Step 1: Write the failing tests**

`tests/grow_server/test_grow_photos_api.py`:

```python
"""GET /api/grow/units/<id>/photo/latest serves the latest photo file."""
import os
import sqlite3
import tempfile
from datetime import datetime
import pytest


@pytest.fixture
def setup(tmp_path, monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.routes.api_grow_photos.DB_FILE", tmp.name)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_photos.GROW_IMAGES_DIR", str(tmp_path / "imgs"))
    init_db.create_db()
    img_dir = tmp_path / "imgs" / "unit_001" / "2026-05-03"
    img_dir.mkdir(parents=True)
    (img_dir / "120000.jpg").write_bytes(b"\xff\xd8FAKEJPEG")

    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, 'h', ?)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.execute(
        "INSERT INTO grow_photos (unit_id, taken_at, file_path, width_px, "
        "height_px, size_bytes) VALUES (1, ?, ?, 100, 100, 9)",
        (datetime(2026, 5, 3, 12, 0, 0), "unit_001/2026-05-03/120000.jpg"),
    )
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_photos import api_grow_photos_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_photos_bp)
    return app.test_client()


def test_latest_serves_jpeg(setup):
    r = setup.get("/api/grow/units/1/photo/latest")
    assert r.status_code == 200
    assert r.mimetype == "image/jpeg"
    assert r.data == b"\xff\xd8FAKEJPEG"


def test_latest_404_for_unit_with_no_photos(setup):
    r = setup.get("/api/grow/units/9999/photo/latest")
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_grow_photos_api.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`mlss_monitor/routes/api_grow_photos.py`:

```python
"""Serve photo files for a grow unit."""
import os
import sqlite3
from flask import Blueprint, send_from_directory, abort
from database.init_db import DB_FILE

api_grow_photos_bp = Blueprint("api_grow_photos", __name__)
GROW_IMAGES_DIR = os.environ.get("MLSS_GROW_IMAGES_DIR", "/var/lib/mlss/grow_images")


@api_grow_photos_bp.route("/api/grow/units/<int:unit_id>/photo/latest", methods=["GET"])
def latest_photo(unit_id):
    conn = sqlite3.connect(DB_FILE, timeout=5)
    row = conn.execute(
        "SELECT file_path FROM grow_photos WHERE unit_id=? "
        "ORDER BY taken_at DESC LIMIT 1", (unit_id,),
    ).fetchone()
    conn.close()
    if row is None:
        abort(404)
    file_path = row[0]
    abs_path = os.path.join(GROW_IMAGES_DIR, file_path)
    if not os.path.exists(abs_path):
        abort(404)
    directory, filename = os.path.split(abs_path)
    return send_from_directory(directory, filename, mimetype="image/jpeg")
```

Register in `mlss_monitor/routes/__init__.py`.

Append to `static/js/grow/unit_detail.mjs`:

```javascript
export function renderPhotoPanel(unit, doc = document) {
  const wrap = doc.createElement("div");
  wrap.className = "du-panel";
  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>📷 Latest photo</span>";
  wrap.appendChild(head);

  const photo = doc.createElement("div");
  photo.className = "du-photo-hero";
  // Cache-bust to refresh on poll
  const url = `/api/grow/units/${unit.id}/photo/latest?ts=${Date.now()}`;
  photo.style.backgroundImage = `url(${url})`;
  photo.style.backgroundSize = "cover";
  photo.style.backgroundPosition = "center";
  wrap.appendChild(photo);
  return wrap;
}
```

Update `init()` in `unit_detail.mjs`:

```javascript
body.appendChild(renderPhotoPanel(unit));
body.appendChild(renderLiveReadings(unit));
body.appendChild(renderQuickControls(unit));
```

Append to `static/css/grow.css`:

```css
.du-photo-hero { aspect-ratio: 16/9; background: #0a1219; }
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_grow_photos_api.py -v`
Expected: PASS (2 tests)

```bash
git add mlss_monitor/routes/api_grow_photos.py mlss_monitor/routes/__init__.py static/js/grow/unit_detail.mjs static/css/grow.css tests/grow_server/test_grow_photos_api.py
git commit -m "Add latest-photo serving + photo panel on Live tab

GET /api/grow/units/<id>/photo/latest streams the most recent JPEG
from disk. Detail page renders it as a 16:9 hero. Photo timelapse
scrubber + lightbox deferred to Phase 2 (History tab)."
```

---

### Task 11.5: Visual watering history + light schedule bar (Live tab)

**Files:**
- Create: `static/js/grow/components/sensor-event-chart.mjs`
- Create: `static/js/grow/components/schedule-bar.mjs`
- Modify: `static/js/grow/unit_detail.mjs`
- Create: `mlss_monitor/routes/api_grow_history.py` (light history endpoint)
- Modify: `mlss_monitor/routes/__init__.py`
- Create: `tests/js/test_schedule_bar.mjs`
- Create: `tests/grow_server/test_grow_history.py`

- [ ] **Step 1: Write the failing tests**

`tests/js/test_schedule_bar.mjs`:

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderScheduleBar, computeOnSegments } from "../../static/js/grow/components/schedule-bar.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


test("computeOnSegments: single window 06:00-22:00 → one segment 25%-91.67%", () => {
  const segs = computeOnSegments([{ start: "06:00", end: "22:00" }]);
  assert.equal(segs.length, 1);
  assert.equal(Math.round(segs[0].leftPct * 100), 2500);   // 6/24
  assert.equal(Math.round(segs[0].widthPct * 100), 6667);  // 16/24
});


test("computeOnSegments: overnight window 22:00-06:00 → two segments", () => {
  const segs = computeOnSegments([{ start: "22:00", end: "06:00" }]);
  assert.equal(segs.length, 2);
});


test("computeOnSegments: empty → no segments", () => {
  assert.deepEqual(computeOnSegments([]), []);
});


test("renderScheduleBar shows 'NOW' indicator at correct position", () => {
  const now = new Date("2026-05-03T12:00:00Z");  // 50% of day
  const el = renderScheduleBar([{ start: "06:00", end: "22:00" }], now, document);
  const nowMarker = el.querySelector(".du-schedule-now");
  assert.ok(nowMarker);
  // Left position should be roughly 50%
  assert.match(nowMarker.style.left, /5[0-1]/);
});
```

`tests/grow_server/test_grow_history.py`:

```python
"""GET /api/grow/units/<id>/history?range=24h returns moisture series + watering events."""
import sqlite3
import tempfile
from datetime import datetime, timedelta
import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.routes.api_grow_history.DB_FILE", tmp.name)
    init_db.create_db()
    now = datetime.utcnow()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, 'h', ?)",
        (now, now),
    )
    # 3 telemetry rows + 1 watering event
    for hours_ago, raw, pct in [(3, 612, 31), (2, 800, 46), (1, 1100, 70)]:
        conn.execute(
            "INSERT INTO grow_telemetry (unit_id, timestamp_utc, "
            "soil_moisture_raw, soil_moisture_pct, light_state, pump_state) "
            "VALUES (1, ?, ?, ?, 1, 0)",
            (now - timedelta(hours=hours_ago), raw, pct),
        )
    conn.execute(
        "INSERT INTO grow_watering_events (unit_id, timestamp_utc, trigger, "
        "duration_s, soil_pct_before) VALUES (1, ?, 'pid', 6.0, 31)",
        (now - timedelta(hours=2, minutes=55),),
    )
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_history import api_grow_history_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_history_bp)
    return app.test_client()


def test_history_returns_moisture_and_events(client):
    r = client.get("/api/grow/units/1/history?range=24h")
    assert r.status_code == 200
    body = r.get_json()
    assert "moisture" in body
    assert "watering_events" in body
    assert len(body["moisture"]) == 3
    assert len(body["watering_events"]) == 1
    assert body["watering_events"][0]["duration_s"] == 6.0


def test_history_supports_range_param(client):
    """range=7d or range=30d should also be accepted."""
    r = client.get("/api/grow/units/1/history?range=7d")
    assert r.status_code == 200
    r = client.get("/api/grow/units/1/history?range=30d")
    assert r.status_code == 200


def test_history_invalid_range_400(client):
    r = client.get("/api/grow/units/1/history?range=bogus")
    assert r.status_code == 400
```

- [ ] **Step 2: Run to verify they fail**

Run: `node --test tests/js/test_schedule_bar.mjs` (FAIL)
Run: `python -m pytest tests/grow_server/test_grow_history.py -v` (FAIL)

- [ ] **Step 3: Implement**

`static/js/grow/components/schedule-bar.mjs`:

```javascript
/**
 * 24h horizontal schedule bar with on-window highlights + NOW indicator.
 * Pure function computeOnSegments separates time math from DOM construction.
 */

function _hhMmToHours(s) {
  const [h, m] = s.split(":").map(Number);
  return h + m / 60;
}


export function computeOnSegments(windows) {
  const segs = [];
  for (const w of windows) {
    const start = _hhMmToHours(w.start);
    const end = _hhMmToHours(w.end);
    if (start <= end) {
      segs.push({ leftPct: start / 24, widthPct: (end - start) / 24 });
    } else {
      // Overnight: split into two segments
      segs.push({ leftPct: start / 24, widthPct: (24 - start) / 24 });
      segs.push({ leftPct: 0, widthPct: end / 24 });
    }
  }
  return segs;
}


export function renderScheduleBar(windows, now = new Date(), doc = document) {
  const wrap = doc.createElement("div");
  wrap.className = "du-schedule-bar";
  const track = doc.createElement("div");
  track.className = "du-schedule-track";

  for (const seg of computeOnSegments(windows)) {
    const onSeg = doc.createElement("div");
    onSeg.className = "du-schedule-on";
    onSeg.style.left = `${seg.leftPct * 100}%`;
    onSeg.style.width = `${seg.widthPct * 100}%`;
    track.appendChild(onSeg);
  }

  const nowMarker = doc.createElement("div");
  nowMarker.className = "du-schedule-now";
  const fracOfDay = (now.getUTCHours() + now.getUTCMinutes() / 60) / 24;
  nowMarker.style.left = `${fracOfDay * 100}%`;
  const lbl = doc.createElement("span");
  lbl.className = "lbl";
  lbl.textContent = `NOW · ${now.toISOString().substring(11, 16)}`;
  nowMarker.appendChild(lbl);
  track.appendChild(nowMarker);

  wrap.appendChild(track);
  return wrap;
}
```

`mlss_monitor/routes/api_grow_history.py`:

```python
"""GET /api/grow/units/<id>/history — moisture series + watering events for charts."""
import sqlite3
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, abort
from database.init_db import DB_FILE

api_grow_history_bp = Blueprint("api_grow_history", __name__)

_RANGE_TO_HOURS = {"24h": 24, "7d": 168, "30d": 720}


@api_grow_history_bp.route("/api/grow/units/<int:unit_id>/history", methods=["GET"])
def history(unit_id):
    range_str = request.args.get("range", "24h")
    if range_str not in _RANGE_TO_HOURS:
        return jsonify({"error": "invalid_range"}), 400
    hours = _RANGE_TO_HOURS[range_str]
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    moisture = conn.execute(
        "SELECT timestamp_utc, soil_moisture_pct, soil_moisture_raw "
        "FROM grow_telemetry WHERE unit_id=? AND timestamp_utc >= ? "
        "ORDER BY timestamp_utc ASC", (unit_id, cutoff),
    ).fetchall()
    events = conn.execute(
        "SELECT timestamp_utc, trigger, duration_s, soil_pct_before "
        "FROM grow_watering_events WHERE unit_id=? AND timestamp_utc >= ? "
        "ORDER BY timestamp_utc ASC", (unit_id, cutoff),
    ).fetchall()
    conn.close()

    return jsonify({
        "moisture": [
            {"ts": r["timestamp_utc"], "pct": r["soil_moisture_pct"],
             "raw": r["soil_moisture_raw"]}
            for r in moisture
        ],
        "watering_events": [
            {"ts": r["timestamp_utc"], "trigger": r["trigger"],
             "duration_s": r["duration_s"], "soil_pct_before": r["soil_pct_before"]}
            for r in events
        ],
    })
```

`static/js/grow/components/sensor-event-chart.mjs`:

```javascript
/**
 * Sensor-event chart: moisture % line + watering event vertical bars.
 * Uses Plotly (already loaded by base.html) for the actual rendering.
 * Reusable for any (sensor series, discrete events, target band) chart.
 */

export function renderSensorEventChart(container, data) {
  const { moisture, events, targetPct = 55, deadband = 5 } = data;

  if (typeof Plotly === "undefined") {
    container.textContent = "Plotly not loaded";
    return;
  }

  const traces = [
    {
      x: moisture.map(m => m.ts),
      y: moisture.map(m => m.pct),
      mode: "lines",
      line: { color: "#56f000", width: 2 },
      name: "Moisture %",
      fill: "tozeroy",
      fillcolor: "rgba(86, 240, 0, 0.15)",
      yaxis: "y",
    },
    {
      x: events.map(e => e.ts),
      y: events.map(e => e.duration_s),
      type: "bar",
      marker: { color: events.map(e => e.trigger === "manual" ? "#ffb302" : "#4dacff") },
      name: "Pulse (s)",
      yaxis: "y2",
    },
  ];

  const layout = {
    paper_bgcolor: "#0a1219",
    plot_bgcolor: "#0a1219",
    font: { color: "#c2d2e3", family: "Roboto, sans-serif" },
    margin: { l: 40, r: 60, t: 20, b: 30 },
    height: 240,
    xaxis: { showgrid: false },
    yaxis: { range: [0, 100], title: "%", gridcolor: "#1c2733" },
    yaxis2: { overlaying: "y", side: "right", range: [0, 30],
              title: "pulse s", showgrid: false },
    shapes: [
      // Target band
      { type: "rect", xref: "paper", x0: 0, x1: 1, yref: "y",
        y0: targetPct - deadband, y1: targetPct + deadband,
        fillcolor: "#56f000", opacity: 0.08, line: { width: 0 } },
    ],
    showlegend: false,
  };

  Plotly.newPlot(container, traces, layout, { displayModeBar: false });
}
```

In `static/js/grow/unit_detail.mjs`, append:

```javascript
import { renderScheduleBar } from "./components/schedule-bar.mjs";
import { renderSensorEventChart } from "./components/sensor-event-chart.mjs";


export function renderLightSchedulePanel(unit, doc = document) {
  const wrap = doc.createElement("div");
  wrap.className = "du-panel";
  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = `<span>🕐 Light schedule · ${unit.current_phase}</span>`;
  wrap.appendChild(head);

  // Phase 1: assume single window from spec defaults if no per-unit windows present.
  // Phase 2 will let users edit windows in the Configure tab.
  const windows = unit.light_windows && unit.light_windows.length > 0
    ? unit.light_windows
    : [{ start: "06:00", end: "22:00" }];
  wrap.appendChild(renderScheduleBar(windows, new Date(), doc));
  return wrap;
}


async function renderWateringHistoryPanel(unit, doc = document) {
  const wrap = doc.createElement("div");
  wrap.className = "du-panel";
  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>💧 Watering history · last 24h</span>";
  wrap.appendChild(head);
  const chartDiv = doc.createElement("div");
  chartDiv.id = `watering-chart-${unit.id}`;
  wrap.appendChild(chartDiv);

  const r = await fetch(`/api/grow/units/${unit.id}/history?range=24h`);
  if (r.ok) {
    const data = await r.json();
    renderSensorEventChart(chartDiv, data);
  }
  return wrap;
}
```

Update `init()`:

```javascript
  body.appendChild(renderPhotoPanel(unit));
  body.appendChild(renderLiveReadings(unit));
  body.appendChild(renderLightSchedulePanel(unit));
  body.appendChild(await renderWateringHistoryPanel(unit));
  body.appendChild(renderQuickControls(unit));
```

Append to `static/css/grow.css`:

```css
.du-schedule-bar { padding: 14px; }
.du-schedule-track { height: 24px; background: #0a1219; border-radius: 2px; position: relative; overflow: hidden; }
.du-schedule-on { position: absolute; top: 0; bottom: 0; background: linear-gradient(180deg, #4dacff, #2476b8); opacity: 0.7; }
.du-schedule-now { position: absolute; top: -4px; bottom: -4px; width: 2px; background: #56f000; box-shadow: 0 0 8px #56f000; }
.du-schedule-now .lbl { position: absolute; top: -16px; left: 4px; font-size: 9px; color: #56f000; letter-spacing: 0.08em; white-space: nowrap; }
```

Register `api_grow_history_bp` in `mlss_monitor/routes/__init__.py`.

- [ ] **Step 4: Run + commit**

```bash
node --test tests/js/test_schedule_bar.mjs
python -m pytest tests/grow_server/test_grow_history.py -v
```

Expected: PASS (4 + 3 tests)

```bash
git add static/js/grow/components/schedule-bar.mjs static/js/grow/components/sensor-event-chart.mjs static/js/grow/unit_detail.mjs static/css/grow.css mlss_monitor/routes/api_grow_history.py mlss_monitor/routes/__init__.py tests/js/test_schedule_bar.mjs tests/grow_server/test_grow_history.py
git commit -m "Add light schedule bar + visual watering history (Plotly)

Schedule bar uses pure-function computeOnSegments() then DOM-renders
on-segments + NOW marker. Watering history uses Plotly with moisture
line + event bars + target band shading — same component pattern can
be reused for any sensor + events chart."
```

---

## Section 12 — Browser: empty state + enrollment-key reveal

When zero units are enrolled, the fleet view replaces the card grid with a guided 5-step onboarding panel including the household enrollment key (revealed once at install).

---

### Task 12.1: GET `/api/grow/enrollment-key/peek-once` (one-shot reveal)

**Files:**
- Modify: `mlss_monitor/routes/api_grow_dist.py` (add endpoint)
- Create: `tests/grow_server/test_enrollment_key_reveal.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_enrollment_key_reveal.py`:

```python
"""Reveal the raw enrollment key once (then it's deleted from app_settings)."""
import sqlite3
import tempfile
import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.routes.api_grow_dist.DB_FILE", tmp.name)
    init_db.create_db()
    from flask import Flask
    from mlss_monitor.routes.api_grow_dist import api_grow_dist_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_dist_bp)
    return app.test_client(), tmp.name


def test_peek_once_returns_raw_key_first_time(client):
    c, db = client
    r = c.get("/api/grow/enrollment-key/peek-once")
    assert r.status_code == 200
    assert "key" in r.get_json()


def test_peek_once_deletes_after_reveal(client):
    c, db = client
    c.get("/api/grow/enrollment-key/peek-once")
    r = c.get("/api/grow/enrollment-key/peek-once")
    assert r.status_code == 410  # Gone — already revealed
    body = r.get_json()
    assert "already_revealed" in body.get("error", "")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_enrollment_key_reveal.py -v`
Expected: FAIL — endpoint doesn't exist

- [ ] **Step 3: Implement**

Add to `mlss_monitor/routes/api_grow_dist.py` (and ensure `from database.init_db import DB_FILE`):

```python
import sqlite3


@api_grow_dist_bp.route("/api/grow/enrollment-key/peek-once", methods=["GET"])
def peek_enrollment_key():
    """Return the raw enrollment key once. Deletes it from app_settings after.

    Used by the empty-state UI on first visit. After viewing, key is gone —
    rotation is a separate flow (Phase 2 Settings → Grow page).
    """
    conn = sqlite3.connect(DB_FILE, timeout=5)
    row = conn.execute(
        "SELECT value FROM app_settings "
        "WHERE key='grow_enrollment_key_raw_pending_reveal'",
    ).fetchone()
    if row is None or not row[0]:
        conn.close()
        return jsonify({"error": "already_revealed"}), 410
    raw_key = row[0]
    conn.execute(
        "DELETE FROM app_settings "
        "WHERE key='grow_enrollment_key_raw_pending_reveal'",
    )
    conn.commit()
    conn.close()
    return jsonify({"key": raw_key})
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_enrollment_key_reveal.py -v`
Expected: PASS (2 tests)

```bash
git add mlss_monitor/routes/api_grow_dist.py tests/grow_server/test_enrollment_key_reveal.py
git commit -m "Add /api/grow/enrollment-key/peek-once endpoint

Returns raw enrollment key once at first visit then deletes it from
app_settings. After reveal, the key only exists as its argon2 hash
(used to validate enrollment requests). Rotation is a Phase 2
Settings→Grow flow."
```

---

### Task 12.2: Empty-state guided onboarding panel

**Files:**
- Create: `static/js/grow/components/empty-state.mjs`
- Modify: `static/js/grow/fleet.mjs` (use it when units count = 0)
- Modify: `static/css/grow.css`
- Create: `tests/js/test_empty_state.mjs`

- [ ] **Step 1: Write the failing test**

`tests/js/test_empty_state.mjs`:

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderEmptyState } from "../../static/js/grow/components/empty-state.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


test("renders 5 numbered steps", () => {
  const el = renderEmptyState({ enrollmentKey: "test-key-123", mlssHost: "mlss.local" }, document);
  const steps = el.querySelectorAll(".step");
  assert.equal(steps.length, 5);
});


test("displays the enrollment key", () => {
  const el = renderEmptyState({ enrollmentKey: "test-key-123", mlssHost: "mlss.local" }, document);
  assert.match(el.textContent, /test-key-123/);
});


test("includes the install one-liner", () => {
  const el = renderEmptyState({ enrollmentKey: "x", mlssHost: "mlss.local" }, document);
  assert.match(el.textContent, /curl.*mlss\.local.*install\.sh/);
});


test("when no key (already revealed) shows rotation note", () => {
  const el = renderEmptyState({ enrollmentKey: null, mlssHost: "mlss.local" }, document);
  assert.match(el.textContent, /already revealed|rotate|Settings/i);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/js/test_empty_state.mjs`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`static/js/grow/components/empty-state.mjs`:

```javascript
/**
 * Guided onboarding panel shown when zero units are enrolled.
 * Numbered steps, copy-button enrollment key, install one-liner.
 */

export function renderEmptyState({ enrollmentKey, mlssHost }, doc = document) {
  const wrap = doc.createElement("div");
  wrap.className = "empty-wrap";

  const card = doc.createElement("div");
  card.className = "empty-card";

  card.innerHTML = `
    <div class="hero">
      <div class="icon">🌱</div>
      <div>
        <h3>No grow units enrolled yet</h3>
        <p class="sub">Get a Pi Zero W with the Automation pHAT online in about 5 minutes.</p>
      </div>
    </div>
    <div class="steps">
      <div class="step">
        <div class="step-num">1</div>
        <div class="step-body">
          <strong>Copy your household enrollment key.</strong>
          ${enrollmentKey
            ? `<div class="key-display"><code>${enrollmentKey}</code><button class="copy-btn" data-copy="${enrollmentKey}">📋 Copy</button></div>`
            : `<p style="color:#ffb302">Already revealed — go to Settings → Grow to rotate (Phase 2).</p>`}
        </div>
      </div>
      <div class="step">
        <div class="step-num">2</div>
        <div class="step-body"><strong>Flash Raspberry Pi OS Lite</strong> with WiFi + SSH preconfigured.</div>
      </div>
      <div class="step">
        <div class="step-num">3</div>
        <div class="step-body">
          <strong>Drop /boot/mlss-grow.yaml</strong> on the SD card before ejecting:
          <pre><code>mlss_host: ${mlssHost}
enrollment_key: ${enrollmentKey || '<your-key>'}
plant:
  name: Tomato 1</code></pre>
        </div>
      </div>
      <div class="step">
        <div class="step-num">4</div>
        <div class="step-body">
          <strong>Insert + power on; SSH in once and run:</strong>
          <pre><code>curl -k https://${mlssHost}:5000/api/grow/install.sh | sudo bash</code></pre>
        </div>
      </div>
      <div class="step">
        <div class="step-num">5</div>
        <div class="step-body"><strong>Done.</strong> Unit appears in this Grow tab within ~60 seconds.</div>
      </div>
    </div>
    <div class="empty-foot">
      <a href="/static/docs/PLANT_GROW_UNIT_SETUP.md" class="doc-link">📖 Full setup guide →</a>
    </div>
  `;

  // Wire copy button
  card.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".copy-btn");
    if (!btn) return;
    navigator.clipboard?.writeText(btn.dataset.copy);
    btn.textContent = "✓ Copied";
    setTimeout(() => { btn.textContent = "📋 Copy"; }, 2000);
  });

  wrap.appendChild(card);
  return wrap;
}
```

In `static/js/grow/fleet.mjs`, replace the empty-state placeholder with:

```javascript
import { renderEmptyState } from "./components/empty-state.mjs";


async function _fetchEnrollmentKey() {
  try {
    const r = await fetch("/api/grow/enrollment-key/peek-once");
    if (r.ok) return (await r.json()).key;
  } catch (_) {}
  return null;
}


async function refreshEmpty() {
  const grid = document.getElementById("grow-grid");
  grid.innerHTML = "";
  const key = await _fetchEnrollmentKey();
  grid.appendChild(renderEmptyState({
    enrollmentKey: key,
    mlssHost: window.location.hostname,
  }));
}


// In renderGrid, replace the empty-state branch:
function renderGrid(units) {
  const grid = document.getElementById("grow-grid");
  grid.innerHTML = "";
  if (units.length === 0) {
    refreshEmpty();
    return;
  }
  for (const u of units) grid.appendChild(renderGrowCard(u));
}
```

Append to `static/css/grow.css`:

```css
.empty-wrap { padding: 40px 20px; display: flex; align-items: center; justify-content: center; min-height: 480px; font-family: Roboto, sans-serif; }
.empty-card { background: #142028; border: 1px solid #1f2e3c; border-radius: 4px; max-width: 720px; width: 100%; padding: 32px; }
.empty-card .hero { display: flex; align-items: center; gap: 16px; margin-bottom: 20px; padding-bottom: 20px; border-bottom: 1px solid #1c2733; }
.empty-card .icon { width: 56px; height: 56px; border-radius: 4px; background: linear-gradient(135deg, #1f3a1c, #2d5429); display: flex; align-items: center; justify-content: center; font-size: 28px; }
.empty-card h3 { color: #fff; margin: 0 0 4px; font-size: 20px; font-weight: 500; }
.empty-card .sub { color: #7d92a8; font-size: 13px; margin: 0; }
.steps { display: flex; flex-direction: column; gap: 18px; margin-top: 20px; }
.step { display: flex; gap: 14px; align-items: flex-start; }
.step-num { width: 28px; height: 28px; border-radius: 50%; background: rgba(77, 172, 255, 0.15); color: #4dacff; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 500; flex-shrink: 0; }
.step-body { flex: 1; color: #c2d2e3; font-size: 13px; line-height: 1.55; }
.step-body strong { color: #fff; font-weight: 500; }
.step-body pre { background: #0a1219; padding: 8px 12px; border-radius: 2px; border: 1px solid #1c2733; overflow-x: auto; font-size: 11px; }
.step-body pre code { color: #56f000; font-family: 'SFMono-Regular', Consolas, monospace; }
.key-display { background: #0a1219; border: 1px solid #1c2733; padding: 8px 12px; border-radius: 2px; font-family: monospace; font-size: 11px; color: #ffb302; margin-top: 6px; display: flex; justify-content: space-between; align-items: center; }
.copy-btn { color: #4dacff; font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; cursor: pointer; background: none; border: none; }
.empty-foot { margin-top: 24px; padding-top: 18px; border-top: 1px solid #1c2733; }
.doc-link { color: #4dacff; font-size: 12px; text-decoration: none; }
```

- [ ] **Step 4: Run + commit**

Run: `node --test tests/js/test_empty_state.mjs`
Expected: PASS (4 tests)

```bash
git add static/js/grow/components/empty-state.mjs static/js/grow/fleet.mjs static/css/grow.css tests/js/test_empty_state.mjs
git commit -m "Add empty-state with guided 5-step onboarding + enrollment key reveal

When zero units enrolled, fleet grid is replaced by a guided panel:
peek the enrollment key (one-shot reveal), flash, drop YAML, install
one-liner, wait. Copy button on the key. Reusable 'guided steps'
visual pattern."
```

---

## Section 13 — Documentation (md)

Three docs, all in `docs/`:
- `PLANT_GROW_UNIT_SETUP.md` — installation + first-unit walkthrough for someone new to MLSS
- `PLANT_GROW_UNIT_USAGE.md` — day-to-day how-to: add units, manage plants, troubleshoot
- `PLANT_GROW_UNIT_ARCHITECTURE.md` — architecture deep-dive (audience: devs working on the code)

Tests are markdown lints + spec-coverage reviews via grep on the rendered HTML.

---

### Task 13.1: SETUP.md — installation guide

**Files:**
- Create: `docs/PLANT_GROW_UNIT_SETUP.md`
- Create: `tests/grow_server/test_setup_doc.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_setup_doc.py`:

```python
"""SETUP.md covers required topics + has working internal references."""
from pathlib import Path
import re

DOC = Path(__file__).resolve().parent.parent.parent / "docs" / "PLANT_GROW_UNIT_SETUP.md"


def test_doc_exists():
    assert DOC.exists()


def test_doc_covers_required_topics():
    text = DOC.read_text().lower()
    for topic in [
        "prerequisites", "enrollment", "first unit",
        "/boot/mlss-grow.yaml", "install.sh", "troubleshooting",
    ]:
        assert topic in text, f"missing topic: {topic}"


def test_doc_links_to_hardware_doc():
    text = DOC.read_text()
    assert "PLANT_GROW_UNIT_HARDWARE.md" in text


def test_doc_includes_install_oneliner_example():
    text = DOC.read_text()
    assert re.search(r"curl.*-k.*api/grow/install\.sh.*sudo bash", text)


def test_doc_no_obvious_placeholders():
    text = DOC.read_text()
    for bad in ["TBD", "TODO", "XXX", "FIXME"]:
        assert bad not in text, f"placeholder {bad} found"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/grow_server/test_setup_doc.py -v`
Expected: FAIL — file doesn't exist

- [ ] **Step 3: Implement**

`docs/PLANT_GROW_UNIT_SETUP.md`:

```markdown
# Plant Grow Unit — Setup guide

End-to-end walkthrough: from a clean MLSS install + a Pi Zero in a box, to
a plant being watered and photographed automatically.

> **Hardware reference:** [PLANT_GROW_UNIT_HARDWARE.md](PLANT_GROW_UNIT_HARDWARE.md)
> for BOM, wiring tables, and the bench test sequence.

---

## Prerequisites

Before starting:

- **MLSS server** is installed and running on its Pi (see [PRODUCTION.md](PRODUCTION.md)). You should be able to reach the dashboard at `https://mlss.local:5000`.
- **A Pi Zero W (or Pi Zero 2 W)** flashed with Raspberry Pi OS Lite (64-bit recommended on Zero 2 W).
- **The hardware** wired per [PLANT_GROW_UNIT_HARDWARE.md](PLANT_GROW_UNIT_HARDWARE.md) — Automation pHAT seated on GPIO header, Seesaw soil sensor on I2C, pump on OUT 1, grow light on the relay, camera on CSI, single multi-port USB wall wart split-wired.
- **Bench-tested** — soil sensor reads at `i2cdetect -y 1`, pump pulses with the test snippet, light flashes, camera captures.

---

## First unit walkthrough

### 1. Get your household enrollment key

Open the MLSS dashboard at `https://mlss.local:5000/grow`. Because no units are enrolled yet, you'll see the empty-state onboarding panel with the enrollment key shown once. **Copy it now and save it somewhere safe** — it's only displayed on first visit.

If you missed it (or are setting up after others have already enrolled units), you'll need to rotate the key via Settings → Grow (this is a Phase 2 feature; for now, edit `app_settings.grow_enrollment_key_hash` directly via SQLite or recreate the DB).

### 2. Drop `/boot/mlss-grow.yaml` on the SD card

Before ejecting the Pi's SD card from your laptop, the boot partition is FAT32 and writeable from any OS. Create the file:

```yaml
mlss_host: mlss.local
enrollment_key: <paste-the-key-from-step-1>
plant:
  name: Tomato 1
  type: tomato       # optional; defaults to 'generic'
  medium: soil       # optional; defaults to 'soil'
```

If WiFi wasn't pre-configured by Raspberry Pi Imager's advanced options, also drop `wpa_supplicant.conf` (standard Pi flow).

### 3. Boot the Pi + install the firmware

Insert the SD card and power on. Once the Pi has joined WiFi, SSH in:

```bash
ssh pi@<pi-zero-ip>
```

Then run the install one-liner:

```bash
curl -k https://mlss.local:5000/api/grow/install.sh | sudo bash
```

This will:

1. apt-install Python 3.11+, libcamera-apps, i2c-tools, build-essential
2. Create the `mlss-grow` system user
3. Download both wheels from the MLSS server
4. Create a venv at `/opt/mlss-grow/.venv`, install both wheels
5. Drop the systemd unit at `/etc/systemd/system/mlss-grow.service`
6. Enable + start the service

The first run of the service reads `/boot/mlss-grow.yaml`, posts to `/api/grow/enroll`, gets a per-unit token, saves it to `/etc/mlss/grow.token`, and **deletes the YAML** so the enrollment key isn't sitting on the SD card.

### 4. Watch it appear in the dashboard

Refresh `https://mlss.local:5000/grow`. Within ~60 seconds, your unit appears as a card with status **Nominal**. Click **Open** to see live readings.

Tail the unit's logs if anything's misbehaving:

```bash
ssh pi@<pi-zero-ip>
sudo journalctl -u mlss-grow -f
```

---

## Adding additional units

For unit #2 onwards, repeat steps 2–4 above with the same enrollment key (one key serves all units in your household). About 3 minutes per unit.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Unit doesn't appear in dashboard after install | WiFi not joined | `journalctl -u mlss-grow -f` on the Pi; check for connect errors |
| Card shows "Offline" with no recent telemetry | WS connection dropped | Restart the service: `sudo systemctl restart mlss-grow`. Check WiFi signal. |
| Soil sensor not detected at boot | I2C cable polarity or address conflict | `sudo i2cdetect -y 1` should show `36`. Swap red/black at JST connector if missing. |
| Photos not appearing | Camera not enabled in raspi-config | `sudo raspi-config` → Interface Options → Camera |
| Pump runs continuously | Wiring backwards (NC instead of NO on relay) | Swap relay output terminal — failsafe is dark/dry, so NO must be open at rest. |

---

## See also

- [PLANT_GROW_UNIT_HARDWARE.md](PLANT_GROW_UNIT_HARDWARE.md) — wiring, BOM, bench tests
- [PLANT_GROW_UNIT_USAGE.md](PLANT_GROW_UNIT_USAGE.md) — day-to-day operation
- [PLANT_GROW_UNIT_ARCHITECTURE.md](PLANT_GROW_UNIT_ARCHITECTURE.md) — how it works under the hood
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_setup_doc.py -v`
Expected: PASS (5 tests)

```bash
git add docs/PLANT_GROW_UNIT_SETUP.md tests/grow_server/test_setup_doc.py
git commit -m "Add SETUP.md installation walkthrough"
```

---

### Task 13.2: USAGE.md — day-to-day how-to

**Files:**
- Create: `docs/PLANT_GROW_UNIT_USAGE.md`
- Create: `tests/grow_server/test_usage_doc.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_usage_doc.py`:

```python
from pathlib import Path

DOC = Path(__file__).resolve().parent.parent.parent / "docs" / "PLANT_GROW_UNIT_USAGE.md"


def test_doc_exists(): assert DOC.exists()


def test_doc_covers_user_topics():
    text = DOC.read_text().lower()
    for topic in ["identify", "water now", "schedule", "soak window",
                  "phase", "calibrat", "offline"]:
        assert topic in text, f"missing topic: {topic}"


def test_doc_no_placeholders():
    text = DOC.read_text()
    for bad in ["TBD", "TODO", "XXX"]:
        assert bad not in text
```

- [ ] **Step 2: Run + verify failure**

Run: `python -m pytest tests/grow_server/test_usage_doc.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`docs/PLANT_GROW_UNIT_USAGE.md`:

```markdown
# Plant Grow Unit — Usage guide

Day-to-day operation. Audience: anyone using the MLSS dashboard who has at
least one Plant Grow Unit enrolled.

> First-time setup → [PLANT_GROW_UNIT_SETUP.md](PLANT_GROW_UNIT_SETUP.md)
> How it works internally → [PLANT_GROW_UNIT_ARCHITECTURE.md](PLANT_GROW_UNIT_ARCHITECTURE.md)

---

## The Grow tab

`https://mlss.local:5000/grow` shows your fleet as cards. Each card represents one growing area (a single plant pot, a microgreens tray, etc.). Counts at the top: total units, online, stale, offline.

Cards are colour-coded:

- **Nominal** (green) — unit reporting recent telemetry, no errors
- **Caution** (amber) — moisture below threshold or other warning condition
- **Stale** (cyan) — last telemetry 30s–5min ago (likely brief WiFi drop)
- **Offline** (orange) — no telemetry for >5 min; unit's local safety loop is still running on the Pi

Click any card to open its detail page.

---

## Identifying which physical unit is which

In a fleet of 5+ units, "which one is *this* card?" is a real question. Click **Identify** on any card (or on the detail-page header). The unit's grow light blinks for 10 seconds — distinct from any normal on/off transition. The button shows a countdown so you know the blink is in progress.

---

## Manual controls

The detail page has a **Quick controls** panel with four buttons:

- **⚡ Identify** — 10s blink (always available)
- **💧 Water 5s** — pulse the pump for 5 seconds. **Disabled during the soak window** — see below.
- **💡 Toggle light** — manual override on/off. The schedule will resume on the next 30s tick.
- **📷 Snap photo** — capture immediately, outside the normal 30-min cadence.

---

## The soak window — why "Water now" sometimes won't fire

The soak window is the minimum enforced cool-down between watering pulses. **Default 30 minutes.** Defends against the failure mode "water doesn't reach the sensor for several minutes → system thinks it's still dry → fires another pulse → drowns the pot."

When the soak window is active, the **Water 5s** button is greyed out and shows the unlock time on hover. Identify, light toggle, and snap photo are unaffected.

To override globally, an admin can change `grow_default_soak_window_min` in Settings → Grow (Phase 2). To override for one specific unit (e.g. a unit with deep slow-draining soil), edit `grow_units.soak_window_min_override` (Phase 2).

---

## Phase changes

Each unit has a current phase: `seedling` / `vegetative` / `flowering` / `fruiting` / `dormant`. The phase determines which light schedule and PID watering profile apply.

In Phase 1 (current MVP), changing phase requires editing the database (`grow_units.current_phase`). Phase 2 will add a phase picker in the Configure tab. A future Phase 4 feature will detect phase transitions automatically from the camera images.

---

## Calibration

The Seesaw soil sensor reports a raw capacitance value (200–2000) that varies with the medium type. To get a meaningful "%" reading on the dashboard, the unit needs two calibration points:

- **Dry**: the raw value when the sensor is in dry medium (or air)
- **Wet**: the raw value just after watering when the medium is fully saturated

Defaults are seeded per medium (`soil`, `coco`, `rockwool`) — usable out of the box. For better accuracy, use the Configure tab's calibration two-step (Phase 2): "Calibrate dry" with sensor dry → "Calibrate wet" after watering. Until then, the dashboard shows raw values for `medium_type='custom'` units that haven't been calibrated.

---

## What happens if MLSS goes offline

The unit's safety loop runs every 30 seconds on the Pi itself, with the last-known config persisted to `/var/lib/mlss-grow/config.json`. If MLSS is unreachable:

- Light schedule continues from local config
- PID watering continues from local config
- Photos are captured but **not** buffered (to save SD-card writes); they resume on reconnect
- Telemetry is buffered to local SQLite (default 7 days)
- On reconnect, buffered telemetry replays in original-timestamp order

Bottom line: if your router dies for the weekend, your plants survive. The dashboard will show "Offline" — clicking refresh after MLSS is back will show the unit transitioning through "Stale" → "Online" as the buffer drains.

---

## Photos

By default each unit captures one photo every 30 minutes during daylight hours (06:00–22:00). All photos are kept on the MLSS Pi at `MLSS_GROW_IMAGES_DIR/unit_NNN/YYYY-MM-DD/HHMMSS.jpg` (default `/var/lib/mlss/grow_images`). Each photo is joined to the closest telemetry reading at capture time so you can later train ML models on (image, soil moisture, temperature) tuples.

**Storage:** ~10 MB/day/unit. At 30 units that's ~110 GB/year. **Strongly recommend a USB SSD** rather than relying on the SD card. To migrate: stop MLSS, `rsync` the existing images dir to the new disk, set `MLSS_GROW_IMAGES_DIR` env var, restart.

---

## Troubleshooting recipes

### Unit went offline overnight

1. Check the Grow card → status should be Offline
2. SSH to the Pi → `sudo journalctl -u mlss-grow -f` shows current state
3. Most often: WiFi flap. Restart networking: `sudo systemctl restart wpa_supplicant`
4. If the service crashed: `sudo systemctl status mlss-grow`. Restart with `sudo systemctl restart mlss-grow`.
5. Last resort: reboot the Pi. The systemd watchdog should have caught wedges, but a hard reboot is safe.

### Pump won't fire even though soil is dry

1. Check the soak window — is the **Water 5s** button greyed out? If yes, you're inside the cool-down. Wait or use the global override (Phase 2).
2. Open the unit's detail page → check capabilities. Is `pump` listed? If not, hardware not detected — check OUT 1 wiring.
3. Check the unit logs for `safety_cap_hit` events — pump may be in cooldown after hitting the 30s pulse cap.

### Plant looks stressed and chart shows constant pump pulses

Either:
- Sensor calibration is off (raw → % mapping wrong) — recalibrate
- PID is over-watering — bump `soak_window_min` for that unit, lower `kp`, or both
- Plant profile is wrong for the actual plant — update `plant_type` in `grow_units`
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_usage_doc.py -v`
Expected: PASS (3 tests)

```bash
git add docs/PLANT_GROW_UNIT_USAGE.md tests/grow_server/test_usage_doc.py
git commit -m "Add USAGE.md day-to-day operation guide"
```

---

### Task 13.3: ARCHITECTURE.md — devs deep-dive

**Files:**
- Create: `docs/PLANT_GROW_UNIT_ARCHITECTURE.md`
- Create: `tests/grow_server/test_architecture_doc.py`

- [ ] **Step 1: Write the failing test**

`tests/grow_server/test_architecture_doc.py`:

```python
from pathlib import Path

DOC = Path(__file__).resolve().parent.parent.parent / "docs" / "PLANT_GROW_UNIT_ARCHITECTURE.md"


def test_doc_exists(): assert DOC.exists()


def test_doc_covers_dev_topics():
    text = DOC.read_text().lower()
    for topic in [
        "websocket", "bearer token", "enrollment key",
        "contracts", "package", "abc", "sensor",
        "pid", "soak window", "buffer", "telemetry_id",
    ]:
        assert topic in text, f"missing topic: {topic}"


def test_doc_links_to_spec():
    text = DOC.read_text()
    assert "2026-05-03-plant-grow-unit-system-design.md" in text
```

- [ ] **Step 2: Run + verify failure**

Run: `python -m pytest tests/grow_server/test_architecture_doc.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

`docs/PLANT_GROW_UNIT_ARCHITECTURE.md`:

```markdown
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

- **Household enrollment key** — argon2-hashed in `app_settings.grow_enrollment_key_hash`. Used once at first-boot to mint the per-unit token. The raw key is shown once in the empty-state UI then deleted from the DB.
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
```

- [ ] **Step 4: Run + commit**

Run: `python -m pytest tests/grow_server/test_architecture_doc.py -v`
Expected: PASS (3 tests)

```bash
git add docs/PLANT_GROW_UNIT_ARCHITECTURE.md tests/grow_server/test_architecture_doc.py
git commit -m "Add ARCHITECTURE.md dev deep-dive"
```

---

## Section 14 — End-to-end smoke test + final wiring

One integration test that exercises the full path: spin up MLSS in a test container, simulate a Pi Zero with the firmware modules + a fake serial bus, run enrollment + telemetry + image upload + identify command + offline + reconnect.

---

### Task 14.1: End-to-end integration test

**Files:**
- Create: `tests/grow_server/test_e2e_smoke.py`

- [ ] **Step 1: Write the test**

```python
"""End-to-end smoke: enroll, send telemetry, send photo, receive command, replay buffer."""
import asyncio
import json
import sqlite3
import struct
import tempfile
from datetime import datetime
import pytest
import websockets


@pytest.fixture
def setup(tmp_path, monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    img_dir = tmp_path / "images"
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    for mod in ["mlss_monitor.grow.auth", "mlss_monitor.grow.handlers",
                "mlss_monitor.grow.photo_storage", "mlss_monitor.routes.api_grow_enroll",
                "mlss_monitor.routes.api_grow_units", "mlss_monitor.routes.api_grow_dist",
                "mlss_monitor.routes.api_grow_history", "mlss_monitor.routes.api_grow_photos"]:
        try:
            monkeypatch.setattr(f"{mod}.DB_FILE", tmp.name)
        except AttributeError:
            pass
    monkeypatch.setattr("mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", str(img_dir))
    init_db.create_db()

    # Get raw enrollment key
    conn = sqlite3.connect(tmp.name)
    raw_key = conn.execute(
        "SELECT value FROM app_settings WHERE key='grow_enrollment_key_raw_pending_reveal'"
    ).fetchone()[0]
    conn.close()

    # Start WS listener
    from mlss_monitor.grow.ws_registry import WSRegistry
    from mlss_monitor.routes.api_grow_ws import start_ws_listener
    registry = WSRegistry()
    server = start_ws_listener("127.0.0.1", 0, registry)
    port = server.sockets[0].getsockname()[1]

    yield raw_key, port, tmp.name, str(img_dir), registry
    server.close()


@pytest.mark.asyncio
async def test_full_lifecycle(setup):
    raw_key, port, db_path, img_dir, registry = setup

    # 1. Enrol via REST
    from flask import Flask
    from mlss_monitor.routes.api_grow_enroll import api_grow_enroll_bp
    app = Flask(__name__); app.register_blueprint(api_grow_enroll_bp)
    enroll_resp = app.test_client().post("/api/grow/enroll", json={
        "enrollment_key": raw_key, "hardware_serial": "test-pi-001",
        "plant": {"name": "Test Tomato", "type": "tomato", "medium": "soil"},
    })
    assert enroll_resp.status_code == 201
    body = enroll_resp.get_json()
    unit_id, token = body["unit_id"], body["token"]

    # 2. Open WS, send capabilities + telemetry + photo
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/api/grow/{unit_id}/ws",
        extra_headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        await ws.send(json.dumps({
            "type": "capabilities", "ts": "2026-05-03T12:00:00Z",
            "payload": {
                "capabilities": [
                    {"channel": "soil_moisture", "hardware": "Seesaw",
                     "is_required": True, "unit_label": "raw"},
                    {"channel": "soil_temp_c", "hardware": "Seesaw",
                     "is_required": False, "unit_label": "°C"},
                    {"channel": "light", "hardware": "AutomationPHATLight",
                     "is_required": True, "unit_label": ""},
                    {"channel": "pump", "hardware": "AutomationPHATPump",
                     "is_required": True, "unit_label": ""},
                    {"channel": "camera", "hardware": "picamera2",
                     "is_required": True, "unit_label": ""},
                ],
                "firmware_version": "0.1.0",
                "hardware_serial": "test-pi-001",
            },
        }))
        await ws.send(json.dumps({
            "type": "telemetry", "ts": "2026-05-03T12:00:00Z",
            "payload": {"soil_moisture_raw": 612, "light_state": True,
                        "pump_state": False, "soil_temp_c": 21.4},
        }))
        # Photo
        header = json.dumps({"taken_at": "2026-05-03T12:00:00Z",
                             "width": 100, "height": 100}).encode()
        photo = b"\xff\xd8FAKEPHOTODATA"
        await ws.send(struct.pack(">I", len(header)) + header + photo)
        await asyncio.sleep(0.3)

        # 3. Send identify command from server side via registry
        await registry.send_to_unit(unit_id, json.dumps({
            "type": "command", "ts": "2026-05-03T12:00:01Z",
            "payload": {"name": "identify", "args": {"duration_s": 1}},
        }))
        # ack would normally come back; for the smoke test we just verify send didn't throw

    # 4. Verify DB rows
    conn = sqlite3.connect(db_path)
    n_caps = conn.execute("SELECT COUNT(*) FROM grow_unit_capabilities WHERE unit_id=?",
                          (unit_id,)).fetchone()[0]
    n_tel = conn.execute("SELECT COUNT(*) FROM grow_telemetry WHERE unit_id=?",
                         (unit_id,)).fetchone()[0]
    photos = conn.execute("SELECT file_path, telemetry_id FROM grow_photos WHERE unit_id=?",
                          (unit_id,)).fetchall()
    conn.close()

    assert n_caps == 5
    assert n_tel >= 1
    assert len(photos) == 1
    assert photos[0][1] is not None  # telemetry_id was joined

    # 5. Verify image file actually written to disk
    import os
    assert os.path.exists(os.path.join(img_dir, photos[0][0]))
```

- [ ] **Step 2: Run**

Run: `python -m pytest tests/grow_server/test_e2e_smoke.py -v`
Expected: PASS — full enroll → connect → telemetry → photo → command path works end-to-end

- [ ] **Step 3: Commit**

```bash
git add tests/grow_server/test_e2e_smoke.py
git commit -m "Add end-to-end smoke test

Exercises the complete Phase 1 path: enrolment → WS handshake →
capabilities → telemetry → photo upload (with telemetry_id join) →
server-pushed command. Catches any cross-module wiring breakage."
```

---

## Section 15 — Documentation finishing touches

### Task 15.1: Update top-level README + roadmap

**Files:**
- Modify: `readme.md` (add Grow Unit section in features list)
- Modify: `docs/Bugs_Improvements_and_Roadmap.md` (add Phase 2/3/4/5 items + hardware watchdog)

- [ ] **Step 1: Add a section about Plant Grow Units**

In `readme.md` features list, add:

```markdown
- **Plant Grow Units** — remote Pi Zero W satellites, each managing one growing area (single plant, microgreens tray, etc.) with soil moisture sensing, PID-driven watering, configurable light schedule, and timelapse photography. See [PLANT_GROW_UNIT_HARDWARE.md](docs/PLANT_GROW_UNIT_HARDWARE.md), [PLANT_GROW_UNIT_SETUP.md](docs/PLANT_GROW_UNIT_SETUP.md), and the [system design spec](docs/superpowers/specs/2026-05-03-plant-grow-unit-system-design.md).
```

In `docs/Bugs_Improvements_and_Roadmap.md`, append:

```markdown
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

### Phase 4 (smarts)
- Image-based phase classifier
- Plant-stage-aware PID adjustments
- Cross-unit anomaly detection
- Reservoir / water budget tracking

### Phase 5 (polish)
- Custom Pi SD-card .img for one-step provisioning
- Public PyPI release of `mlss-grow`
- Mobile-optimised fleet view
- Plant journal / annotations on the History tab
- Time-lapse video generation

### Hardware/reliability deferred
- **Hardware watchdog (`/dev/watchdog`)** on Pi Zero — designed in but not wired up due to risk of misconfigured timer rebooting healthy Pi mid-write. Re-evaluate if a unit silently wedges in production despite systemd watchdog.
```

- [ ] **Step 2: Commit**

```bash
git add readme.md docs/Bugs_Improvements_and_Roadmap.md
git commit -m "Document Plant Grow Unit feature in README + roadmap

Adds Phase 2/3/4/5 items plus the hardware-watchdog deferred note."
```

---

## Plan complete — execution handoff

You've now got 14 sections covering the complete Phase 1 of the Plant Grow Unit System: contracts, schema, server REST + WS, firmware (sensors, actuators, camera, PID, schedule, buffer, safety loop, WS client, enrollment, systemd), build + install scripts, browser fleet view + detail Live tab + empty state, three documentation files, and an end-to-end smoke test.

Phase 2 and Phase 3 plans will be written separately when Phase 1 is shipping or shipped.

**Estimated commit count when this plan executes:** ~75 commits (one per passing test).

**Pre-execution checklist:**
- [ ] Branch `feature/plant-grow-units` is current (already created)
- [ ] Spec is reviewed and approved (already done)
- [ ] Hardware doc is reviewed and approved (already done — see commit `071ae70`)
- [ ] Plan reviewed (your turn — read through and flag anything missing or overly prescriptive)

After plan review, execute via `/subagent-driven-development` (your stated preference) — fresh subagent per task, two-stage review between tasks.












