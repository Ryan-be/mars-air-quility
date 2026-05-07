"""Handle binary photo frames from grow units.

Frame layout:
  [4 bytes BE]  header_length
  [N bytes]     UTF-8 JSON header {taken_at, width, height, jpeg_quality, ...}
  [remaining]   raw JPEG bytes

On receipt: write the JPEG to MLSS_GROW_IMAGES_DIR/<unit_dir>/<date>/<HHMMSS>.jpg
(filesystem layout from the spec), insert a grow_photos row with the relative
path, and back-fill telemetry_id by joining to the closest grow_telemetry
row for the same unit within ±60 seconds. The denormalised join key makes
ML training queries cheap.
"""
import json
import logging
import os
import sqlite3
import struct
from datetime import datetime, timezone, timedelta
from pathlib import Path

from database.init_db import DB_FILE

log = logging.getLogger(__name__)

# Default to <project-root>/data/grow_images, computed from this file's
# location so it's stable regardless of where gunicorn was started from
# (e.g. systemd cwd, manual launch, etc.). Mirrors the data/sensor_data.db
# posture in database/init_db.py but as an absolute path so a gunicorn
# process started with cwd=/ doesn't resolve "data/grow_images" to "/data".
#
# Override via env (MLSS_GROW_IMAGES_DIR) or app_settings.grow_images_dir
# if you want photos elsewhere (e.g. an external SSD mounted at /mnt/photos).
#
# This module sits at <project-root>/mlss_monitor/grow/photo_storage.py,
# so parent.parent.parent is the project root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GROW_IMAGES_DIR = os.environ.get(
    "MLSS_GROW_IMAGES_DIR", str(_PROJECT_ROOT / "data" / "grow_images")
)

_JOIN_WINDOW_SECONDS = 60


def _resolve_images_dir() -> str:
    """app_settings override > env var > built-in default."""
    try:
        conn = sqlite3.connect(DB_FILE, timeout=2)
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='grow_images_dir'"
        ).fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return GROW_IMAGES_DIR


def handle_photo_frame(unit_id: int, frame: bytes) -> None:
    """Parse a binary photo frame and persist file + metadata.

    Caller (the WS listener in Task 4.5) is expected to have authenticated
    the unit via bearer token before invoking. The frame body itself is
    only structurally validated here (header length, JSON parseability,
    non-empty JPEG body); the JSON header fields (`taken_at`, `width`,
    `height`, ...) are trusted to have been validated upstream by pydantic.
    Missing required header fields surface as `KeyError`.

    Atomicity: INSERT is staged before the file write. If the file write
    fails, the row is rolled back. If the commit fails after the file
    write, the file is unlinked. This bounds the orphan-window to the
    (much rarer) sqlite-commit failure case.

    Same-second collisions: filename includes millisecond precision
    (`HHMMSS_mmm.jpg`) and the grow_photos table has
    `UNIQUE(unit_id, taken_at)`, so two photos at the same exact
    `taken_at` raise `sqlite3.IntegrityError` rather than silently
    corrupting.
    """
    log.info("handle_photo_frame: unit=%s frame_len=%d bytes",
             unit_id, len(frame))
    if len(frame) < 4:
        raise ValueError("photo frame too short for header length")
    (h_len,) = struct.unpack(">I", frame[:4])
    if h_len <= 0 or h_len > 65536:
        raise ValueError(f"invalid header length: {h_len}")
    header = json.loads(frame[4:4 + h_len].decode("utf-8"))
    jpeg_bytes = frame[4 + h_len:]
    if not jpeg_bytes:
        raise ValueError("photo frame has empty JPEG payload")

    taken_at = datetime.fromisoformat(header["taken_at"].replace("Z", "+00:00"))
    if taken_at.tzinfo:
        taken_at_utc = taken_at.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        taken_at_utc = taken_at

    images_dir = _resolve_images_dir()
    rel_dir = f"unit_{unit_id:03d}/{taken_at_utc.strftime('%Y-%m-%d')}"
    rel_path = f"{rel_dir}/{taken_at_utc.strftime('%H%M%S_%f')[:-3]}.jpg"
    abs_dir = os.path.join(images_dir, rel_dir)
    abs_path = os.path.join(images_dir, rel_path)

    # mkdir -p the unit/date subdir. The most common failure mode is the
    # service user not having write access to the chosen images_dir
    # (typically when MLSS_GROW_IMAGES_DIR is set to a system path like
    # /var/lib/mlss/grow_images that needs root). Catch + re-raise with
    # a clearer message so ops doesn't have to dig through journalctl.
    try:
        Path(abs_dir).mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        log.error(
            "Cannot create photo dir %s — service user lacks write access. "
            "Either chown the dir to the service user, or set "
            "MLSS_GROW_IMAGES_DIR / app_settings.grow_images_dir to a "
            "writable path (default 'data/grow_images' is project-relative "
            "and works without sudo).", abs_dir,
        )
        raise
    except FileNotFoundError as exc:
        log.error(
            "Cannot create photo dir %s — parent path doesn't exist and "
            "service user can't traverse to create it. Same fix as "
            "PermissionError above.", abs_dir,
        )
        raise

    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        # Find closest telemetry row within ±60s for the join key
        win = timedelta(seconds=_JOIN_WINDOW_SECONDS)
        join_row = conn.execute(
            "SELECT id FROM grow_telemetry WHERE unit_id=? "
            "AND timestamp_utc BETWEEN ? AND ? "
            "ORDER BY ABS(julianday(timestamp_utc) - julianday(?)) "
            "LIMIT 1",
            (unit_id, taken_at_utc - win, taken_at_utc + win, taken_at_utc),
        ).fetchone()
        telemetry_id = join_row[0] if join_row else None

        # Stage the INSERT before writing the file so a failed insert
        # (e.g. UNIQUE violation, OperationalError) rolls back cleanly
        # without leaving an orphan JPEG on disk.
        conn.execute(
            "INSERT INTO grow_photos "
            "(unit_id, taken_at, file_path, width_px, height_px, size_bytes, "
            " jpeg_quality, shutter_us, iso, white_balance, telemetry_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (unit_id, taken_at_utc, rel_path,
             header["width"], header["height"], len(jpeg_bytes),
             header.get("jpeg_quality"), header.get("shutter_us"),
             header.get("iso"), header.get("white_balance"), telemetry_id),
        )

        # Write the file. If this fails we rollback the staged row.
        with open(abs_path, "wb") as f:
            f.write(jpeg_bytes)

        # Commit only after both the row and the file are in place. If
        # the commit fails, the outer except will rollback and unlink.
        conn.commit()
    except Exception:
        conn.rollback()
        # File may have been written before the failure (file-write
        # error mid-write, or commit failure after a successful write).
        # Unlink so we don't leave an orphan JPEG on disk.
        try:
            if os.path.exists(abs_path):
                os.unlink(abs_path)
        except OSError:
            pass
        raise
    finally:
        conn.close()
