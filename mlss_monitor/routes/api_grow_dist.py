"""Serve the grow firmware install script + wheel files.

The install script is a single bash file that runs on a fresh Pi Zero
to download both wheels (mlss_contracts + mlss_grow) and install them
into a venv at /opt/mlss-grow/.venv. See grow_unit/install.sh for the
canonical source (created in Task 9.2).
"""
import hashlib
import re
import sqlite3
from pathlib import Path
from flask import Blueprint, send_from_directory, jsonify, abort

from database.init_db import DB_FILE
from mlss_monitor.rbac import require_role

api_grow_dist_bp = Blueprint("api_grow_dist", __name__)

# Default location of the served wheels — overridable for tests
GROW_DIST_DIR = str(
    Path(__file__).resolve().parent.parent.parent / "static" / "grow_dist"
)
# Path-typed alias for tests that monkeypatch a Path object (and for code that
# wants pathlib niceties). Kept in sync with GROW_DIST_DIR for backward compat.
_WHEEL_DIR = Path(GROW_DIST_DIR)
_WHEEL_RE = re.compile(r"^([a-z_]+)-(\d+\.\d+\.\d+)-py3-none-any\.whl$")


def _wheel_dir() -> Path:
    """Resolve the active wheel directory (string or Path), favouring _WHEEL_DIR
    when tests monkeypatch it to a tmp_path."""
    return Path(_WHEEL_DIR)


def _wheel_sha256(filename: str) -> str:
    """Compute SHA256 of a wheel file in the active wheel directory."""
    h = hashlib.sha256()
    with open(_wheel_dir() / filename, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


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
    return send_from_directory(str(_wheel_dir()), filename, as_attachment=True)


def _latest_versions():
    """Walk the wheel dir and return {pkg: {version, filename, sha256}} so the
    Pi installer can verify integrity after download (defends against LAN MITM).
    """
    out: dict = {}
    wheel_dir = _wheel_dir()
    if not wheel_dir.is_dir():
        return jsonify(out)
    for p in sorted(wheel_dir.iterdir()):
        m = _WHEEL_RE.match(p.name)
        if not m:
            continue
        pkg, ver = m.group(1), m.group(2)
        # Keep the highest version when multiple wheels exist for one pkg.
        if pkg in out and out[pkg]["version"] >= ver:
            continue
        out[pkg] = {
            "version": ver,
            "filename": p.name,
            "sha256": _wheel_sha256(p.name),
        }
    return jsonify(out)


@api_grow_dist_bp.route("/api/grow/enrollment-key/peek-once", methods=["GET"])
@require_role("admin")
def peek_enrollment_key():
    """Return the raw enrollment key once. Deletes it from app_settings after.

    Used by the empty-state UI on first visit. After viewing, key is gone —
    rotation is a separate flow (Phase 2 Settings → Grow page).

    Admin-only: the key authorises POST /api/grow/enroll which is idempotent
    by hardware_serial — meaning anyone with the key can rotate the bearer
    token of an existing enrolled unit. Only admins should ever see it.
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
