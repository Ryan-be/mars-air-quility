"""Settings API routes: location, energy rate, and inference thresholds."""

from flask import Blueprint, jsonify, request

from database.db_logger import (
    get_all_thresholds,
    get_location,
    get_unit_rate,
    save_location,
    save_unit_rate,
    update_threshold,
)

api_settings_bp = Blueprint("api_settings", __name__)


@api_settings_bp.route("/api/settings/location", methods=["GET"])
def get_location_route():
    return jsonify(get_location())


@api_settings_bp.route("/api/settings/location", methods=["POST"])
def save_location_route():
    data = request.get_json()
    save_location(data.get("lat"), data.get("lon"), data.get("name", ""))
    return jsonify({"message": "Location saved"})


@api_settings_bp.route("/api/settings/energy", methods=["GET"])
def get_energy_settings():
    return jsonify({"unit_rate_pence": get_unit_rate()})


@api_settings_bp.route("/api/settings/energy", methods=["POST"])
def save_energy_settings():
    data = request.get_json()
    try:
        rate = float(data.get("unit_rate_pence", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "unit_rate_pence must be a number"}), 400
    save_unit_rate(rate)
    return jsonify({"message": "Energy rate saved"})


@api_settings_bp.route("/api/settings/thresholds", methods=["GET"])
def get_thresholds_route():
    return jsonify(get_all_thresholds())


@api_settings_bp.route("/api/settings/thresholds", methods=["POST"])
def save_thresholds_route():
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({"error": "Expected JSON object"}), 400
    updated = 0
    for key, value in data.items():
        if value is None or value == "":
            update_threshold(key, None)
        else:
            try:
                update_threshold(key, float(value))
            except (TypeError, ValueError):
                return jsonify({"error": f"Invalid value for {key}"}), 400
        updated += 1
    return jsonify({"message": f"{updated} threshold(s) updated"})
