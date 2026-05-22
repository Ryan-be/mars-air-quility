"""V2 effector REST API.

Owns the ``/api/effectors`` resource family — list / get / create /
patch / delete / state / layout. Replaces the legacy single-fan
``POST /api/effector`` endpoint, which now shims onto this module via
:func:`mlss_monitor.routes.api_effectors.set_effector`.

Routes:

* ``GET    /api/effectors``               — any logged-in user
* ``GET    /api/effectors/<int:id>``      — any logged-in user
* ``POST   /api/effectors``               — admin
* ``PATCH  /api/effectors/<int:id>``      — admin
* ``DELETE /api/effectors/<int:id>``      — admin
* ``POST   /api/effectors/<int:id>/state``— controller+admin
* ``PATCH  /api/effectors/layout``        — controller+admin

State changes (``POST .../state``) publish an ``effector_state_changed``
event on the in-process bus so the topology UI's SSE channel can refresh
without polling.

Validation philosophy: pure validation lives in
:mod:`mlss_monitor.effectors.base` (the per-type compatibility matrix
+ the canonical type list). The route handlers translate those checks
into 400 responses; SQLite ``IntegrityError`` on a duplicate
``kasa_host`` becomes 409.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime
from typing import Any

from flask import Blueprint, jsonify, request, session

from database.init_db import DB_FILE
from mlss_monitor import state
from mlss_monitor.effectors import store
from mlss_monitor.effectors.base import (
    COMPATIBLE_SCOPES,
    EFFECTOR_TYPES,
    is_scope_compatible,
)
from mlss_monitor.rbac import require_role

log = logging.getLogger(__name__)

api_effectors_v2_bp = Blueprint("api_effectors_v2", __name__)


# --- helpers --------------------------------------------------------------

def _require_logged_in():
    """Return None when logged in, or a (json, status) tuple on auth fail.

    Mirrors the pattern in :class:`mlss_monitor.rbac.require_role` but
    without forcing a specific role — used by the read-only endpoints
    (GET list, GET single) where any signed-in user is fine.
    """
    if not session.get("logged_in"):
        return jsonify({"error": "Unauthorised"}), 401
    return None


def _validate_create(body: dict) -> str | None:
    """Return an error message string, or ``None`` if body is valid."""
    for required in ("label", "effector_type", "scope", "kasa_host"):
        if required not in body or body[required] in (None, ""):
            return f"'{required}' is required"
    etype = body["effector_type"]
    scope = body["scope"]
    if etype not in EFFECTOR_TYPES:
        return f"Unknown effector_type {etype!r}"
    if scope not in ("hub", "grow_unit"):
        return f"Invalid scope {scope!r}"
    if not is_scope_compatible(etype, scope):
        return (f"effector_type {etype!r} cannot be scoped to {scope!r}; "
                f"allowed: {sorted(COMPATIBLE_SCOPES[etype])}")
    if scope == "grow_unit" and not body.get("grow_unit_id"):
        return "'grow_unit_id' is required when scope='grow_unit'"
    if scope == "hub" and body.get("grow_unit_id") is not None:
        return "'grow_unit_id' must be null when scope='hub'"
    return None


def _validate_patch(body: dict, current: dict) -> str | None:
    """Validate a partial update against the existing row + type matrix.

    Field-by-field gating:
    * If the caller is only renaming/toggling auto/updating rules and the
      type+scope don't change, no compatibility check is needed.
    * If ``effector_type`` OR ``scope`` is in the patch, we compute the
      RESULT pair (patched value falling back to current row's value)
      and validate that pair holistically — this handles the case where
      a single PATCH simultaneously moves an effector from hub to a
      specific grow unit AND retypes it.
    """
    if not body:
        return "Empty body — nothing to patch"
    new_type = body.get("effector_type", current["effector_type"])
    new_scope = body.get("scope", current["scope"])
    if "effector_type" in body and new_type not in EFFECTOR_TYPES:
        return f"Unknown effector_type {new_type!r}"
    if "scope" in body and new_scope not in ("hub", "grow_unit"):
        return f"Invalid scope {new_scope!r}"
    if ("effector_type" in body or "scope" in body) and not is_scope_compatible(
        new_type, new_scope,
    ):
        return (f"effector_type {new_type!r} cannot be scoped to "
                f"{new_scope!r}; allowed: "
                f"{sorted(COMPATIBLE_SCOPES[new_type])}")
    # Pull the would-be grow_unit_id off either the patch or the row.
    new_gu = (body.get("grow_unit_id")
              if "grow_unit_id" in body
              else current["grow_unit_id"])
    if new_scope == "grow_unit" and not new_gu:
        return "'grow_unit_id' is required when scope='grow_unit'"
    if new_scope == "hub" and new_gu is not None:
        return "'grow_unit_id' must be null when scope='hub'"
    return None


# --- list / get -----------------------------------------------------------


@api_effectors_v2_bp.route("/api/effectors", methods=["GET"])
def list_effectors():
    err = _require_logged_in()
    if err is not None:
        return err
    return jsonify({"effectors": store.list_smart_plugs()})


@api_effectors_v2_bp.route("/api/effectors/<int:plug_id>", methods=["GET"])
def get_effector(plug_id: int):
    err = _require_logged_in()
    if err is not None:
        return err
    row = store.get_smart_plug(plug_id)
    if row is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify(row)


# --- create / patch / delete ---------------------------------------------


@api_effectors_v2_bp.route("/api/effectors", methods=["POST"])
@require_role("admin")
def create_effector():
    body: dict = request.get_json(silent=True) or {}
    err = _validate_create(body)
    if err is not None:
        return jsonify({"error": err}), 400
    try:
        new_id = store.create_smart_plug(
            label=body["label"],
            effector_type=body["effector_type"],
            scope=body["scope"],
            kasa_host=body["kasa_host"],
            grow_unit_id=body.get("grow_unit_id"),
            protocol=body.get("protocol", "kasa"),
            is_enabled=int(body.get("is_enabled", 1)),
            auto_mode=int(body.get("auto_mode", 1)),
            rules=body.get("rules"),
            layout=body.get("layout"),
        )
    except sqlite3.IntegrityError as exc:
        # UNIQUE(kasa_host) is the common case here; the CHECK
        # constraints we already gated upstream are belt-and-braces.
        msg = str(exc).lower()
        if "unique" in msg and "kasa_host" in msg:
            return jsonify({"error": "duplicate_kasa_host"}), 409
        log.warning("create_effector integrity error: %s", exc)
        return jsonify({"error": "constraint_violation",
                        "detail": str(exc)}), 400
    row = store.get_smart_plug(new_id)
    return jsonify(row), 201


@api_effectors_v2_bp.route("/api/effectors/<int:plug_id>", methods=["PATCH"])
@require_role("admin")
def patch_effector(plug_id: int):
    body: dict = request.get_json(silent=True) or {}
    current = store.get_smart_plug(plug_id)
    if current is None:
        return jsonify({"error": "not_found"}), 404
    err = _validate_patch(body, current)
    if err is not None:
        return jsonify({"error": err}), 400
    try:
        ok = store.update_smart_plug(plug_id, **body)
    except KeyError as exc:
        return jsonify({"error": f"unknown_field: {exc}"}), 400
    except sqlite3.IntegrityError as exc:
        msg = str(exc).lower()
        if "unique" in msg and "kasa_host" in msg:
            return jsonify({"error": "duplicate_kasa_host"}), 409
        return jsonify({"error": "constraint_violation",
                        "detail": str(exc)}), 400
    if not ok:
        return jsonify({"error": "no_change"}), 400
    return jsonify(store.get_smart_plug(plug_id))


@api_effectors_v2_bp.route("/api/effectors/<int:plug_id>", methods=["DELETE"])
@require_role("admin")
def delete_effector(plug_id: int):
    if not store.delete_smart_plug(plug_id):
        return jsonify({"error": "not_found"}), 404
    return jsonify({"deleted": plug_id})


# --- state toggle ---------------------------------------------------------


def apply_state(plug_id: int, desired_state: str) -> tuple[dict, int]:
    """Pure state-change machinery, callable from this module + the
    legacy ``api_effectors.set_effector`` shim.

    Returns ``(body_dict, http_status)``. Caller wraps in jsonify.
    Side effects:
    * Switches the live plug handle (best-effort, network errors → 500).
    * Persists ``current_state`` to ``smart_plugs``.
    * For 'on'/'off', flips ``auto_mode`` to 0 (forced override).
    * For 'auto', flips ``auto_mode`` to 1.
    * Publishes an ``effector_state_changed`` event on the SSE bus.
    """
    if desired_state not in ("on", "off", "auto"):
        return {"error": "state must be 'on', 'off', or 'auto'"}, 400
    row = store.get_smart_plug(plug_id)
    if row is None:
        return {"error": "not_found"}, 404

    handle = (state.smart_plugs.get(plug_id)
              if getattr(state, "smart_plugs", None) else None)

    if desired_state in ("on", "off"):
        # Best-effort network call. Tests stub asyncio out so this is a
        # no-op there; production routes it through state.thread_loop
        # like the legacy fan switch does.
        if handle is not None and state.thread_loop is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    handle.switch(desired_state == "on"),
                    state.thread_loop,
                )
                future.result(timeout=5)
            except Exception as exc:  # pylint: disable=broad-except
                log.error("apply_state: switch failed for plug %s: %s",
                          plug_id, exc)
                return {"error": f"switch_failed: {exc}"}, 500
        store.update_smart_plug(
            plug_id, current_state=desired_state, auto_mode=0,
        )
    else:  # 'auto'
        # 'auto' returns the plug to rule-driven control; physical state
        # will catch up on the next evaluator tick.
        store.update_smart_plug(plug_id, auto_mode=1)

    # Record the new current_state timestamp explicitly for the live UI.
    if desired_state in ("on", "off"):
        store.update_last_state(plug_id, desired_state)

    bus = getattr(state, "event_bus", None)
    if bus is not None:
        bus.publish("effector_state_changed", {
            "id":    plug_id,
            "state": desired_state,
            "auto":  desired_state == "auto",
        })

    return {
        "id":    plug_id,
        "state": desired_state,
        "auto":  desired_state == "auto",
    }, 200


@api_effectors_v2_bp.route(
    "/api/effectors/<int:plug_id>/state", methods=["POST"],
)
@require_role("controller", "admin")
def post_state(plug_id: int):
    body: dict = request.get_json(silent=True) or {}
    desired = body.get("state")
    out, status = apply_state(plug_id, desired)
    return jsonify(out), status


# --- bulk layout save -----------------------------------------------------


_VALID_KINDS = ("hub", "grow", "effector")


@api_effectors_v2_bp.route("/api/effectors/layout", methods=["PATCH"])
@require_role("controller", "admin")
def patch_layout():
    """Bulk-save node positions in a single transaction.

    Body: ``{"positions": [{kind, id, x, y}, ...]}``.

    * ``kind='effector'`` rows update ``smart_plugs.layout_json`` on the
      matching row id.
    * ``kind in ('hub','grow')`` rows upsert into ``node_layout`` keyed
      on (kind, id-as-string).

    Either every position commits or none does — wrapping in a single
    sqlite3 transaction so a partial drag-drop save can't leave the UI
    state half-applied.
    """
    body: dict = request.get_json(silent=True) or {}
    positions: list[dict[str, Any]] = body.get("positions") or []
    # Validate everything UP FRONT so we don't have to roll back a
    # partially-applied transaction on the first bad entry.
    for pos in positions:
        if pos.get("kind") not in _VALID_KINDS:
            return jsonify({"error": f"invalid kind {pos.get('kind')!r}"}), 400
        for required in ("id", "x", "y"):
            if required not in pos:
                return jsonify({
                    "error": f"missing '{required}' in position entry",
                }), 400

    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_FILE, timeout=5)
    try:
        cur = conn.cursor()
        cur.execute("BEGIN")
        for pos in positions:
            kind = pos["kind"]
            node_id = str(pos["id"])
            x_val = float(pos["x"])
            y_val = float(pos["y"])
            if kind == "effector":
                cur.execute(
                    "UPDATE smart_plugs SET layout_json = ?, updated_at = ? "
                    "WHERE id = ?",
                    (f'{{"x": {x_val}, "y": {y_val}}}', now, int(node_id)),
                )
            else:
                # INSERT OR REPLACE upserts on the (kind, id) PK so a
                # repeated drag of the same node overwrites rather than
                # piling rows.
                cur.execute(
                    "INSERT OR REPLACE INTO node_layout "
                    "(node_kind, node_id, x, y, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (kind, node_id, x_val, y_val, now),
                )
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        log.error("patch_layout: %s", exc)
        return jsonify({"error": "db_error", "detail": str(exc)}), 500
    finally:
        conn.close()

    return jsonify({"saved": len(positions)})
