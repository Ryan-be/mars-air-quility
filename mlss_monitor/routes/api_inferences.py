"""API routes for environment inferences."""

from flask import Blueprint, jsonify, request

from database.db_logger import (
    dismiss_inference,
    get_inferences,
    update_inference_notes,
)

api_inferences_bp = Blueprint("api_inferences", __name__)


@api_inferences_bp.route("/api/inferences")
def list_inferences():
    limit = request.args.get("limit", 50, type=int)
    include_dismissed = request.args.get("dismissed", "0") == "1"
    return jsonify(get_inferences(limit=limit, include_dismissed=include_dismissed))


@api_inferences_bp.route("/api/inferences/<int:inference_id>/notes", methods=["POST"])
def save_notes(inference_id):
    data = request.get_json(force=True)
    notes = data.get("notes", "")
    update_inference_notes(inference_id, notes)
    return jsonify({"ok": True})


@api_inferences_bp.route("/api/inferences/<int:inference_id>/dismiss", methods=["POST"])
def dismiss(inference_id):
    dismiss_inference(inference_id)
    return jsonify({"ok": True})
