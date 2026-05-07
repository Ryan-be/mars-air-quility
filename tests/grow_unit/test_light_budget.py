"""LightBudget: enforces the spec §7 20h/24h light cap.

The class tracks cumulative on-time for the current UTC day and rolls
over at midnight. Tests cover:
  * fresh budget allows turn-on
  * record_on/record_off accumulates correctly
  * exceeding 20h closes the gate
  * crossing midnight resets the counter
  * the on-state survives the day rollover (partial spans before/after)
"""
from datetime import datetime, timedelta

from mlss_grow.light_budget import LightBudget


def test_can_turn_on_when_no_history():
    b = LightBudget()
    assert b.can_turn_on(datetime(2026, 5, 6, 8, 0)) is True


def test_records_minutes_between_on_and_off():
    b = LightBudget()
    t0 = datetime(2026, 5, 6, 8, 0)
    b.record_on(t0)
    b.record_off(t0 + timedelta(minutes=30))
    # 30 min accumulated, plenty of headroom under 20h cap.
    assert b.can_turn_on(t0 + timedelta(minutes=30)) is True
    assert b.minutes_used_today(t0 + timedelta(minutes=30)) == 30.0


def test_record_on_is_idempotent_while_already_on():
    """record_on while already on doesn't re-start the timer (would
    discard accrued on-time)."""
    b = LightBudget()
    t0 = datetime(2026, 5, 6, 8, 0)
    b.record_on(t0)
    b.record_on(t0 + timedelta(minutes=10))  # extra call, no-op
    b.record_off(t0 + timedelta(minutes=30))
    assert b.minutes_used_today(t0 + timedelta(minutes=30)) == 30.0


def test_can_turn_on_returns_false_after_20h_accumulated_today():
    """Accumulate 20h of on-time → cap exhausted → can_turn_on=False."""
    b = LightBudget()
    t0 = datetime(2026, 5, 6, 0, 0)
    b.record_on(t0)
    b.record_off(t0 + timedelta(hours=20))
    # Right at the boundary: 20h * 60 = 1200 min, cap is < 1200 ⇒ False.
    assert b.minutes_used_today(t0 + timedelta(hours=20)) == 1200.0
    assert b.can_turn_on(t0 + timedelta(hours=20)) is False


def test_can_turn_on_just_under_cap_returns_true():
    """At 19:59 of on-time, can_turn_on=True; one more minute and it
    flips false."""
    b = LightBudget()
    t0 = datetime(2026, 5, 6, 0, 0)
    b.record_on(t0)
    b.record_off(t0 + timedelta(hours=19, minutes=59))
    assert b.can_turn_on(t0 + timedelta(hours=19, minutes=59)) is True


def test_resets_at_midnight_utc():
    """Accumulate 21h on day 1 (yes, would have been blocked mid-day —
    the test is checking the rollover, not the cap), then advance to day
    2: budget should be fresh.
    """
    b = LightBudget()
    t0 = datetime(2026, 5, 6, 0, 0)
    # Force 21h of recorded on-time with a single off-flip.
    b.record_on(t0)
    b.record_off(t0 + timedelta(hours=21))
    assert b.can_turn_on(t0 + timedelta(hours=22)) is False  # still day 1

    # Advance into the next UTC day.
    next_day = datetime(2026, 5, 7, 1, 0)
    assert b.can_turn_on(next_day) is True
    assert b.minutes_used_today(next_day) == 0.0


def test_handles_on_state_crossing_midnight():
    """Turn on at 22:00 UTC day 1, off at 02:00 UTC day 2.

    Day 1 should account for 2h (22→00). Day 2 should start fresh and
    account for 2h (00→02). On the day-2 record_off, total day-2
    on-minutes = 120.
    """
    b = LightBudget()
    on_time = datetime(2026, 5, 6, 22, 0)
    b.record_on(on_time)
    # In day 1 + still on at 23:30 — accumulated 1.5h (90 min).
    assert b.minutes_used_today(datetime(2026, 5, 6, 23, 30)) == 90.0

    # Cross midnight. Reading at 01:00 UTC day 2 should show 60 min.
    after_midnight = datetime(2026, 5, 7, 1, 0)
    assert b.minutes_used_today(after_midnight) == 60.0

    # Record off at 02:00 day 2. Day 2 minutes = 120.
    off_time = datetime(2026, 5, 7, 2, 0)
    b.record_off(off_time)
    assert b.minutes_used_today(off_time) == 120.0
    assert b.can_turn_on(off_time) is True


def test_record_off_when_already_off_is_noop():
    """Defensive: a stray record_off without a matching record_on
    shouldn't crash or distort the count."""
    b = LightBudget()
    t0 = datetime(2026, 5, 6, 8, 0)
    b.record_off(t0)  # no-op
    assert b.minutes_used_today(t0) == 0.0
    b.record_on(t0)
    b.record_off(t0 + timedelta(minutes=10))
    b.record_off(t0 + timedelta(minutes=20))  # extra off, no-op
    assert b.minutes_used_today(t0 + timedelta(minutes=20)) == 10.0
