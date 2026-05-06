"""First-boot enrollment HTTP call to MLSS."""
import logging
import os
import requests
from mlss_grow.config import FirstbootConfig

log = logging.getLogger(__name__)

_CPUINFO_PATH = "/proc/cpuinfo"


class EnrollmentError(Exception):
    pass


def get_hardware_serial() -> str:
    """Extract Pi hardware serial from /proc/cpuinfo. Returns empty string if missing."""
    try:
        with open(_CPUINFO_PATH, "r") as f:
            for line in f:
                if line.startswith("Serial"):
                    return line.split(":", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""


def enroll_unit(cfg: FirstbootConfig, hardware_serial: str) -> tuple[int, str]:
    """POST /api/grow/enroll. Returns (unit_id, token). Raises on failure."""
    url = f"https://{cfg.mlss_host}:5000/api/grow/enroll"
    body = {
        "enrollment_key": cfg.enrollment_key,
        "hardware_serial": hardware_serial,
        "plant": {
            "name": cfg.plant_name,
            "type": cfg.plant_type,
            "medium": cfg.medium,
        },
    }
    # Pinned-cert verification (C3 fix). The previous `verify=False` reasoning
    # was reversed: the enrollment_key travels in the request body, so an MITM
    # on the LAN could sniff it and enrol a malicious unit. install.sh fetches
    # the MLSS server cert at install time (TOFU under the documented LAN
    # trust model) and writes it to cfg.server_cert_path. When that file
    # exists, verify against it; otherwise (dev/test, or pre-install) fall
    # back to verify=False AND log a prominent WARNING so the insecure
    # posture shows up in operator logs.
    cert_path = getattr(cfg, "server_cert_path", None)
    if cert_path and os.path.isfile(cert_path):
        verify: "bool | str" = cert_path
    else:
        log.warning(
            "MLSS server cert not found at %s — falling back to verify=False. "
            "This is INSECURE: the enrollment_key in the POST body is "
            "vulnerable to LAN MITM sniffing. Run install.sh on a Pi to pin "
            "the cert, or override server_cert_path in /boot/mlss-grow.yaml.",
            cert_path,
        )
        verify = False
    try:
        resp = requests.post(url, json=body, timeout=30, verify=verify)
    except requests.RequestException as exc:
        raise EnrollmentError(f"network error contacting {url}: {exc}")

    if resp.status_code != 201:
        raise EnrollmentError(f"enrollment failed (HTTP {resp.status_code}): {resp.text}")

    data = resp.json()
    if "unit_id" not in data or "token" not in data:
        raise EnrollmentError(f"malformed enrollment response: {data}")
    return data["unit_id"], data["token"]
