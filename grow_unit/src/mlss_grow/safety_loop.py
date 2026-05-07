"""Top-level orchestration: sensors → PID → actuators every tick."""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from mlss_grow.pid import PIDConfig, PIDState, pid_decide
from mlss_grow.light_schedule import is_light_on

log = logging.getLogger(__name__)


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
    # Household-wide vacation flag. When True, the SafetyLoop suppresses
    # pump pulses (PID still runs and emits debug-level info, but no
    # actuation). Lights + telemetry continue normally so the operator
    # comes home to a lit plant and a continuous log instead of a dry
    # one. Updated by config_sync.apply_config from the server pull.
    holiday_mode: bool = False


class SafetyLoop:
    def __init__(self, sensors, pump, light, camera, config: LoopConfig,
                 emit: Callable[[str, dict], None],
                 now_fn: Callable[[], datetime] = datetime.utcnow,
                 pid_state: PIDState | None = None) -> None:
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

    def tick(self) -> None:
        now = self._now()

        # 1. Read all sensors
        readings = {}
        any_degraded = False
        for s in self._sensors:
            try:
                vals = s.read()
                readings.update(vals)
            except Exception as exc:
                # Hide-the-bug avoidance: surface the failure to the operator
                # via the log. The loop continues — a bad read on one sensor
                # shouldn't abort the whole tick — but the failure must not
                # be silent or the operator has no way to notice the unit is
                # half-blind.
                log.warning(
                    "sensor %s read failed: %s",
                    type(s).__name__, exc,
                )
            if not s.healthy():
                any_degraded = True

        if any_degraded:
            self._emit("event", {"kind": "sensor_degraded", "details": {}})

        # 2. Light schedule
        should_be_on = is_light_on(now, self._config.light_windows)
        if should_be_on != self._light.state():
            (self._light.on() if should_be_on else self._light.off())

        # 3. PID watering — bypassed entirely when holiday mode is on.
        # Telemetry + lights still run; the operator's plant stays lit and
        # logged but not over-watered while they're away. PID *state*
        # isn't reset, so coming back from holiday picks up where we left.
        raw = readings.get("soil_moisture")
        if raw is not None and not self._config.holiday_mode:
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
            except Exception as exc:
                # Same reasoning as the sensor block above — surface the
                # failure rather than swallowing it. Photos can fail for
                # mundane reasons (libcamera busy mid-exposure, brief CSI
                # glitch) but a persistent failure should be visible.
                log.warning("camera capture failed: %s", exc)

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
