"""Direct actuator drive in response to a `safety_override` command.

The server-side admin UI emits one of these actions:
  * force_pump_on    — turn pump on for `duration_s` seconds, then off
  * force_pump_off   — turn pump off immediately, no duration
  * force_light_on   — turn light on for `duration_s` seconds, then off
  * force_light_off  — turn light off immediately, no duration
  * skip_next_soak   — set a flag the safety loop checks to skip its
                        usual soak-window guard on the next PID decision

Critical non-functional requirement: invoke_safety_override must NOT
block the caller thread for `duration_s` seconds. The dispatcher thread
that calls this also services the WS receive loop; if a 60-second
override stalled it, the unit would appear offline (no telemetry/keepalive)
for the duration. Solution: schedule the .off() flip on a `threading.Timer`
and return immediately.

A fresh override pre-empts any pending Timer from a previous override
(otherwise a quick succession of overrides leaks Timer threads).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class SafetyOverrideState:
    """In-process state shared between the dispatcher (writer) and the
    safety loop (reader). The dispatcher invokes overrides; the safety
    loop polls `consume_skip_next_soak` each tick to decide whether to
    bypass the soak-window guard."""

    pending_timer: Optional[threading.Timer] = None
    skip_next_soak: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock,
                                    repr=False, compare=False)

    def cancel_pending(self) -> None:
        """Cancel any in-flight Timer. Safe to call when no timer pending."""
        with self._lock:
            t = self.pending_timer
            self.pending_timer = None
        if t is not None:
            t.cancel()

    def consume_skip_next_soak(self) -> bool:
        """Atomically read-and-clear the skip flag. Returns True iff
        skip_next_soak was set (i.e. one-shot consume)."""
        with self._lock:
            v = self.skip_next_soak
            self.skip_next_soak = False
        return v


def invoke_safety_override(action: str, duration_s: float, pump, light,
                            state: SafetyOverrideState) -> None:
    """Drive the named actuator. Non-blocking: the off-flip is scheduled
    on a Timer thread.

    Unknown actions are logged and dropped — the dispatcher must not
    crash on a malformed payload because the WS receive loop runs in
    the same thread.
    """
    # Always cancel any prior pending off-timer before starting a new
    # action — otherwise back-to-back overrides leak Timer threads and
    # a stale .off() may fire mid-way through a fresh on().
    state.cancel_pending()

    if action == "force_pump_on":
        pump.on()
        _schedule_off(state, pump.off, duration_s, "force_pump_on")
    elif action == "force_pump_off":
        pump.off()
    elif action == "force_light_on":
        light.on()
        _schedule_off(state, light.off, duration_s, "force_light_on")
    elif action == "force_light_off":
        light.off()
    elif action == "skip_next_soak":
        with state._lock:
            state.skip_next_soak = True
        log.info("safety_override: skip_next_soak flag set")
    else:
        log.warning("safety_override: unknown action %r — ignoring", action)


def _schedule_off(state: SafetyOverrideState, off_callable,
                   duration_s: float, action_label: str) -> None:
    """Schedule the off-flip on a daemon Timer so the caller returns
    immediately. Stashes the Timer in `state.pending_timer` so a
    follow-up override can pre-empt it."""

    def _off_and_clear():
        try:
            off_callable()
        except Exception as exc:
            log.exception(
                "safety_override (%s): off-flip failed: %s",
                action_label, exc,
            )
        # Clear the pending-timer pointer (under lock) so cancel_pending
        # doesn't try to cancel an already-fired timer. Atomic to avoid
        # racing with a concurrent override that's setting a new timer.
        with state._lock:
            if state.pending_timer is t:
                state.pending_timer = None

    t = threading.Timer(duration_s, _off_and_clear)
    t.daemon = True
    with state._lock:
        state.pending_timer = t
    t.start()
    log.info(
        "safety_override (%s): off scheduled in %.2fs",
        action_label, duration_s,
    )
