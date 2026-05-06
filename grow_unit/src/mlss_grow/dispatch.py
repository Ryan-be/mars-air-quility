"""Single switchboard for incoming WS `command` messages.

The server sends commands in two slightly different shapes:
  * Legacy:  {"name": "identify", "args": {...}}
            {"name": "water_now", "args": {...}}
            {"name": "snap_photo"}
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


async def dispatch_command(payload: dict, ctx: DispatchContext) -> None:
    """Route an incoming command payload to the right handler.

    Accepts both `kind`-keyed (new) and `name`-keyed (legacy) payloads.
    Catches all exceptions internally — never raises.
    """
    try:
        kind = payload.get("kind")
        name = payload.get("name")
        if kind == "config_changed":
            await _handle_config_changed(ctx)
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
        else:
            log.warning(
                "dispatcher: unknown command (kind=%r, name=%r) — dropping",
                kind, name,
            )
    except Exception as exc:
        log.exception("dispatcher: command failed: %s", exc)


async def _handle_config_changed(ctx: DispatchContext) -> None:
    """Pull fresh config and apply to the running loop_cfg.

    Best-effort: a network/auth/server failure is logged but does NOT
    abort the dispatcher. The firmware will re-pull on its next
    config_changed (or, in the future, on each WS reconnect).
    """
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
