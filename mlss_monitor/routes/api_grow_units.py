"""REST endpoints for the browser to read grow unit state.

GET  /api/grow/units                        — fleet view, list
GET  /api/grow/units/<id>                   — detail
POST /api/grow/units/<id>/identify          — push identify command via WS
POST /api/grow/units/<id>/water-now         — push manual watering command via WS
"""
import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request

from database.init_db import DB_FILE
from mlss_monitor import state

api_grow_units_bp = Blueprint("api_grow_units", __name__)

_STALE_AFTER = timedelta(seconds=30)
_OFFLINE_AFTER = timedelta(minutes=5)


def _classify_status(last_seen_at: str | None) -> str:
    if last_seen_at is None:
        return "offline"
    seen = datetime.fromisoformat(last_seen_at) if isinstance(last_seen_at, str) else last_seen_at
    age = datetime.utcnow() - seen
    if age < _STALE_AFTER:
        return "online"
    if age < _OFFLINE_AFTER:
        return "stale"
    return "offline"


@api_grow_units_bp.route("/api/grow/units", methods=["GET"])
def list_units():
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, label, plant_type, medium_type, current_phase, "
        "       sown_at, enrolled_at, last_seen_at, last_known_state_json "
        "FROM grow_units WHERE is_active=1 ORDER BY label"
    ).fetchall()
    conn.close()

    units = []
    for r in rows:
        units.append({
            "id": r["id"],
            "label": r["label"],
            "plant_type": r["plant_type"],
            "medium_type": r["medium_type"],
            "current_phase": r["current_phase"],
            "sown_at": r["sown_at"],
            "enrolled_at": r["enrolled_at"],
            "last_seen_at": r["last_seen_at"],
            "status": _classify_status(r["last_seen_at"]),
            "last_known_state": json.loads(r["last_known_state_json"])
                                if r["last_known_state_json"] else None,
        })
    return jsonify({"units": units})


@api_grow_units_bp.route("/api/grow/units/<int:unit_id>", methods=["GET"])
def get_unit(unit_id):
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM grow_units WHERE id=? AND is_active=1", (unit_id,)
    ).fetchone()
    if row is None:
        conn.close()
        return jsonify({"error": "not_found"}), 404

    caps = conn.execute(
        "SELECT channel, hardware, is_required, unit_label, details_json "
        "FROM grow_unit_capabilities WHERE unit_id=?", (unit_id,)
    ).fetchall()
    conn.close()

    body = {k: row[k] for k in row.keys()}
    body.pop("bearer_token_hash", None)  # never expose
    body["status"] = _classify_status(row["last_seen_at"])
    body["last_known_state"] = (
        json.loads(row["last_known_state_json"])
        if row["last_known_state_json"] else None
    )
    body.pop("last_known_state_json", None)
    body["capabilities"] = [
        {
            "channel": c["channel"],
            "hardware": c["hardware"],
            "is_required": bool(c["is_required"]),
            "unit_label": c["unit_label"],
            "details": json.loads(c["details_json"]) if c["details_json"] else None,
        }
        for c in caps
    ]
    return jsonify(body)


def _push_command_blocking(unit_id: int, command: dict) -> tuple[int, dict]:
    """Send a command to a unit via the WS registry. Returns (status, body)."""
    registry = state.grow_ws_registry
    if registry is None or not registry.is_connected(unit_id):
        return 503, {"error": "unit_not_connected"}
    msg = json.dumps({
        "type": "command",
        "ts": datetime.utcnow().isoformat() + "Z",
        "payload": command,
    })
    # The WS listener runs on its own loop on another thread — schedule there
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(registry.send_to_unit(unit_id, msg))
        loop.close()
    except KeyError:
        return 503, {"error": "unit_not_connected"}
    return 202, {"queued": True}


@api_grow_units_bp.route("/api/grow/units/<int:unit_id>/identify", methods=["POST"])
def identify(unit_id):
    status, body = _push_command_blocking(unit_id, {
        "name": "identify",
        "args": {"duration_s": 10},
    })
    return jsonify(body), status


@api_grow_units_bp.route("/api/grow/units/<int:unit_id>/water-now", methods=["POST"])
def water_now(unit_id):
    body_in = request.get_json(silent=True) or {}
    duration_s = max(1, min(30, int(body_in.get("duration_s", 5))))  # safety cap
    status, body = _push_command_blocking(unit_id, {
        "name": "water_now",
        "args": {"duration_s": duration_s},
    })
    return jsonify(body), status
