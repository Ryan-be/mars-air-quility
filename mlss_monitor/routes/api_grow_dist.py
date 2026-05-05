"""Serve the grow firmware install script + wheel files.

The install script is a single bash file that runs on a fresh Pi Zero
to download both wheels (mlss_contracts + mlss_grow) and install them
into a venv at /opt/mlss-grow/.venv. See grow_unit/install.sh for the
canonical source (created in Task 9.2).
"""
import os
import re
import sqlite3
from pathlib import Path
from flask import Blueprint, send_from_directory, jsonify, abort

from database.init_db import DB_FILE

api_grow_dist_bp = Blueprint("api_grow_dist", __name__)

# Default location of the served wheels — overridable for tests
GROW_DIST_DIR = str(
    Path(__file__).resolve().parent.parent.parent / "static" / "grow_dist"
)
_WHEEL_RE = re.compile(r"^([a-z_]+)-(\d+\.\d+\.\d+)-py3-none-any\.whl$")


@api_grow_dist_bp.route("/api/grow/install.sh", methods=["GET"])
def install_sh():
    """The Pi Zero install one-liner downloads + executes this."""
    install_path = (
        Path(__file__).resolve().parent.parent.parent
        / "grow_unit" / "install.sh"
    )
    if not install_path.exists():
        placeholder = (
            b"#!/bin/bash\n"
            b"# mlss-grow installer placeholder.\n"
            b"# Real install.sh not yet built. Run scripts/build_grow_wheel.sh\n"
            b"# on the MLSS server (Task 9.2 creates the real installer).\n"
            b"echo 'mlss-grow installer not yet available' >&2\n"
            b"exit 1\n"
        )
        return (placeholder, 200, {"Content-Type": "text/x-shellscript"})
    with open(install_path, "rb") as f:
        return (f.read(), 200, {"Content-Type": "text/x-shellscript"})


@api_grow_dist_bp.route("/api/grow/dist/<path:filename>", methods=["GET"])
def serve_wheel(filename):
    # Reject path traversal / weird names — only basenames allowed
    if "/" in filename or "\\" in filename or ".." in filename:
        abort(400)
    if filename == "latest":
        return _latest_versions()
    # send_from_directory returns 404 for missing files
    return send_from_directory(GROW_DIST_DIR, filename, as_attachment=True)


def _latest_versions():
    out = {}
    if not os.path.isdir(GROW_DIST_DIR):
        return jsonify(out)
    for fname in os.listdir(GROW_DIST_DIR):
        m = _WHEEL_RE.match(fname)
        if m:
            pkg, ver = m.group(1), m.group(2)
            if pkg not in out or ver > out[pkg]:
                out[pkg] = ver
    return jsonify(out)


@api_grow_dist_bp.route("/api/grow/enrollment-key/peek-once", methods=["GET"])
def peek_enrollment_key():
    """Return the raw enrollment key once. Deletes it from app_settings after.

    Used by the empty-state UI on first visit. After viewing, key is gone —
    rotation is a separate flow (Phase 2 Settings → Grow page).
    """
    conn = sqlite3.connect(DB_FILE, timeout=5)
    try:
        row = conn.execute(
            "SELECT value FROM app_settings "
            "WHERE key='grow_enrollment_key_raw_pending_reveal'",
        ).fetchone()
        if row is None or not row[0]:
            return jsonify({"error": "already_revealed"}), 410
        raw_key = row[0]
        conn.execute(
            "DELETE FROM app_settings "
            "WHERE key='grow_enrollment_key_raw_pending_reveal'",
        )
        conn.commit()
        return jsonify({"key": raw_key})
    finally:
        conn.close()
