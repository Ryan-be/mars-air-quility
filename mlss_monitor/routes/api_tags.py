"""GET /api/tags — returns the controlled vocabulary of valid event tags."""

from flask import Blueprint, jsonify

from mlss_monitor import state

api_tags_bp = Blueprint("api_tags", __name__)


@api_tags_bp.route("/api/tags")
def list_tags():
    """Return all valid tag IDs and labels derived from loaded fingerprints."""
    engine = state.detection_engine
    if engine and engine._attribution_engine:
        tags = engine._attribution_engine.tags_with_labels()
    else:
        tags = []
    return jsonify({"tags": tags})
