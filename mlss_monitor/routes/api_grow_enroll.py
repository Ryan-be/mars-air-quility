"""POST /api/grow/enroll — first-boot enrollment endpoint for new units."""
import sqlite3
from datetime import datetime
from flask import Blueprint, request, jsonify

from database.init_db import DB_FILE
from mlss_monitor.grow.auth import (
    verify_enrollment_key, generate_token, hash_secret, AuthError,
)

api_grow_enroll_bp = Blueprint("api_grow_enroll", __name__)


@api_grow_enroll_bp.route("/api/grow/enroll", methods=["POST"])
def enroll():
    body = request.get_json(silent=True) or {}

    enrollment_key = body.get("enrollment_key")
    hardware_serial = body.get("hardware_serial")
    plant = body.get("plant") or {}
    plant_name = plant.get("name")

    if not enrollment_key or not hardware_serial or not plant_name:
        return jsonify({
            "error": "missing_fields",
            "required": ["enrollment_key", "hardware_serial", "plant.name"],
        }), 400

    try:
        if not verify_enrollment_key(enrollment_key):
            return jsonify({"error": "invalid_enrollment_key"}), 401
    except AuthError as exc:
        return jsonify({"error": "auth_not_configured", "detail": str(exc)}), 500

    plant_type = plant.get("type", "generic")
    medium_type = plant.get("medium", "soil")
    now = datetime.utcnow()

    raw_token = generate_token()
    token_hash = hash_secret(raw_token)

    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        existing = conn.execute(
            "SELECT id FROM grow_units WHERE hardware_serial=?",
            (hardware_serial,),
        ).fetchone()
        if existing:
            unit_id = existing[0]
            conn.execute(
                "UPDATE grow_units SET bearer_token_hash=?, is_active=1, "
                "label=COALESCE(label, ?) WHERE id=?",
                (token_hash, plant_name, unit_id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO grow_units "
                "(hardware_serial, label, enrolled_at, bearer_token_hash, "
                " plant_type, medium_type, phase_set_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (hardware_serial, plant_name, now, token_hash,
                 plant_type, medium_type, now),
            )
            unit_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    return jsonify({"unit_id": unit_id, "token": raw_token}), 201
