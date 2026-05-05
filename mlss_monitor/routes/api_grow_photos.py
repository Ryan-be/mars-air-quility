"""Serve photo files for a grow unit."""
import os
import sqlite3
from flask import Blueprint, send_from_directory, abort
from database.init_db import DB_FILE

api_grow_photos_bp = Blueprint("api_grow_photos", __name__)
GROW_IMAGES_DIR = os.environ.get("MLSS_GROW_IMAGES_DIR", "/var/lib/mlss/grow_images")


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
    abs_path = os.path.join(GROW_IMAGES_DIR, file_path)
    if not os.path.exists(abs_path):
        abort(404)
    directory, filename = os.path.split(abs_path)
    return send_from_directory(directory, filename, mimetype="image/jpeg")
