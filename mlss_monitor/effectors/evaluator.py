"""Periodic loop driving every enabled auto-mode smart plug.

Mirrors the daemon-thread pattern used by
:mod:`mlss_monitor.notifications.dispatcher` and the backup workers:
``start_evaluator()`` spawns a named daemon thread that calls
:func:`evaluate_once` every :data:`EVAL_INTERVAL_S` seconds, swallowing
all exceptions so a single bad row never kills the loop.

Decision flow per tick (see plan §3.4):

1. Walk ``smart_plugs`` via :func:`mlss_monitor.effectors.store.list_smart_plugs`.
2. Skip disabled rows (``is_enabled = 0``).
3. Skip rows the operator has put under manual control (``auto_mode = 0``).
4. Resolve the live plug handle via ``state.smart_plugs[id]``; skip if
   absent (e.g. plug not on the LAN at boot).
5. Resolve the per-type controller via
   :func:`mlss_monitor.effectors.registry.controller_for`; skip unknown.
6. Fetch the relevant reading (hub: latest hot_tier snapshot row;
   grow: latest ``grow_telemetry`` row for the bound unit).
7. Ask the controller :meth:`should_be_on(reading, rules)`.
8. If the desired state matches ``current_state`` already, skip — this
   is the cheap idempotence guard that keeps us from re-publishing
   SSE events every tick.
9. Otherwise dispatch ``handle.switch(want_on)`` through
   ``state.thread_loop`` (the shared async loop the legacy fan code
   already uses), persist the new ``current_state`` via
   :func:`store.update_last_state`, and publish
   ``effector_state_changed`` on the event bus.

The evaluator replaces the inline ``if settings['enabled'] and
fan_mode == 'auto':`` block that used to live in :func:`app.log_data`;
the legacy ``POST /api/effector`` shim's call into
:func:`api_effectors_v2.apply_state` now flips ``auto_mode = 0`` so
the evaluator backs off the row entirely until the operator returns
to ``"auto"``.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time

from database.init_db import DB_FILE
from mlss_monitor import state
from mlss_monitor.effectors import store
from mlss_monitor.effectors.registry import controller_for

log = logging.getLogger(__name__)

# Tick rate. 10s matches the existing LOG_INTERVAL default and is fast
# enough to react to a step-change reading inside one normal log cycle
# without flogging the SQLite reader.
EVAL_INTERVAL_S = 10


def _read_for_plug(plug: dict) -> dict | None:
    """Return the latest reading dict for *plug*, or ``None`` if unavailable.

    * Hub-scope plugs read the last entry from
      :meth:`mlss_monitor.hot_tier.HotTier.snapshot` (the in-memory ring
      buffer the sensor loop populates every second).
    * Grow-scope plugs read the latest ``grow_telemetry`` row for the
      bound ``grow_unit_id`` — one short-lived SQLite connection per
      call, 5s timeout to match the rest of the hub-room writers.
    """
    if plug["scope"] == "hub":
        hot = state.hot_tier
        if hot is None:
            return None
        snap = hot.snapshot()
        return snap[-1] if snap else None

    # grow_unit-scope: pull the freshest telemetry row for the unit.
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM grow_telemetry WHERE unit_id = ? "
            "ORDER BY timestamp_utc DESC LIMIT 1",
            (plug["grow_unit_id"],),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def evaluate_once() -> None:
    """One full pass over every smart_plug row. Idempotent + safe to
    call from a test (no side effects beyond DB writes + SSE publish).
    """
    for plug in store.list_smart_plugs():
        if not plug["is_enabled"] or not plug["auto_mode"]:
            continue
        handle = state.smart_plugs.get(plug["id"]) if getattr(
            state, "smart_plugs", None,
        ) else None
        if handle is None:
            continue
        ctrl_cls = controller_for(plug["effector_type"])
        if ctrl_cls is None:
            continue
        reading = _read_for_plug(plug)
        if reading is None:
            continue
        want_on = ctrl_cls().should_be_on(reading, plug["rules"] or {})
        desired = "on" if want_on else "off"
        if plug["current_state"] == desired:
            continue
        loop = getattr(state, "thread_loop", None)
        if loop is None:
            # The shared async loop hasn't been initialised yet (e.g.
            # tests that imported the evaluator before app.py wired
            # state.thread_loop) — skip rather than blow up.
            continue
        try:
            future = asyncio.run_coroutine_threadsafe(
                handle.switch(want_on), loop,
            )
            future.result(timeout=5)
        except Exception as exc:  # pylint: disable=broad-except
            log.error("evaluator: switch failed for plug %s: %s",
                      plug["id"], exc)
            continue
        store.update_last_state(plug["id"], desired)
        bus = getattr(state, "event_bus", None)
        if bus is not None:
            bus.publish("effector_state_changed", {
                "id":    plug["id"],
                "state": desired,
                "auto":  True,
            })


def _loop() -> None:
    """Infinite evaluator loop. Swallows ALL exceptions so a single
    bad row never kills the daemon. Catches at the outermost level
    because :func:`evaluate_once` already swallows per-plug errors;
    the catch here is belt-and-braces for an unexpected store-layer
    failure that escapes that inner net.
    """
    while True:
        try:
            evaluate_once()
        except Exception as exc:  # pylint: disable=broad-except
            log.error("evaluator loop error: %s", exc)
        time.sleep(EVAL_INTERVAL_S)


def start_evaluator() -> threading.Thread:
    """Spawn the evaluator daemon thread and return its handle.

    Named ``effector-evaluator`` so ``ps`` / journalctl / Python's
    introspection (``threading.enumerate()``) all show what it is.
    Daemon-flag so the thread dies with the process; no graceful
    shutdown hook is needed because the loop only ever sleeps.
    """
    thread = threading.Thread(
        target=_loop, daemon=True, name="effector-evaluator",
    )
    thread.start()
    return thread
