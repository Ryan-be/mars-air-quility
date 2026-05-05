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
import os
import sqlite3
import struct
from datetime import datetime, timezone, timedelta
from pathlib import Path

from database.init_db import DB_FILE

GROW_IMAGES_DIR = os.environ.get(
    "MLSS_GROW_IMAGES_DIR", "/var/lib/mlss/grow_images"
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

    Known limitations (tracked for post-Phase-1):
    - Two photos with the same second-precision `taken_at` overwrite the
      file silently; only one DB row's `file_path` will then point to
      correct bytes. Camera cadence makes this rare but not impossible.
    - File is written before the DB row is inserted; if the INSERT raises,
      the JPEG remains on disk with no DB reference.
    """
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
    rel_path = f"{rel_dir}/{taken_at_utc.strftime('%H%M%S')}.jpg"
    abs_dir = os.path.join(images_dir, rel_dir)
    abs_path = os.path.join(images_dir, rel_path)

    Path(abs_dir).mkdir(parents=True, exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(jpeg_bytes)

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
        conn.commit()
    finally:
        conn.close()
