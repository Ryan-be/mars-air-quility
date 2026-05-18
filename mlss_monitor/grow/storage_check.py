"""Estimate disk usage of the grow_images directory.

Used by the fleet page to display a warning banner when disk is
approaching full. Computed lazily on each page render — no background
task. The shutil.disk_usage call is fast (a single statvfs syscall).
"""
import logging
import os
import shutil
import sqlite3
from typing import Optional, TypedDict

from database.init_db import DB_FILE
from mlss_monitor.grow.photo_storage import _resolve_images_dir

log = logging.getLogger(__name__)


class StorageStatus(TypedDict):
    images_dir: str
    used_bytes: int
    total_bytes: int
    used_pct: float
    threshold_pct: float
    is_warning: bool


def _get_warn_threshold_pct(conn) -> float:
    """Read grow_disk_warn_pct from app_settings (default 90)."""
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key='grow_disk_warn_pct'"
    ).fetchone()
    if row is None:
        return 90.0
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return 90.0


def get_storage_status() -> Optional[StorageStatus]:
    """Returns disk usage for the grow_images directory, or None on failure.

    Computes against the directory's mount point (so this reflects the
    actual SD/disk constraint, not just the dir size). Best-effort: a
    failure logs a warning and returns None — the caller renders nothing
    rather than crashing the page.
    """
    try:
        conn = sqlite3.connect(DB_FILE, timeout=2)
        try:
            threshold_pct = _get_warn_threshold_pct(conn)
        finally:
            conn.close()
        images_dir = _resolve_images_dir()
        if not os.path.isdir(images_dir):
            # Directory hasn't been created yet (no photos arrived);
            # use the parent that DOES exist so disk_usage can return.
            check_path = os.path.dirname(images_dir) or "/"
            while not os.path.exists(check_path) and check_path != "/":
                check_path = os.path.dirname(check_path)
            if not os.path.exists(check_path):
                return None
        else:
            check_path = images_dir
        total, used, _ = shutil.disk_usage(check_path)
        used_pct = (used / total) * 100 if total > 0 else 0
        return {
            "images_dir": images_dir,
            "used_bytes": used,
            "total_bytes": total,
            "used_pct": used_pct,
            "threshold_pct": threshold_pct,
            "is_warning": used_pct >= threshold_pct,
        }
    except Exception as exc:
        log.warning("storage_check failed: %s", exc)
        return None
