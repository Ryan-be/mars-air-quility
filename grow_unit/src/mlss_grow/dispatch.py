"""Single switchboard for incoming WS `command` messages.

The server sends commands in two slightly different shapes:
  * Legacy:  {"name": "identify", "args": {...}}
            {"name": "water_now", "args": {...}}
            {"name": "snap_photo"}
            {"name": "light_override",
             "args": {"state": "on"|"off", "duration_min": int}}
            {"name": "reload_config"}    # alias for kind=config_changed
            {"name": "reboot"}           # systemctl reboot the Pi
  * New:    {"kind": "config_changed", "section": "pid"}
            {"kind": "safety_override", "action": "force_pump_on",
             "duration_s": 10}

Both are accepted because they're produced by different server modules
(`api_grow_units` uses `name`; `api_grow_config` uses `kind`).
Long-term the spellings should converge, but for now the dispatcher
accepts either.

The dispatcher is intentionally a pure function over (payload, context)
so it can be tested directly without booting the whole service. The
runtime wiring in `service.py` constructs a DispatchContext at startup
and feeds the command queue through.

Robustness: any exception inside a command handler is caught + logged.
The dispatcher MUST NOT raise out of `dispatch_command` because the
caller (the command_handler coroutine) is in the same async loop as
the WS receive task — a propagated exception would tear that down too.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Any

from mlss_grow.config_sync import pull_unit_config, apply_config
from mlss_grow.safety_override import (
    SafetyOverrideState,
    invoke_safety_override,
)

log = logging.getLogger(__name__)


@dataclass
class DispatchContext:
    """Bundle of state the command handlers need.

    Carried through from `_run_main_loop` so the dispatcher doesn't have
    to reach into module globals; makes test mocking trivial.
    """
    unit_id: int
    server_url: str
    token: str
    server_cert_path: Optional[str]
    pump: Any           # Actuator
    light: Any          # Actuator (with blink_pattern)
    camera: Any         # Camera or None
    loop_cfg: Any       # safety_loop.LoopConfig — apply_config mutates this
    ws: Any             # WSClient — for snap_photo response
    override_state: Optional[SafetyOverrideState] = None
    # Phase 3 Task 4: buffer reference so the `clear_buffer` command can
    # empty the local SQLite buffer on operator request. Optional because
    # legacy tests construct DispatchContext without one — the handler
    # logs + drops the command if buffer is None.
    buffer: Any = None  # LocalBuffer or None


async def dispatch_command(payload: dict, ctx: DispatchContext) -> None:
    """Route an incoming command payload to the right handler.

    Accepts both `kind`-keyed (new) and `name`-keyed (legacy) payloads.
    Catches all exceptions internally — never raises.
    """
    try:
        kind = payload.get("kind")
        name = payload.get("name")
        if kind == "config_changed":
            await _handle_config_changed(payload, ctx)
        elif kind == "safety_override":
            _handle_safety_override(payload, ctx)
        elif name == "identify":
            duration_s = payload.get("args", {}).get("duration_s", 10)
            ctx.light.blink_pattern(duration_s=duration_s)
        elif name == "water_now":
            duration_s = payload.get("args", {}).get("duration_s", 5)
            ctx.pump.pulse(duration_s)
        elif name == "snap_photo" and ctx.camera is not None:
            jpeg, meta = ctx.camera.capture()
            meta["taken_at"] = datetime.utcnow().isoformat() + "Z"
            await ctx.ws.send_photo(meta, jpeg)
        elif name == "light_override":
            _handle_light_override(payload, ctx)
        elif name == "reload_config":
            # Spec §6 lists `reload_config` alongside `kind=config_changed`.
            # Both mean the same thing — pull + apply. Route through the
            # canonical handler so behaviour stays in lock-step.
            await _handle_config_changed(
                {"section": "<reload_config>"}, ctx,
            )
        elif name == "reboot":
            _handle_reboot()
        elif name == "clear_buffer":
            _handle_clear_buffer(ctx)
        else:
            log.warning(
                "dispatcher: unknown command (kind=%r, name=%r) — dropping",
                kind, name,
            )
    except Exception as exc:
        log.exception("dispatcher: command failed: %s", exc)


async def _handle_config_changed(payload: dict, ctx: DispatchContext) -> None:
    """Pull fresh config and apply to the running loop_cfg.

    Best-effort: a network/auth/server failure is logged but does NOT
    abort the dispatcher. The firmware will re-pull on its next
    config_changed (or, in the future, on each WS reconnect).
    """
    section = payload.get("section", "<unknown>")
    log.info(
        "dispatcher: config_changed (section=%s) - pulling fresh config",
        section,
    )
    try:
        unit_cfg = pull_unit_config(
            ctx.server_url, ctx.unit_id, ctx.token, ctx.server_cert_path,
        )
    except Exception as exc:
        log.warning(
            "dispatcher: config pull failed (%s) — keeping old config",
            exc,
        )
        return
    try:
        apply_config(unit_cfg, ctx.loop_cfg)
        log.info(
            "dispatcher: config applied (phase=%s, plant_type=%s)",
            unit_cfg.current_phase, unit_cfg.plant_type,
        )
    except Exception as exc:
        # apply_config shouldn't raise under normal use, but defensive:
        # a malformed light_window from the server shouldn't kill the
        # dispatcher.
        log.exception("dispatcher: apply_config failed: %s", exc)


def _handle_safety_override(payload: dict, ctx: DispatchContext) -> None:
    """Forward to invoke_safety_override. Schedules off-flip on a Timer
    so the dispatcher (and thus the WS receive loop) stays responsive
    even on a long-duration override."""
    if ctx.override_state is None:
        log.warning(
            "dispatcher: safety_override received but override_state not "
            "wired up — dropping action=%r", payload.get("action"),
        )
        return
    action = payload.get("action")
    duration_s = float(payload.get("duration_s", 0))
    invoke_safety_override(
        action, duration_s,
        pump=ctx.pump, light=ctx.light, state=ctx.override_state,
    )


def _handle_light_override(payload: dict, ctx: DispatchContext) -> None:
    """Spec §6 `light_override` legacy command.

    Args: ``{"state": "on"|"off", "duration_min": int}``. We reuse the
    safety_override plumbing (Timer-scheduled off-flip) so the dispatcher
    thread doesn't block for `duration_min` minutes — same reasoning as
    `_handle_safety_override`. For `state=off`, we simply turn the light
    off immediately (no duration semantics — the regular light schedule
    will resume on the next safety_loop tick whose schedule asks for on).
    """
    if ctx.override_state is None:
        log.warning(
            "dispatcher: light_override received but override_state not "
            "wired up — dropping",
        )
        return
    args = payload.get("args") or {}
    state = args.get("state")
    duration_min = float(args.get("duration_min", 0))
    if state == "on":
        invoke_safety_override(
            "force_light_on", duration_min * 60.0,
            pump=ctx.pump, light=ctx.light, state=ctx.override_state,
        )
    elif state == "off":
        invoke_safety_override(
            "force_light_off", 0.0,
            pump=ctx.pump, light=ctx.light, state=ctx.override_state,
        )
    else:
        log.warning(
            "dispatcher: light_override unknown state=%r — dropping", state,
        )


def _handle_clear_buffer(ctx: DispatchContext) -> None:
    """Spec §6 (Phase 3 Task 4) `clear_buffer` command.

    Empties the local SQLite buffer.sqlite — destructive, but the server
    has already gated the action behind a confirm modal, so by the time
    we receive it the operator has consciously chosen to drop un-replayed
    telemetry. We just clear and log.

    Defensive: if `ctx.buffer` is None (legacy DispatchContext, or a test
    wiring) log + drop rather than raising — same pattern as
    `_handle_safety_override` when override_state is unset.
    """
    if ctx.buffer is None:
        log.warning(
            "dispatcher: clear_buffer received but buffer not wired up — "
            "dropping command",
        )
        return
    log.info("dispatcher: clear_buffer command received — emptying local buffer")
    ctx.buffer.clear()


def _handle_reboot() -> None:
    """Spec §6 `reboot` command — reboot the host via systemctl.

    Logged at INFO before the call so journalctl shows what triggered
    the reboot. Run on a daemon thread so the dispatcher (and WS
    receive coroutine) returns immediately — though by the time the
    reboot signal hits, both will be torn down anyway.

    The subprocess call is intentionally fire-and-forget; we don't
    await its exit because the Pi will be rebooting and we don't want
    to keep a handle in a partially-shutdown state.
    """
    log.info("dispatcher: reboot command received — invoking systemctl reboot")

    def _run():
        try:
            subprocess.run(
                ["sudo", "systemctl", "reboot"],
                check=False,
            )
        except Exception as exc:
            # Most likely sudo not available in dev — log + ignore.
            log.exception("dispatcher: reboot failed: %s", exc)

    threading.Thread(target=_run, daemon=True).start()
