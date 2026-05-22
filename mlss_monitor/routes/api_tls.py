"""Admin TLS endpoints — CA download, iOS profile generator, status."""

import hashlib
import logging
import os
from pathlib import Path

from flask import Blueprint, Response, jsonify, send_file

from mlss_monitor.notifications import tls_profile
from mlss_monitor.rbac import require_role

log = logging.getLogger(__name__)

api_tls_bp = Blueprint("api_tls", __name__)


def _uuid_from_ca(ca_pem: str) -> str:
    """Stable UUID derived from CA content — reinstalling same profile updates."""
    h = hashlib.sha256(ca_pem.encode("utf-8")).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


@api_tls_bp.route("/api/admin/tls/status", methods=["GET"])
@require_role("admin")
def status():
    ca_path = tls_profile._CA_PATH      # pylint: disable=protected-access
    cert_path = tls_profile._CERT_PATH  # pylint: disable=protected-access
    return jsonify({
        "ca_exists":      os.path.isfile(ca_path),
        "cert_exists":    os.path.isfile(cert_path),
        "cert_not_after": tls_profile.cert_not_after(),
    })


@api_tls_bp.route("/api/admin/tls/ca.crt", methods=["GET"])
@require_role("admin")
def ca_download():
    ca_path = tls_profile._CA_PATH  # pylint: disable=protected-access
    if not os.path.isfile(ca_path):
        return jsonify({
            "error": "CA certificate not found. Run scripts/generate_local_ca.sh "
                     "on the hub to generate one."
        }), 404
    return send_file(
        Path(ca_path).resolve(),
        mimetype="application/x-x509-ca-cert",
        as_attachment=True,
        download_name="mlss-root-ca.crt",
    )


@api_tls_bp.route("/api/admin/tls/ios-profile.mobileconfig", methods=["GET"])
@require_role("admin")
def ios_profile():
    try:
        ca_pem = tls_profile.read_ca_pem()
    except FileNotFoundError:
        return jsonify({
            "error": "CA certificate not found. Run scripts/generate_local_ca.sh "
                     "on the hub to generate one."
        }), 404
    payload_uuid = _uuid_from_ca(ca_pem)
    blob = tls_profile.build_mobileconfig(
        ca_pem,
        payload_uuid=payload_uuid,
        payload_org="MLSS",
        payload_name="MLSS Root CA",
    )
    return Response(
        blob,
        mimetype="application/x-apple-aspen-config",
        headers={
            "Content-Disposition":
                'attachment; filename="mlss-mobile.mobileconfig"',
        },
    )
