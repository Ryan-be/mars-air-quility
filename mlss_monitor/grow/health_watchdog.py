"""Lazy watchdog for capability health: did the unit react to a command?

The user's first deployment will have the camera + soil moisture sensor
wired but pump + grow light unpowered (waiting on second PSU). We need
the UI to grey out actuator buttons when the unit's hardware isn't
actually responding — without making the user toggle a "sense-only mode"
flag explicitly.

The watchdog is intentionally lazy: it lives entirely in process memory
and is only consulted when a GET /api/grow/units/<id> happens. There is
no background polling thread; we just record when the server *sent* an
actuator command, then on the next GET we ask "did follow-up evidence
arrive within the timeout window?". If not, the capability is flipped to
"unresponsive" in the response (NOT in the database — the next telemetry
that proves the actuator works will quietly upgrade it back).

Single-process Pi deployments (one Flask instance) can use a module-level
dict; if/when this is split across gunicorn workers, swap to Redis or a
DB row.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from threading import Lock
from typing import Optional

from database.init_db import DB_FILE

log = logging.getLogger(__name__)

# How long we wait after sending a command before flipping the capability
# to "unresponsive". 30 s comfortably exceeds:
#  - the firmware's command-dispatch latency (sub-second),
#  - the buffer-replay drain on a one-off reconnect (a few seconds),
#  - one telemetry tick (configured to 10 s in the safety loop default).
# Below 30 s we'd risk false-positives on a unit that's just slow to
# respond; above 30 s the user has to wait too long to see the warning.
DEFAULT_TIMEOUT_S = 30

_last_command_at: dict[tuple[int, str], datetime] = {}
_lock = Lock()


def record_command_sent(unit_id: int, channel: str, *,
                        at: Optional[datetime] = None) -> None:
    """Record that an actuator command was just sent to ``(unit_id, channel)``.

    Call this from POST handlers like ``water_now`` AFTER the
    ``_push_command_blocking`` call returns 202 — recording before the
    push would generate spurious "unresponsive" reports for commands
    the registry refused (unit not connected, send timeout, etc.).
    """
    with _lock:
        _last_command_at[(unit_id, channel)] = at or datetime.utcnow()


def clear() -> None:
    """Test helper — drop all in-memory command timestamps between tests."""
    with _lock:
        _last_command_at.clear()


def _has_pump_evidence_since(unit_id: int, since: datetime) -> bool:
    """Return True if a watering_event landed for this unit after ``since``.

    Strong evidence the pump is working — stronger than telemetry, because
    the firmware only emits a watering_event AFTER the actuation
    completes. If a watering_event hasn't arrived in the window, we don't
    yet know if the actuation finished; the safer answer is to say
    "unresponsive" until evidence arrives.
    """
    conn = sqlite3.connect(DB_FILE, timeout=5)
    try:
        row = conn.execute(
            "SELECT 1 FROM grow_watering_events "
            "WHERE unit_id=? AND timestamp_utc >= ? LIMIT 1",
            (unit_id, since),
        ).fetchone()
    finally:
        conn.close()
    return row is not None


def _has_light_evidence_since(unit_id: int, since: datetime) -> bool:
    """Return True if telemetry showed light_state=1 after ``since``."""
    conn = sqlite3.connect(DB_FILE, timeout=5)
    try:
        row = conn.execute(
            "SELECT 1 FROM grow_telemetry "
            "WHERE unit_id=? AND timestamp_utc >= ? AND light_state=1 LIMIT 1",
            (unit_id, since),
        ).fetchone()
    finally:
        conn.close()
    return row is not None


def check_unresponsive(unit_id: int, channel: str, *,
                      timeout_s: int = DEFAULT_TIMEOUT_S,
                      now: Optional[datetime] = None) -> bool:
    """Decide whether ``(unit_id, channel)`` should be reported unresponsive.

    Returns True if:
      - a command was recorded for this (unit, channel),
      - more than ``timeout_s`` seconds have elapsed since,
      - AND no follow-up evidence (event row for pump, telemetry for
        light) landed in that window.

    Returns False otherwise — including when no command has been sent at
    all (the watchdog only knows about commands the server actually
    issued; an idle pump that's never been asked to do anything stays
    in whatever health it had).

    Intentionally read-only: callers (the GET handler) overlay the
    result on top of the persisted health value before responding.
    """
    with _lock:
        cmd_at = _last_command_at.get((unit_id, channel))
    if cmd_at is None:
        return False
    now = now or datetime.utcnow()
    if (now - cmd_at) <= timedelta(seconds=timeout_s):
        return False
    # Past timeout — look for evidence in the (cmd_at, now] window.
    if channel == "pump":
        if _has_pump_evidence_since(unit_id, cmd_at):
            return False
    elif channel == "light":
        if _has_light_evidence_since(unit_id, cmd_at):
            return False
    else:
        # Unknown actuator channel — be conservative and don't flag.
        # The UI fallback for unknown channels is "untested" via the
        # default in details_json, which is fine.
        return False
    return True
