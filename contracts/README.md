# mlss-contracts

Shared pydantic schemas used by both the MLSS server (`mlss_monitor/`) and the
Plant Grow Unit firmware (`grow_unit/`). Single source of truth — both packages
import from this one to guarantee message-shape compatibility on the
authenticated WSS link between hub and grow units.

## What's in here

| Module | Purpose |
|---|---|
| `mlss_contracts.ws_messages` | Top-level envelopes for every WS frame type (`telemetry`, `event`, `command`, `config`, `ack`, `capabilities`) |
| `mlss_contracts.enums` | `Channel`, `Severity`, `CommandName`, `PhaseName`, `MediumType`, … — controlled vocabularies validated at the boundary |
| `mlss_contracts.config_models` | `ProfileUpdate`, `PIDUpdate`, `LightWindowsUpdate`, `CalibrationUpdate`, `PhotoScheduleUpdate`, `SafetyOverrideRequest` — payload shapes for the Configure-tab PUTs |

A schema change is a single edit here; any drift between server and firmware
becomes a static error rather than a runtime one.

## Install (dev, path-dep)

```bash
poetry install
```

## See also

- [docs/PLANT_GROW_UNIT_ARCHITECTURE.md](../docs/PLANT_GROW_UNIT_ARCHITECTURE.md) — how the WS protocol uses these schemas
- [docs/RELEASE_PROCESS.md](../docs/RELEASE_PROCESS.md) — wheel build flow (no public PyPI publish)
- [grow_unit/README.md](../grow_unit/README.md) — firmware package that consumes these schemas
