"""Pull-on-`config_changed`: when the server says config changed, GET the
latest values via the bearer-authenticated /api/grow/units/<id>/config
endpoint and apply them to the running PIDConfig + light schedule.

Two halves:
  * `pull_unit_config(...)` — network layer (URL, auth header, TLS verify)
  * `apply_config(unit_cfg, loop_cfg)` — mutate in-memory state in place,
    no service restart required

Why pull rather than push: the server resolves null overrides against
`grow_plant_profiles` BEFORE responding, so the firmware sees concrete
numbers and never has to maintain its own profile table. Smaller
firmware, single source of truth on the server.

TLS verify posture matches enrol.py + ws_client.py — pinned cert when
the file exists, fall back to `verify=False` with a prominent WARNING
log when it doesn't (dev/test, pre-install).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import time
from typing import Optional

import requests

from mlss_grow.light_schedule import parse_window
from mlss_grow.pid import PIDConfig
from mlss_grow.safety_loop import LoopConfig

log = logging.getLogger(__name__)

# pull_unit_config is called on every config_changed push and on every
# WS reconnect, so a per-call WARNING for a missing TLS cert would spam
# the journal in dev/test setups. Latch a process-level flag so we log
# the warning exactly once per dispatcher lifetime.
_warned_missing_cert = False


@dataclass
class UnitConfig:
    """Server response shape, pre-resolved against plant profiles.

    `holiday_mode` is the household-wide vacation flag — when True the
    loop suppresses pump pulses but keeps light schedule + telemetry
    going. Defaults False so older server responses (without the field)
    don't accidentally pause watering.

    `buffer_retention_days` is the per-unit override of the firmware's
    age-based buffer prune retention. None means "use the firmware
    default" (_DEFAULT_BUFFER_RETENTION_DAYS, currently 7 — matches the
    server's `grow_default_buffer_retention_days` app_setting). Read by
    the buffer_retention_days_provider closure that ws_client passes
    to LocalBuffer.prune on every successful reconnect.
    """
    overrides: dict
    calibration: dict
    light_windows: dict
    current_phase: str
    plant_type: str
    holiday_mode: bool = False
    buffer_retention_days: Optional[int] = None


# Maps overrides-key → PIDConfig attribute name. Any None values in the
# overrides dict are skipped (defensive: server should resolve, but
# preserving existing PID state is safer than overwriting with None).
_PID_FIELD_MAP = {
    "watering_target":  "target_pct",
    "kp":               "kp",
    "ki":               "ki",
    "kd":               "kd",
    "soak_window_min":  "soak_window_min",
    "min_pulse_s":      "min_pulse_s",
    "max_pulse_s":      "max_pulse_s",
}


def pull_unit_config(server_url: str, unit_id: int, token: str,
                     server_cert_path: Optional[str] = None,
                     timeout: float = 10) -> UnitConfig:
    """GET <server_url>/api/grow/units/<unit_id>/config with bearer auth.

    Returns a parsed UnitConfig. Raises:
      * requests.RequestException — network error or 4xx/5xx status
      * KeyError — server returned a body missing required keys
        (current_phase, plant_type)
    """
    url = f"{server_url}/api/grow/units/{unit_id}/config"
    if server_cert_path and os.path.isfile(server_cert_path):
        verify: "bool | str" = server_cert_path
    else:
        global _warned_missing_cert
        if not _warned_missing_cert:
            log.warning(
                "MLSS server cert not found at %s — pulling config with "
                "verify=False. INSECURE on a hostile LAN; fine for dev/test.",
                server_cert_path,
            )
            _warned_missing_cert = True
        verify = False
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, verify=verify, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    # current_phase / plant_type are required because apply_config uses them
    # to pick which phase's light_windows to load.
    return UnitConfig(
        overrides=data.get("overrides", {}) or {},
        calibration=data.get("calibration", {}) or {},
        light_windows=data.get("light_windows", {}) or {},
        current_phase=data["current_phase"],
        plant_type=data["plant_type"],
        holiday_mode=bool(data.get("holiday_mode", False)),
        # Server returns NULL for "no override" — preserve that as None
        # so the firmware default kicks in. Coerce to int when present
        # in case older servers serialise as numeric string.
        buffer_retention_days=(
            int(data["buffer_retention_days"])
            if data.get("buffer_retention_days") is not None
            else None
        ),
    )


def apply_config(unit_cfg: UnitConfig, loop_cfg: LoopConfig) -> None:
    """Mutate `loop_cfg` in place to reflect the new config.

    Three pieces of state get updated without restarting the service:
      1. PIDConfig fields (target, gains, pulse limits, soak window)
      2. Soil calibration tuple (soil_dry_raw, soil_wet_raw)
      3. Light schedule (only for the unit's current_phase — firmware
         only schedules one phase at a time)

    None values in any field are SKIPPED rather than written. The server
    is supposed to resolve them before sending, but preserving existing
    state is the right defensive choice.
    """
    pid: PIDConfig = loop_cfg.pid

    # 1. PID overrides
    for src_key, pid_attr in _PID_FIELD_MAP.items():
        val = unit_cfg.overrides.get(src_key)
        if val is None:
            continue
        setattr(pid, pid_attr, val)

    # 2. Soil calibration
    dry_raw = unit_cfg.calibration.get("dry_raw")
    wet_raw = unit_cfg.calibration.get("wet_raw")
    if dry_raw is not None and wet_raw is not None:
        loop_cfg.soil_calibration = (dry_raw, wet_raw)

    # 3. Light schedule for the current phase. Other phases' windows are
    # ignored — this firmware doesn't multi-phase schedule.
    phase_windows = unit_cfg.light_windows.get(unit_cfg.current_phase, [])
    new_windows: list[tuple[time, time]] = []
    for w in phase_windows:
        try:
            new_windows.append(parse_window(w["start"], w["end"]))
        except (KeyError, ValueError) as exc:
            log.warning("skipping malformed light window %r: %s", w, exc)
    loop_cfg.light_windows = new_windows

    # 4. Holiday mode flag. The SafetyLoop reads this each tick and
    # short-circuits the pump-pulse path when True. Lights + telemetry
    # are unaffected — operator going on vacation wants the plant to
    # keep being lit and logged, just not over-watered while away.
    loop_cfg.holiday_mode = bool(unit_cfg.holiday_mode)
