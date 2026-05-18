"""Persist PID + actuator state across firmware service restarts.

The PID controller's integral term and last_pulse_at timestamp are
in-memory by default. Without persistence, a service restart cold-starts
the integral and forgets soak-window timing — at low Ki this is harmless
but with higher Ki it would briefly mis-shape pulses, and forgetting
last_pulse_at could let a soak window be skipped if the restart lands
inside it.

State is JSON in a small file — ~100 bytes — written after every PID
decision. Read once on boot. Failures are non-fatal: a fresh state is
acceptable, the loop keeps running with default values.
"""
import json
import logging
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_PATH = "/var/lib/mlss-grow/watering_state.json"


@dataclass
class PersistedState:
    error_integral: float = 0.0
    last_error: float = 0.0
    last_pulse_at_iso: Optional[str] = None  # ISO8601 string for JSON friendliness


def load_state(path: str = DEFAULT_PATH) -> PersistedState:
    """Read PID state from disk. Returns defaults if file missing or corrupt.

    Any error path (missing file, malformed JSON, wrong types, missing
    keys) returns a fresh PersistedState — the firmware boots with a
    clean integral rather than refusing to start. Boot-time failures
    here would otherwise wedge the service.
    """
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            log.warning(
                "persisted PID state at %s is not a JSON object; "
                "starting fresh", path,
            )
            return PersistedState()
        return PersistedState(
            error_integral=float(data.get("error_integral", 0.0)),
            last_error=float(data.get("last_error", 0.0)),
            last_pulse_at_iso=data.get("last_pulse_at_iso"),
        )
    except FileNotFoundError:
        log.info("no persisted PID state at %s; starting fresh", path)
        return PersistedState()
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        log.warning(
            "persisted PID state at %s is corrupt (%s); starting fresh",
            path, exc,
        )
        return PersistedState()


def save_state(state: PersistedState, path: str = DEFAULT_PATH) -> None:
    """Write PID state to disk. Failures log but don't raise — the
    PID loop keeps running even if we can't persist its state.

    Uses an atomic write (tmp file + rename via os.replace) so a power
    cut mid-write can't corrupt the file. Worst case we lose the latest
    tick's update. os.replace is used over os.rename because os.rename
    fails on Windows when the target exists, while os.replace is atomic
    on both POSIX and Windows.
    """
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(asdict(state), f)
        os.replace(tmp_path, path)
    except Exception as exc:  # noqa: BLE001 — best-effort persistence
        log.warning("failed to persist PID state to %s: %s", path, exc)
