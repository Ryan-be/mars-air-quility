"""Fingerprint YAML loader for the attribution layer."""
from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# Required top-level keys every fingerprint must have.
_REQUIRED_KEYS = {"id", "label", "description", "sensors", "temporal",
                  "confidence_floor", "description_template", "action_template"}


@dataclasses.dataclass
class Fingerprint:
    id:                   str
    label:                str
    description:          str
    examples:             str
    sensors:              dict  # sensor_name -> state label
    temporal:             dict  # temporal profile keys -> values
    confidence_floor:     float
    description_template: str
    action_template:      str


def load_fingerprints(config_path) -> list:
    """Load and validate fingerprint definitions from YAML.

    Skips malformed entries (missing required keys) with a warning.
    Raises FileNotFoundError if config_path does not exist.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Fingerprint config not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    fingerprints = []
    for entry in raw.get("sources", []):
        missing = _REQUIRED_KEYS - set(entry.keys())
        if missing:
            log.warning(
                "Fingerprint loader: skipping entry missing keys %r: %s",
                missing,
                entry.get("id", "<no id>"),
            )
            continue
        fingerprints.append(
            Fingerprint(
                id=entry["id"],
                label=entry["label"],
                description=entry["description"],
                examples=entry.get("examples", ""),
                sensors=entry["sensors"],
                temporal=entry["temporal"],
                confidence_floor=float(entry["confidence_floor"]),
                description_template=entry["description_template"],
                action_template=entry["action_template"],
            )
        )
    return fingerprints
