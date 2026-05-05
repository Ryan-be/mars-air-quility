"""Loaders for first-boot config and persisted bearer token."""
import json
import os
from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass
class FirstbootConfig:
    mlss_host: str
    enrollment_key: str
    plant_name: str
    plant_type: str = "generic"
    medium: str = "soil"
    wifi_ssid: str | None = None
    wifi_psk: str | None = None


def load_firstboot_config(path: str) -> "FirstbootConfig | None":
    """Read /boot/mlss-grow.yaml. Returns None if file missing (already enrolled)."""
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    plant = data.get("plant") or {}
    wifi = data.get("wifi") or {}
    return FirstbootConfig(
        mlss_host=data["mlss_host"],
        enrollment_key=data["enrollment_key"],
        plant_name=plant["name"],
        plant_type=plant.get("type", "generic"),
        medium=plant.get("medium", "soil"),
        wifi_ssid=wifi.get("ssid"),
        wifi_psk=wifi.get("psk"),
    )


def save_token(path: str, unit_id: int, token: str) -> None:
    """Persist the per-unit bearer token + unit_id at the given path with mode 0600."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({"unit_id": unit_id, "token": token}, f)
    if os.name == "posix":
        os.chmod(path, 0o600)


def load_token(path: str) -> "tuple[int, str] | None":
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        data = json.load(f)
    return (data["unit_id"], data["token"])
