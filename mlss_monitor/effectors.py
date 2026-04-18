"""Effector registry — maps string keys to controllable device handles.

The registry is intentionally small and additive.  Each entry describes how
to locate the underlying hardware object on :mod:`mlss_monitor.state`, what
kind of device it is, and how to read / write its on/off state via the
shared :data:`mlss_monitor.state.thread_loop` asyncio loop.

Only on/off semantics live here; device-specific config (auto-mode
thresholds, unit rates, etc.) stays on the per-device blueprint.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict

from mlss_monitor import state


@dataclass(frozen=True)
class Effector:
    """Describe a single controllable device for the effector API."""

    key: str
    type: str
    # Callable returning the live hardware handle (or None).  Wrapped in a
    # lambda so registration happens at import time but lookup is deferred
    # until the handle is actually populated by app.py startup.
    get_handle: Callable[[], Any]


_REGISTRY: Dict[str, Effector] = {
    "fan1": Effector(
        key="fan1",
        type="smart_plug",
        get_handle=lambda: state.fan_smart_plug,
    ),
}


def get(key: str) -> Effector | None:
    """Return the :class:`Effector` for *key* or ``None`` if unknown."""
    return _REGISTRY.get(key)


def all_keys() -> list[str]:
    """Return all registered effector keys, in insertion order."""
    return list(_REGISTRY)


def set_state(effector: Effector, on: bool) -> None:
    """Synchronously switch *effector* on/off via the shared thread loop."""
    handle = effector.get_handle()
    if handle is None:
        raise RuntimeError(f"Effector {effector.key!r} has no live handle")
    asyncio.run_coroutine_threadsafe(
        handle.switch(on), state.thread_loop,
    ).result(timeout=5)


def snapshot(effector: Effector) -> dict:
    """Return ``{key, type, state, power_w}`` for *effector*.

    ``state`` is the string ``"on"`` / ``"off"`` / ``None`` (unknown); any
    networking errors are swallowed and yield ``None`` values so the list
    endpoint never fails because a single plug is unreachable.
    """
    handle = effector.get_handle()
    out: dict = {"key": effector.key, "type": effector.type,
                 "state": None, "power_w": None}
    if handle is None:
        return out
    try:
        plug_state = asyncio.run_coroutine_threadsafe(
            handle.get_state(), state.thread_loop,
        ).result(timeout=5)
        if isinstance(plug_state, dict) and "state" in plug_state:
            out["state"] = "on" if plug_state["state"] else "off"
    except Exception:
        pass
    try:
        power = asyncio.run_coroutine_threadsafe(
            handle.get_power(), state.thread_loop,
        ).result(timeout=5)
        if isinstance(power, dict):
            out["power_w"] = power.get("power_w")
    except Exception:
        pass
    return out
