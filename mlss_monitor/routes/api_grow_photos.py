"""Serve photo files for a grow unit.

Endpoints:
  GET /api/grow/units/<id>/photo/latest             — most recent JPEG
  GET /api/grow/units/<id>/photo/latest?size=thumb  — most recent, 320px wide
  GET /api/grow/units/<id>/photos?range=…           — list photos in a range
  GET /api/grow/units/<id>/photos/<photo_id>        — fetch one JPEG by id
  GET /api/grow/units/<id>/photos/<photo_id>?size=thumb — fetch one, 320px wide

The list endpoint returns minimal metadata (``{id, taken_at, telemetry_id}``
per photo — no file paths, no image bytes) so a History-tab scrubber can
build a timeline cheaply and lazily fetch each JPEG on demand via the
by-id endpoint. The by-id endpoint cross-checks ``unit_id`` from the URL
against the photo row so unit A's viewer cannot guess unit B's photo IDs
to leak photos across units.

Range vocabulary (24h / 7d / 30d / 90d / all) matches GET
``/api/grow/units/<id>/history`` so the History tab uses one selector.

Thumbnail variant (``?size=thumb``):
  Lazily resizes via ``photo_storage.get_or_create_thumbnail`` and serves
  the cached file. First request takes a few hundred ms (Pillow encode);
  subsequent requests serve from disk. Cache lives under
  ``data/grow_thumbnails/<unit>/...`` and is wiped alongside the
  originals by ``DELETE /photos``.
"""
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, send_from_directory, abort
from database.init_db import DB_FILE
from mlss_monitor.grow.api_helpers import RANGE_TO_HOURS
from mlss_monitor.grow.photo_storage import (
    _resolve_images_dir,
    get_or_create_thumbnail,
    THUMB_WIDTHS,
)

log = logging.getLogger(__name__)

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

# Map ``?size=`` query param values to a thumbnail width in pixels. Only
# "thumb" is a valid token today; routes return 400 on any other value.
# An unset ``?size`` means "original full-resolution JPEG" — the
# pre-Phase-4 default.
_SIZE_TOKEN_WIDTHS = {"thumb": THUMB_WIDTHS[0]}


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


def _resolved_size_width():
    """Read ``?size=`` from the current request and resolve it to a
    pixel width. Returns ``None`` when the param is absent (caller serves
    the original). Raises ``werkzeug.HTTPException`` (via ``abort``) on
    an unknown token so unknown values become a clean 400 rather than
    silently falling through to original.
    """
    size = request.args.get("size")
    if size is None:
        return None
    width = _SIZE_TOKEN_WIDTHS.get(size)
    if width is None:
        abort(400, description=(
            f"unknown size token {size!r}; "
            f"allowed: {sorted(_SIZE_TOKEN_WIDTHS)}"
        ))
    return width


def _serve_thumbnail_or_fallback(photo_relpath, width, *, max_age, immutable):
    """Generate (or reuse cached) thumbnail and serve it; on any Pillow
    failure, fall back to the original so a corrupted source frame
    doesn't take down the fleet card.

    Returns a Flask response with the same caching semantics the route
    would have applied to the original — ``max_age`` is forwarded to
    ``send_from_directory``; ``immutable`` triggers ``_make_immutable``
    after.
    """
    try:
        thumb_abs = get_or_create_thumbnail(photo_relpath, width)
    except FileNotFoundError:
        # Source disappeared between the DB query and the resize. Same
        # 404 the original-path branch would have produced.
        abort(404)
    except (OSError, ValueError) as exc:
        # Pillow couldn't decode (corrupted JPEG?), or filesystem write
        # failed. Log + fall back to the original so the fleet card
        # still renders.
        log.warning(
            "thumbnail generation failed for %s w=%d: %s — "
            "falling back to original",
            photo_relpath, width, exc,
        )
        return None  # caller falls through to original-serving path
    directory, filename = os.path.split(thumb_abs)
    response = send_from_directory(
        directory, filename, mimetype="image/jpeg", max_age=max_age,
    )
    if immutable:
        response = _make_immutable(response)
    return response


@api_grow_photos_bp.route("/api/grow/units/<int:unit_id>/photo/latest", methods=["GET"])
def latest_photo(unit_id):
    width = _resolved_size_width()  # None when ?size absent; 400 on bad token
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

    if width is not None:
        thumb_response = _serve_thumbnail_or_fallback(
            file_path, width,
            max_age=_LATEST_PHOTO_MAX_AGE_S, immutable=False,
        )
        if thumb_response is not None:
            return thumb_response
        # Pillow failure path — fall through to serving the original.

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
    width = _resolved_size_width()  # None when ?size absent; 400 on bad token
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
    file_path = row[0]
    abs_path = os.path.join(_resolve_images_dir(), file_path)
    if not os.path.exists(abs_path):
        abort(404)

    if width is not None:
        thumb_response = _serve_thumbnail_or_fallback(
            file_path, width,
            max_age=_PHOTO_BY_ID_MAX_AGE_S, immutable=True,
        )
        if thumb_response is not None:
            return thumb_response
        # Pillow failure path — fall through to serving the original.

    directory, filename = os.path.split(abs_path)
    response = send_from_directory(
        directory, filename, mimetype="image/jpeg",
        max_age=_PHOTO_BY_ID_MAX_AGE_S,
    )
    return _make_immutable(response)
