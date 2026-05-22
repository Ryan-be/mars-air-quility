"""Effector subsystem — legacy single-fan registry + new generalised model.

This module presents two APIs:

* **Legacy** (kept for backwards-compat during the Phase 2 → 3 migration
  of the MLSS topology feature): a tiny in-memory registry that maps
  string keys (``"fan1"``) to a :class:`Effector` value-object wrapping
  the live :data:`mlss_monitor.state.fan_smart_plug` handle. The legacy
  ``POST /api/effector`` shim in :mod:`mlss_monitor.routes.api_effectors`
  uses ``get`` / ``set_state`` / ``snapshot`` / ``all_keys``.

* **Generalised** (Phase 2+): :mod:`mlss_monitor.effectors.store` is the
  pure CRUD layer against the ``smart_plugs`` table;
  :mod:`mlss_monitor.effectors.base` holds the type/scope enums + the
  per-type scope-compatibility matrix used by the v2 API validator.
  Phase 3+ adds per-type ``EffectorController`` classes and the
  periodic evaluator loop.

Only on/off semantics live in the legacy half; device-specific config
(auto-mode thresholds, unit rates, etc.) lives on either the per-device
blueprint (legacy fan) or the ``smart_plugs.rules_json`` blob (v2).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict

from mlss_monitor import state


@dataclass(frozen=True)
class Effector:
    """Describe a single controllable device for the legacy effector API."""

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
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        power = asyncio.run_coroutine_threadsafe(
            handle.get_power(), state.thread_loop,
        ).result(timeout=5)
        if isinstance(power, dict):
            out["power_w"] = power.get("power_w")
    except Exception:  # pylint: disable=broad-except
        pass
    return out
