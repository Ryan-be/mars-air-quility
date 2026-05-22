"""Legacy effector API — now a deprecation-headed shim onto v2.

The original ``POST /api/effector`` toggle (and ``GET /api/effectors``
listing) lived here and dispatched via the in-memory registry in
:mod:`mlss_monitor.effectors`. Phase 2 of the MLSS topology feature
moves the canonical surface to :mod:`mlss_monitor.routes.api_effectors_v2`
(see ``docs/superpowers/plans/2026-05-22-mlss-topology.md``).

We keep the POST endpoint as a thin compatibility shim:

* ``POST /api/effector {key:"fan1", state:"on"|"off"}`` looks up the
  seeded row in ``smart_plugs`` (the migration in
  :mod:`database.effectors_schema` guarantees it exists when
  ``MLSS_FAN_KASA_SMART_PLUG_IP`` is set) and routes through v2's
  :func:`apply_state` helper. The response carries a
  ``Deprecation: true`` header so any external consumer is on notice.

The legacy ``GET /api/effectors`` is intentionally removed — the v2
blueprint serves that path with the new ``{"effectors": [...]}``
shape. Old consumers calling the legacy GET will see the v2 shape.
"""
from __future__ import annotations

import logging
import sqlite3

from flask import Blueprint, jsonify, request

from database.init_db import DB_FILE
from mlss_monitor.rbac import require_role
from mlss_monitor.routes.api_effectors_v2 import apply_state

log = logging.getLogger(__name__)

api_effectors_bp = Blueprint("api_effectors", __name__)


def _resolve_fan1_id() -> int | None:
    """Find the seeded hub-room fan row id; returns None if absent.

    The migration in ``database/effectors_schema.py`` inserts the legacy
    fan as the first row when ``MLSS_FAN_KASA_SMART_PLUG_IP`` is set;
    we look it up by the lowest-id (effector_type='fan', scope='hub')
    pair so the shim keeps working even if an admin has added more fans.
    """
    conn = sqlite3.connect(DB_FILE, timeout=5)
    try:
        row = conn.execute(
            "SELECT id FROM smart_plugs "
            "WHERE effector_type='fan' AND scope='hub' "
            "ORDER BY id LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


@api_effectors_bp.route("/api/effector", methods=["POST"])
@require_role("controller", "admin")
def set_effector():
    """Toggle an effector on or off via ``{"key": ..., "state": "on"|"off"}``.

    DEPRECATED — prefer ``POST /api/effectors/<id>/state`` on the v2
    blueprint. Currently only ``key="fan1"`` is supported (the legacy
    single-fan registry only ever exposed that one entry).
    """
    data = request.get_json(force=True, silent=True) or {}
    key = data.get("key")
    desired = data.get("state")

    if not key:
        return jsonify({"error": "'key' is required in the request body."}), 400
    if desired not in ("on", "off"):
        return jsonify({"error": "'state' must be 'on' or 'off'."}), 400

    if key != "fan1":
        return jsonify({"error": f"Unknown effector {key!r}"}), 404

    plug_id = _resolve_fan1_id()
    if plug_id is None:
        return jsonify({
            "error": "No fan smart-plug row in smart_plugs — set "
                     "MLSS_FAN_KASA_SMART_PLUG_IP and restart to seed.",
        }), 500

    body, status = apply_state(plug_id, desired)
    # Translate the v2 response shape back to the legacy
    # `{message, key, state}` shape so any existing consumer doesn't
    # also need to change to read the new fields.
    if status == 200:
        legacy_body = {
            "message": f"Effector {key!r} set to {desired}.",
            "key":     key,
            "state":   desired,
        }
    else:
        legacy_body = body
    response = jsonify(legacy_body)
    response.status_code = status
    response.headers["Deprecation"] = "true"
    return response
