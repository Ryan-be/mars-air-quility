"""Top-level orchestration: sensors → PID → actuators every tick."""
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from mlss_grow.pid import PIDConfig, PIDState, pid_decide
from mlss_grow.light_schedule import is_light_on
from mlss_grow.light_budget import LightBudget
from mlss_grow.state_persistence import (
    PersistedState, load_state, save_state, DEFAULT_PATH as _STATE_DEFAULT_PATH,
)

log = logging.getLogger(__name__)

# Spec §7 "Failsafe limits" — hardware-protection invariants that
# override anything the user can set in config. Enforced unconditionally
# on the unit so a misconfigured server (or a buggy PID tune that wants
# a 60s pulse) cannot drive the pump past safe limits.
_HARD_PUMP_CAP_S = 30.0
_PUMP_COOLDOWN_AFTER_HARD_CAP_S = 5 * 60  # 5 minutes


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
                 pid_state: PIDState | None = None,
                 light_budget: LightBudget | None = None,
                 uptime_provider: Optional[Callable[[], float]] = None,
                 buffer: Optional[Any] = None,
                 state_path: Optional[str] = None) -> None:
        self._sensors = sensors
        self._pump = pump
        self._light = light
        self._camera = camera
        self._config = config
        self._emit = emit
        self._now = now_fn
        self._pid_state = pid_state or PIDState(
            last_pulse_at=datetime(2000, 1, 1))
        # Phase 3 Task 7: persist PID integral + last_pulse_at across
        # service restarts. Path resolves: explicit kwarg → env var
        # MLSS_GROW_STATE_PATH → /var/lib/mlss-grow/watering_state.json
        # On boot, hydrate the PID state from disk (a fresh state on
        # missing/corrupt file is acceptable — load_state handles that).
        # We save after each tick that produces a pulse so the integral
        # + last_pulse_at survive a restart. With Ki=0 (current default)
        # the integral hydration is invisible but with higher Ki future
        # tuning it would briefly mis-shape pulses on every restart.
        self._state_path = (
            state_path
            or os.environ.get("MLSS_GROW_STATE_PATH")
            or _STATE_DEFAULT_PATH
        )
        persisted = load_state(self._state_path)
        self._pid_state.error_integral = persisted.error_integral
        self._pid_state.last_error = persisted.last_error
        if persisted.last_pulse_at_iso:
            try:
                self._pid_state.last_pulse_at = datetime.fromisoformat(
                    persisted.last_pulse_at_iso,
                )
            except (TypeError, ValueError) as exc:
                log.warning(
                    "persisted last_pulse_at_iso=%r unparseable (%s); "
                    "keeping in-memory default",
                    persisted.last_pulse_at_iso, exc,
                )
        # The 20h/24h light budget lives on the SafetyLoop (not in
        # config) because it's an enforced invariant, not a tunable.
        # Constructor-injectable for tests so they can pre-populate
        # cumulative on-time.
        self._light_budget = light_budget or LightBudget()
        self._last_photo_at: Optional[datetime] = None
        # Phase 3 diagnostics: every telemetry frame includes uptime_s
        # and buffer_size so the server can cache them in grow_units
        # for the Diagnostics tab. Both are injected (rather than
        # imported from service.py) so tests can supply fakes without
        # poking at module-level globals.
        self._uptime_provider = uptime_provider
        self._buffer = buffer

    def _persist_pid_state(self) -> None:
        """Snapshot current PID state to disk. Best-effort, never raises."""
        save_state(
            PersistedState(
                error_integral=self._pid_state.error_integral,
                last_error=self._pid_state.last_error,
                last_pulse_at_iso=self._pid_state.last_pulse_at.isoformat(),
            ),
            self._state_path,
        )

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

        # 2. Light schedule.
        # The decision flows: schedule says ON → also check 20h budget.
        # If the budget is exhausted we keep the relay open and emit a
        # one-shot safety_cap_hit so the dashboard surfaces the cap.
        # Schedule says OFF → just go off (and record the off-edge so
        # the budget accounts for the on-span that just ended).
        currently_on = self._light.state()
        scheduled_on = is_light_on(now, self._config.light_windows)
        if scheduled_on:
            if self._light_budget.can_turn_on(now):
                if not currently_on:
                    self._light.on()
                    self._light_budget.record_on(now)
            else:
                # Budget exhausted — refuse to (re-)energise. If the
                # light is currently on (e.g. we crossed the cap mid-on),
                # turn it off and record the off-edge.
                if currently_on:
                    log.warning(
                        "light budget exhausted (>%d min today) — opening relay",
                        20 * 60,
                    )
                    self._light.off()
                    self._light_budget.record_off(now)
                    self._emit("event", {
                        "kind": "safety_cap_hit",
                        "details": {"cap": "light_20h_per_day"},
                    })
                else:
                    log.info(
                        "light schedule says ON but daily 20h budget exhausted; staying off",
                    )
        else:
            if currently_on:
                self._light.off()
                self._light_budget.record_off(now)

        # 3. PID watering — bypassed entirely when holiday mode is on.
        # Telemetry + lights still run; the operator's plant stays lit and
        # logged but not over-watered while they're away. PID *state*
        # isn't reset, so coming back from holiday picks up where we left.
        raw = readings.get("soil_moisture")
        if raw is not None and not self._config.holiday_mode:
            pct = _moisture_pct(raw, self._config.soil_calibration)
            if pct is not None:
                # Cooldown check — the cap from the last pulse is still
                # in force. PID still runs (so its integrator advances
                # honestly) but we do not actuate.
                in_cooldown = (
                    self._pid_state.pump_cooldown_until is not None
                    and now < self._pid_state.pump_cooldown_until
                )
                if in_cooldown:
                    log.info(
                        "pump in cooldown until %s; skipping any pulse",
                        self._pid_state.pump_cooldown_until,
                    )
                else:
                    d = pid_decide(
                        pct, self._config.pid, self._pid_state, now,
                    )
                    if d.pulse_s > 0:
                        # Hard cap regardless of config.max_pulse_s. The
                        # spec §7 30s ceiling is a hardware-protection
                        # invariant — if PID asks for more, clamp + arm
                        # the 5-min cooldown so a runaway controller
                        # cannot keep pummelling the pump.
                        clamped = min(d.pulse_s, _HARD_PUMP_CAP_S)
                        if clamped < d.pulse_s:
                            log.warning(
                                "PID requested %.1fs pulse, hard-capping to %.1fs (spec §7); arming %ds cooldown",
                                d.pulse_s, clamped,
                                _PUMP_COOLDOWN_AFTER_HARD_CAP_S,
                            )
                            self._pid_state.pump_cooldown_until = (
                                now
                                + timedelta(
                                    seconds=_PUMP_COOLDOWN_AFTER_HARD_CAP_S,
                                )
                            )
                            self._emit("event", {
                                "kind": "safety_cap_hit",
                                "details": {
                                    "cap": "pump_30s_max_pulse",
                                    "requested_s": d.pulse_s,
                                    "clamped_s": clamped,
                                },
                            })
                        self._pump.pulse(clamped)
                        self._pid_state.last_pulse_at = now
                        # Phase 3 Task 7: snapshot integral + last_pulse_at
                        # to disk after each fire so a service restart
                        # picks up where we left off (no integral cold-start,
                        # no skipped soak window). Save is best-effort —
                        # save_state swallows write errors. We only persist
                        # on actual fires (not deadband/soak-window ticks)
                        # because pulses are infrequent: this keeps SD-card
                        # write churn minimal.
                        self._persist_pid_state()
                        self._emit("event", {
                            "kind": "watering_pulse",
                            "details": {
                                "duration_s": clamped, "trigger": "pid",
                                "soil_pct_before": pct,
                                "pid_error":
                                    self._config.pid.target_pct - pct,
                                "pid_p_term": d.p_term,
                                "pid_i_term": d.i_term,
                                "pid_d_term": d.d_term,
                                "triggered_by": "system",
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
        telemetry: dict = {
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
        }
        # Phase 3 diagnostics fields. Both are best-effort: a buffer.size()
        # call that raises (e.g. SQLite locked) shouldn't take down the
        # tick — the field just won't appear, and the server's
        # omit-doesnt-clobber persistence keeps the last good value.
        if self._uptime_provider is not None:
            try:
                telemetry["uptime_s"] = self._uptime_provider()
            except Exception as exc:  # noqa: BLE001
                log.warning("uptime_provider failed: %s", exc)
        if self._buffer is not None:
            try:
                telemetry["buffer_size"] = self._buffer.size()
            except Exception as exc:  # noqa: BLE001
                log.warning("buffer.size() failed: %s", exc)
        self._emit("telemetry", telemetry)

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
