"""PID watering decision: pure function over (current_pct, config, state)."""
from datetime import datetime, timedelta
from mlss_grow.pid import (
    PIDConfig, PIDState, pid_decide,
)


def _cfg(**kwargs):
    defaults = {
        "target_pct": 55, "deadband_pct": 5, "kp": 0.4, "ki": 0, "kd": 0,
        "min_pulse_s": 2, "max_pulse_s": 8, "soak_window_min": 30,
    }
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


def test_error_exactly_at_deadband_does_not_fire():
    """Boundary: soil at 50%, target 55, deadband 5 → error == 5 → still within deadband.
    Pins the `<=` semantics so a future change to `<` would surface as a test failure."""
    state = PIDState(last_pulse_at=datetime(2026, 1, 1), last_error=0, error_integral=0)
    d = pid_decide(current_pct=50, config=_cfg(), state=state, now=datetime(2026, 5, 3))
    assert d.pulse_s == 0
    assert d.reason == "within_deadband"


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
