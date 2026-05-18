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

Thumbnail cache (Phase 4):
  Fleet view + History timelapse don't need the full ~2MB camera capture —
  a 320px-wide JPEG is ~20-50KB and renders identically at fleet-card
  resolution. ``get_or_create_thumbnail`` lazily resizes the original JPEG
  via Pillow on first request and caches the result under
  ``data/grow_thumbnails/<unit>/<...>``. Subsequent requests return the
  cached file without re-encoding. Both the source and thumbnail trees are
  per-unit, so ``DELETE /photos`` can blow away the unit's thumbnail dir
  alongside the originals.
"""
import json
import logging
import os
import sqlite3
import struct
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

from PIL import Image

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
GROW_THUMBNAILS_DIR = os.environ.get(
    "MLSS_GROW_THUMBNAILS_DIR", str(_PROJECT_ROOT / "data" / "grow_thumbnails")
)

# Allowed thumbnail widths. One entry today (320 — fleet card width) but
# extensible: a future History-tab scrubber thumbnail could add 96 here
# without touching the route layer. Any width outside this set is a
# 400 from the route, not a silent resize, so callers can't generate
# arbitrary on-disk artefacts by spamming `?size=N`.
THUMB_WIDTHS = (320,)
THUMB_QUALITY = 75  # subjective sweet spot — ~30KB at 320×240

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


def _resolve_thumbnails_dir() -> str:
    """app_settings override > env var > built-in default. Mirrors
    ``_resolve_images_dir`` so an admin who relocates the photo tree can
    relocate the thumbnail cache in lockstep without code changes.
    """
    try:
        conn = sqlite3.connect(DB_FILE, timeout=2)
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='grow_thumbnails_dir'"
        ).fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return GROW_THUMBNAILS_DIR


def get_or_create_thumbnail(photo_relpath: str, width: int) -> str:
    """Return the absolute path to a cached thumbnail of ``photo_relpath``
    at ``width`` pixels wide.

    Cache layout mirrors the source tree: thumbnail of
    ``unit_001/2026-05-08/120000.jpg`` at width 320 lives at
    ``<thumbnails_dir>/unit_001/2026-05-08/120000_w320.jpg``. Per-width
    suffix means we can extend ``THUMB_WIDTHS`` later without a migration
    — the 320 cache and a hypothetical 96 cache co-exist for the same
    source image without colliding.

    First-request flow:
      1. Compute the cache path. If it already exists, return it
         (idempotent).
      2. Open the source JPEG, downscale to ``width`` preserving aspect
         ratio (``Image.thumbnail`` uses LANCZOS).
      3. Write the cached JPEG with quality 75 (the subjective sweet
         spot — ~30KB at 320×240 vs ~2MB for the source).

    Raises:
        ValueError: ``width`` not in ``THUMB_WIDTHS``.
        FileNotFoundError: source JPEG doesn't exist.

    Anything else (PIL.UnidentifiedImageError, OSError on the cache
    write) propagates — the caller decides whether to 500 or fall back
    to the original. Today the route falls back to the original on any
    Pillow exception so a corrupted source frame doesn't take down the
    fleet card; see ``api_grow_photos._serve_thumbnail_or_fallback``.
    """
    if width not in THUMB_WIDTHS:
        raise ValueError(
            f"unsupported thumbnail width {width}; allowed: {THUMB_WIDTHS}"
        )

    images_dir = _resolve_images_dir()
    thumbs_dir = _resolve_thumbnails_dir()
    src_abs = os.path.join(images_dir, photo_relpath)
    if not os.path.exists(src_abs):
        raise FileNotFoundError(src_abs)

    rel_dir, filename = os.path.split(photo_relpath)
    stem, _ext = os.path.splitext(filename)
    thumb_filename = f"{stem}_w{width}.jpg"
    thumb_rel = os.path.join(rel_dir, thumb_filename) if rel_dir else thumb_filename
    thumb_abs = os.path.join(thumbs_dir, thumb_rel)

    if os.path.exists(thumb_abs):
        return thumb_abs

    # Cache miss — generate. mkdir -p the cache subdir; same failure
    # modes as photo_storage's images dir (PermissionError / read-only).
    Path(os.path.dirname(thumb_abs)).mkdir(parents=True, exist_ok=True)
    with Image.open(src_abs) as img:
        # Image.thumbnail() preserves aspect ratio. We want width=320,
        # height auto. The (w, very-large-h) trick downscales to fit
        # within that box, which for landscape sources collapses to
        # height proportional to width.
        img.thumbnail((width, width * 10), Image.Resampling.LANCZOS)
        # Strip alpha / palette (some camera frames may include them);
        # JPEG cannot represent alpha. RGB is the safe target.
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(thumb_abs, "JPEG", quality=THUMB_QUALITY, optimize=True)

    return thumb_abs


def clear_thumbnail_cache_for_unit(unit_id: int) -> None:
    """Delete the thumbnail cache directory for one unit, if present.

    Called from ``DELETE /api/grow/units/<id>/photos`` so the cache
    doesn't drift out of sync with the source tree. Best-effort: a
    missing cache dir is fine (returns silently); a permission error
    is logged and swallowed (the originals are already gone, so a
    stale thumbnail is the lesser evil vs. a 500 on the wipe path).
    """
    thumbs_dir = _resolve_thumbnails_dir()
    unit_thumbs_dir = os.path.join(thumbs_dir, f"unit_{unit_id:03d}")
    try:
        shutil.rmtree(unit_thumbs_dir)
    except FileNotFoundError:
        # Cache never existed for this unit (no thumbnail ever requested,
        # or it was already cleaned up). Idempotent — fine.
        pass
    except OSError as exc:
        log.warning(
            "clear_thumbnail_cache_for_unit: rmtree(%s) failed: %s",
            unit_thumbs_dir, exc,
        )


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
    except PermissionError:
        log.error(
            "Cannot create photo dir %s — service user lacks write access. "
            "Either chown the dir to the service user, or set "
            "MLSS_GROW_IMAGES_DIR / app_settings.grow_images_dir to a "
            "writable path (default 'data/grow_images' is project-relative "
            "and works without sudo).", abs_dir,
        )
        raise
    except FileNotFoundError:
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
