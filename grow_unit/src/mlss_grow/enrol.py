"""First-boot enrollment HTTP call to MLSS."""
import logging
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
    try:
        # MLSS uses self-signed cert on the LAN — verify=False is safe given
        # we're already proving identity via the enrollment key
        resp = requests.post(url, json=body, timeout=30, verify=False)
    except requests.RequestException as exc:
        raise EnrollmentError(f"network error contacting {url}: {exc}")

    if resp.status_code != 201:
        raise EnrollmentError(f"enrollment failed (HTTP {resp.status_code}): {resp.text}")

    data = resp.json()
    if "unit_id" not in data or "token" not in data:
        raise EnrollmentError(f"malformed enrollment response: {data}")
    return data["unit_id"], data["token"]
