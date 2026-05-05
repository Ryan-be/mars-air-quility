"""Serve the grow firmware install script + wheel files.

The install script is a single bash file that runs on a fresh Pi Zero
to download both wheels (mlss_contracts + mlss_grow) and install them
into a venv at /opt/mlss-grow/.venv. See grow_unit/install.sh for the
canonical source (created in Task 9.2).
"""
import os
import re
from pathlib import Path
from flask import Blueprint, send_from_directory, jsonify, abort

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
