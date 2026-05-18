"""Phase 4 #8 — REST endpoints for time-lapse video generation.

Endpoints:
  POST /api/grow/units/<id>/timelapse        create a job. Body: {range, fps?}
  GET  /api/grow/units/<id>/timelapse        list jobs for the unit
  GET  /api/grow/timelapse/<job_id>          single job status
  GET  /api/grow/timelapse/<job_id>/video    serve the rendered MP4

The actual render runs in a background thread (see
mlss_monitor/grow/timelapse_jobs.py). POST returns 202 with the job id;
the client polls GET /timelapse/<id> for status changes.

RBAC:
  GET     viewer+
  POST    controller+
  Video   viewer+ (it's just a derived view of grow_photos which the
          viewer can already fetch one-by-one)

ffmpeg detection: POST returns 503 if ``ffmpeg`` isn't on PATH so the
operator gets a clear "install ffmpeg first" rather than a job that
silently fails 30s later.
"""
import logging
import os
import sqlite3
from datetime import datetime
from flask import Blueprint, jsonify, request, session, send_from_directory, abort

from database.init_db import DB_FILE
from mlss_monitor.grow.api_helpers import RANGE_TO_HOURS
from mlss_monitor.grow.timelapse_jobs import (
    _resolve_timelapses_dir,
    ffmpeg_available,
)
from mlss_monitor.rbac import require_role

log = logging.getLogger(__name__)

api_grow_timelapse_bp = Blueprint("api_grow_timelapse", __name__)

# Allow only specific FPS values to keep the encode cost predictable —
# 5/10/24 covers "slow drag" / "natural" / "smooth" without letting an
# operator fat-finger 240 and burn an hour of CPU.
_ALLOWED_FPS = (5, 10, 24)
_DEFAULT_FPS = 10

# Per-id MP4 cache: identical to /photos/<id>, the rendered MP4 never
# changes once written (every fresh render gets a new job_id and
# new file). 1-year immutable.
_VIDEO_MAX_AGE_S = 31536000


def _row_to_dict(row):
    return {
        "id": row["id"],
        "unit_id": row["unit_id"],
        "requested_by": row["requested_by"],
        "requested_at": row["requested_at"],
        "range": row["range"],
        "fps": row["fps"],
        "status": row["status"],
        "output_path": row["output_path"],
        "error_message": row["error_message"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "video_url": (
            f"/api/grow/timelapse/{row['id']}/video"
            if row["status"] == "complete" else None
        ),
    }


@api_grow_timelapse_bp.route(
    "/api/grow/units/<int:unit_id>/timelapse", methods=["POST"]
)
@require_role("controller", "admin")
def create_job(unit_id):
    if not ffmpeg_available():
        return jsonify({"error": "ffmpeg_not_installed"}), 503

    data = request.get_json(silent=True) or {}
    range_str = data.get("range", "24h")
    if range_str not in RANGE_TO_HOURS:
        return jsonify({"error": "invalid_range"}), 400

    fps = data.get("fps", _DEFAULT_FPS)
    try:
        fps = int(fps)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_fps"}), 400
    if fps not in _ALLOWED_FPS:
        return jsonify({
            "error": "invalid_fps",
            "allowed": list(_ALLOWED_FPS),
        }), 400

    requested_by = session.get("user") or "unknown"
    now = datetime.utcnow()

    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        unit_row = conn.execute(
            "SELECT id FROM grow_units WHERE id=? AND is_active=1",
            (unit_id,),
        ).fetchone()
        if unit_row is None:
            return jsonify({"error": "unit_not_found"}), 404

        cur = conn.execute(
            "INSERT INTO grow_timelapse_jobs "
            "(unit_id, requested_by, requested_at, range, fps, status) "
            "VALUES (?, ?, ?, ?, ?, 'queued')",
            (unit_id, requested_by, now, range_str, fps),
        )
        job_id = cur.lastrowid
        conn.commit()
        row = conn.execute(
            "SELECT * FROM grow_timelapse_jobs WHERE id=?", (job_id,),
        ).fetchone()
    finally:
        conn.close()
    return jsonify(_row_to_dict(row)), 202


@api_grow_timelapse_bp.route(
    "/api/grow/units/<int:unit_id>/timelapse", methods=["GET"]
)
@require_role("viewer", "controller", "admin")
def list_jobs_for_unit(unit_id):
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM grow_timelapse_jobs WHERE unit_id=? "
            "ORDER BY requested_at DESC LIMIT 50",
            (unit_id,),
        ).fetchall()
    finally:
        conn.close()
    return jsonify([_row_to_dict(r) for r in rows])


@api_grow_timelapse_bp.route(
    "/api/grow/timelapse/<int:job_id>", methods=["GET"]
)
@require_role("viewer", "controller", "admin")
def get_job(job_id):
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM grow_timelapse_jobs WHERE id=?", (job_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return jsonify({"error": "job_not_found"}), 404
    return jsonify(_row_to_dict(row))


@api_grow_timelapse_bp.route(
    "/api/grow/timelapse/<int:job_id>/video", methods=["GET"]
)
@require_role("viewer", "controller", "admin")
def get_video(job_id):
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT status, output_path FROM grow_timelapse_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        abort(404)
    if row["status"] != "complete" or not row["output_path"]:
        # 409 = "the resource exists but isn't in the right state" — better
        # than 404 because the operator's URL is correct, the job just
        # hasn't finished yet.
        return jsonify({
            "error": "not_ready",
            "status": row["status"],
        }), 409
    abs_path = os.path.join(_resolve_timelapses_dir(), row["output_path"])
    if not os.path.exists(abs_path):
        log.warning(
            "get_video: job %s claims complete but file missing at %s",
            job_id, abs_path,
        )
        abort(404)
    directory, filename = os.path.split(abs_path)
    response = send_from_directory(
        directory, filename, mimetype="video/mp4",
        max_age=_VIDEO_MAX_AGE_S,
    )
    cc = response.headers.get("Cache-Control", "")
    if "immutable" not in cc:
        response.headers["Cache-Control"] = (cc + ", immutable").lstrip(", ")
    return response
