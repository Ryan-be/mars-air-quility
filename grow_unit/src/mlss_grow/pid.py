"""Pure PID watering decision logic.

Designed as a pure function over (current_pct, config, state, now) so the
core control logic is unit-testable without any I/O. The safety loop
calls this on every tick; if the returned Decision has pulse_s > 0,
the safety loop pulses the pump.

Note: `pid_decide` mutates the passed-in `PIDState` (updates
`error_integral` and `last_error`) when it advances the controller. This
is intentional — the caller owns the state object across ticks. Tests
assert on the post-call state.
"""
from dataclasses import dataclass
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
    # Set when the safety loop hard-caps a pump pulse to the spec §7
    # 30s ceiling. While now < pump_cooldown_until the safety loop
    # refuses to issue any further pulses (a runaway PID that just hit
    # the absolute ceiling needs cool-down before another shot, even if
    # the soak-window guard would otherwise re-engage).
    pump_cooldown_until: datetime | None = None


@dataclass
class Decision:
    pulse_s: float
    reason: str = ""
    p_term: float = 0
    i_term: float = 0
    d_term: float = 0


_INTEGRAL_CLAMP = 100  # anti-windup


def pid_decide(current_pct: float, config: PIDConfig, state: PIDState,
               now: datetime, tick_seconds: float = 30,
               bypass_soak: bool = False) -> Decision:
    """Compute the watering decision for this tick.

    `bypass_soak` is the admin-side `safety_override:skip_next_soak`
    escape hatch: when True, the in-soak-window early-return is
    skipped for this tick only. The flag is consumed once per
    invocation; the safety loop is responsible for not passing it
    twice in a row (see SafetyOverrideState.consume_skip_next_soak,
    which atomically reads + clears).
    """
    error = config.target_pct - current_pct
    if error <= config.deadband_pct:
        return Decision(pulse_s=0, reason="within_deadband")

    if (not bypass_soak
            and (now - state.last_pulse_at) < timedelta(
                minutes=config.soak_window_min,
            )):
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
