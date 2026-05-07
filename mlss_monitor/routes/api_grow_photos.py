"""Serve photo files for a grow unit.

Endpoints:
  GET /api/grow/units/<id>/photo/latest             — most recent JPEG
  GET /api/grow/units/<id>/photos?range=…           — list photos in a range
  GET /api/grow/units/<id>/photos/<photo_id>        — fetch one JPEG by id

The list endpoint returns minimal metadata (``{id, taken_at, telemetry_id}``
per photo — no file paths, no image bytes) so a History-tab scrubber can
build a timeline cheaply and lazily fetch each JPEG on demand via the
by-id endpoint. The by-id endpoint cross-checks ``unit_id`` from the URL
against the photo row so unit A's viewer cannot guess unit B's photo IDs
to leak photos across units.

Range vocabulary (24h / 7d / 30d / 90d / all) matches GET
``/api/grow/units/<id>/history`` so the History tab uses one selector.
"""
import os
import sqlite3
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, send_from_directory, abort
from database.init_db import DB_FILE
from mlss_monitor.grow.api_helpers import RANGE_TO_HOURS
from mlss_monitor.grow.photo_storage import _resolve_images_dir

api_grow_photos_bp = Blueprint("api_grow_photos", __name__)


# Cache lifetimes for the two photo endpoints below. The split matters:
#
# `/photo/latest` returns DIFFERENT bytes on different requests (the row
# the query selects changes whenever a fresh snap-photo lands), so the
# response is NOT immutable. We allow a tiny 5s window (long enough for
# multiple page renders not to thrash, short enough that a freshly-
# captured photo shows up within ~5s without hitting the cache-bust
# hammer that the JS uses today).
#
# `/photos/<id>` returns the SAME bytes for the same id forever — the
# (unit_id, photo_id) tuple is monotonic and we never overwrite a
# committed JPEG. Aggressive 1-year cache + `immutable` directive lets
# the browser skip revalidation entirely on timelapse re-scrub. This is
# the fix for "timelapse reloads every photo every navigation": Flask's
# default `send_from_directory` doesn't set Cache-Control unless we ask,
# so the browser falls back to heuristic-freshness and re-validates
# constantly.
_LATEST_PHOTO_MAX_AGE_S = 5
_PHOTO_BY_ID_MAX_AGE_S = 31536000  # 1 year, the conventional "forever" max


def _make_immutable(response):
    """Add `immutable` to a Cache-Control header that already carries a
    `max-age=`. Flask's send_file/send_from_directory doesn't expose the
    `immutable` directive directly, so we append it to the value Flask
    set. RFC 8246 specifies the directive; Chrome / Firefox / Safari all
    honour it (skip revalidation for the cached response's lifetime).
    """
    cc = response.headers.get("Cache-Control", "")
    if "immutable" not in cc:
        response.headers["Cache-Control"] = (cc + ", immutable").lstrip(", ")
    return response


@api_grow_photos_bp.route("/api/grow/units/<int:unit_id>/photo/latest", methods=["GET"])
def latest_photo(unit_id):
    conn = sqlite3.connect(DB_FILE, timeout=5)
    try:
        row = conn.execute(
            "SELECT file_path FROM grow_photos WHERE unit_id=? "
            "ORDER BY taken_at DESC LIMIT 1", (unit_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        abort(404)
    file_path = row[0]
    # Resolve via _resolve_images_dir so the app_settings.grow_images_dir
    # override (admin UI) takes effect end-to-end. Same resolver used by
    # photo_by_id below — keep them consistent.
    abs_path = os.path.join(_resolve_images_dir(), file_path)
    if not os.path.exists(abs_path):
        abort(404)
    directory, filename = os.path.split(abs_path)
    return send_from_directory(
        directory, filename, mimetype="image/jpeg",
        max_age=_LATEST_PHOTO_MAX_AGE_S,
    )


@api_grow_photos_bp.route("/api/grow/units/<int:unit_id>/photos", methods=["GET"])
def list_photos(unit_id):
    """List photos for ``unit_id`` filtered by ``?range=…``.

    Returns ``[{id, taken_at, telemetry_id}, …]`` sorted by ``taken_at``
    ascending. A unit with no photos returns ``[]`` (200) — the timeline
    UI distinguishes "no data" from "no unit" via other endpoints.
    """
    range_str = request.args.get("range", "24h")
    if range_str not in RANGE_TO_HOURS:
        return jsonify({"error": "invalid_range"}), 400
    hours = RANGE_TO_HOURS[range_str]
    cutoff = (datetime.utcnow() - timedelta(hours=hours)) if hours is not None else None

    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        if cutoff is not None:
            rows = conn.execute(
                "SELECT id, taken_at, telemetry_id FROM grow_photos "
                "WHERE unit_id=? AND taken_at >= ? ORDER BY taken_at ASC",
                (unit_id, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, taken_at, telemetry_id FROM grow_photos "
                "WHERE unit_id=? ORDER BY taken_at ASC",
                (unit_id,),
            ).fetchall()
    finally:
        conn.close()
    return jsonify([
        {"id": r["id"], "taken_at": r["taken_at"], "telemetry_id": r["telemetry_id"]}
        for r in rows
    ])


@api_grow_photos_bp.route(
    "/api/grow/units/<int:unit_id>/photos/<int:photo_id>", methods=["GET"]
)
def photo_by_id(unit_id, photo_id):
    """Fetch a single photo's JPEG. Cross-checks ``unit_id`` for security."""
    conn = sqlite3.connect(DB_FILE, timeout=5)
    try:
        # The unit_id condition is security-critical — without it a unit-1
        # logged-in viewer could enumerate unit-2's photo IDs and leak them.
        row = conn.execute(
            "SELECT file_path FROM grow_photos WHERE id=? AND unit_id=?",
            (photo_id, unit_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        abort(404)
    abs_path = os.path.join(_resolve_images_dir(), row[0])
    if not os.path.exists(abs_path):
        abort(404)
    directory, filename = os.path.split(abs_path)
    response = send_from_directory(
        directory, filename, mimetype="image/jpeg",
        max_age=_PHOTO_BY_ID_MAX_AGE_S,
    )
    return _make_immutable(response)
