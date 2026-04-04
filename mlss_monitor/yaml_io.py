"""Atomic thread-safe YAML read/write helpers.

All YAML config writes in the insights-engine pipeline go through
``atomic_write`` so readers never observe a partial write.  A single
``RLock`` (``yaml_lock``) serialises all file I/O; contention is
negligible because config saves happen at human speed.
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

import yaml

yaml_lock = threading.RLock()   # one lock covers all YAML files (low contention)


def load_yaml(path: str | Path) -> dict:
    """Load a YAML file under the shared lock. Returns {} on missing file."""
    with yaml_lock:
        p = Path(path)
        if not p.exists():
            return {}
        with open(p) as f:
            return yaml.safe_load(f) or {}


def atomic_write(path: str | Path, data: dict) -> None:
    """Write data to path atomically under the shared lock.

    Writes to a sibling temp file then renames so readers never see a
    partial write. Safe on Linux (Pi OS); rename is atomic on POSIX.
    """
    p = Path(path)
    with yaml_lock:
        fd, tmp_path = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            os.replace(tmp_path, p)          # atomic on POSIX
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
