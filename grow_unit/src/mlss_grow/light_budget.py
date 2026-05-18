"""Daily light-on-time budget enforcement.

Spec §7 caps grow-light usage at 20 h per UTC day. The budget is
enforced unconditionally on the unit (not driven by user-editable
config) — even if the operator schedules a 24h window, the safety
loop will refuse to re-energise the relay once 20h of cumulative
on-time has accrued in the current UTC day. The cap exists because
most plants need a dark period; a stuck-on light is a horticultural
failure even when it's not a hardware risk.

The class tracks cumulative on-minutes for a single UTC day and
rolls over at 00:00 UTC. If the light is already on when the day
flips, the partial spans on either side of midnight are accounted
correctly (the pre-midnight portion stays on day N, the
post-midnight portion starts day N+1's count fresh).

UTC is used for the rollover boundary deliberately — the rest of
the system records timestamps in UTC, and the budget needs to be
consistent with `now_fn` which the safety loop already passes in
UTC. Using local time would risk a 1-hour gap or overlap on DST
transition.
"""
from __future__ import annotations

from datetime import date, datetime, time

_MAX_LIGHT_MINUTES_PER_DAY = 20 * 60  # spec §7


class LightBudget:
    """Tracks cumulative light-on minutes for the current UTC day."""

    def __init__(self) -> None:
        self._date: date | None = None
        self._on_minutes: float = 0.0
        # Wall-clock instant the light most recently transitioned
        # off→on, or None if currently off.
        self._on_since: datetime | None = None

    def can_turn_on(self, now: datetime) -> bool:
        """True iff turning the light on now would not exceed the cap."""
        self._roll_over_day_if_needed(now)
        return self._accumulated_minutes(now) < _MAX_LIGHT_MINUTES_PER_DAY

    def record_on(self, now: datetime) -> None:
        """Mark the light as having transitioned on at `now`. Idempotent
        (if already on, leaves the start time unchanged)."""
        self._roll_over_day_if_needed(now)
        if self._on_since is None:
            self._on_since = now

    def record_off(self, now: datetime) -> None:
        """Mark the light as having transitioned off at `now`. Idempotent
        (if already off, no-op)."""
        self._roll_over_day_if_needed(now)
        if self._on_since is not None:
            elapsed_min = (now - self._on_since).total_seconds() / 60.0
            self._on_minutes += elapsed_min
            self._on_since = None

    def minutes_used_today(self, now: datetime) -> float:
        """Cumulative on-minutes for the UTC day containing `now`,
        including any in-progress on-span up to `now`. Useful for
        telemetry / debug logging."""
        self._roll_over_day_if_needed(now)
        return self._accumulated_minutes(now)

    # ---- internals ----------------------------------------------------

    def _accumulated_minutes(self, now: datetime) -> float:
        """Total on-minutes in the current day, including the in-flight
        span (if currently on)."""
        running = self._on_minutes
        if self._on_since is not None:
            running += (now - self._on_since).total_seconds() / 60.0
        return running

    def _roll_over_day_if_needed(self, now: datetime) -> None:
        """If `now` falls on a different UTC day than the last call,
        reset the counter. If the light was on across the midnight
        boundary, charge the pre-midnight portion to the previous day's
        (now-discarded) count and restart the in-flight span from the
        new day's 00:00 UTC.
        """
        today = now.date()
        if self._date == today:
            return
        # Crossing midnight (or first call after construction).
        if self._on_since is not None and self._date is not None:
            # Charge the on-time from on_since up to midnight against the
            # outgoing day; we'll discard that count below. Then restart
            # the in-flight span from 00:00 of the new day so the
            # post-midnight portion accrues against today's 20h budget.
            midnight = datetime.combine(today, time(0, 0))
            self._on_since = midnight
        elif self._on_since is not None:
            # First-ever call and the light is already on — start the
            # span from `now` rather than guessing.
            self._on_since = now
        self._on_minutes = 0.0
        self._date = today
