"""On-disk buffer for photos taken while MLSS is unreachable.

When the WS is down the camera keeps capturing per the schedule. Photos
land here as JPEG files with sidecar JSON metadata; on reconnect they
flush oldest-first via WSClient.

Bounded by a hard byte cap (default 1 GB) and an age cap (default 7
days) — same defence-in-depth pattern as buffer.py for telemetry. When
the byte cap is hit, oldest photos are evicted FIFO and an eviction
event is emitted (mirrors the buffer.py callback contract).
"""
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

_DEFAULT_DIR = "/var/lib/mlss-grow/photos"
_DEFAULT_MAX_BYTES = 1024 * 1024 * 1024  # 1 GB
_DEFAULT_RETENTION_DAYS = 7


@dataclass
class BufferedPhoto:
    """A photo on disk awaiting upload."""
    jpeg_path: Path
    metadata_path: Path
    metadata: dict     # parsed JSON sidecar
    size_bytes: int    # JPEG size (excludes metadata sidecar)
    mtime: float       # epoch seconds — used for age-based prune


class PhotoBuffer:
    """Disk-backed photo queue with FIFO eviction + age-based prune.

    Mirrors the LocalBuffer (telemetry) protocol:
      - append() writes the photo atomically (tmp + rename) so peek_all
        never sees half-written files.
      - peek_all() returns photos in chronological (filename) order — the
        same oldest-first contract telemetry uses.
      - delete() is per-photo + idempotent so a partial replay leaves
        un-sent photos in place.
      - size caps trigger an on_eviction callback the WSClient hooks into
        to emit grow_errors events (same shape as LocalBuffer).
    """

    def __init__(self, root_dir: str = _DEFAULT_DIR, *,
                 max_bytes: int = _DEFAULT_MAX_BYTES,
                 retention_days: int = _DEFAULT_RETENTION_DAYS,
                 on_eviction: Optional[Callable[..., None]] = None) -> None:
        """Open (or create) the photo buffer at root_dir.

        max_bytes: hard byte cap. When exceeded, oldest JPEGs are evicted
        FIFO until the buffer is back under the cap.

        retention_days: age cap for prune(). Photos older than this are
        deleted. Defaults to 7 days (matches LocalBuffer).

        on_eviction: optional callback fired when the byte cap evicts.
        Called as on_eviction(reason="byte_cap", evicted_count=N).
        Exceptions raised by the callback are caught and swallowed —
        a buggy callback must not break the buffer.
        """
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes
        self._retention_days = retention_days
        self._on_eviction = on_eviction
        # Monotonic counter to disambiguate same-millisecond appends
        # (rare, but observable on fast Windows test hosts where two
        # consecutive appends can resolve to the same time.time() ms).
        # Reset to 0 each process start, but the millisecond timestamp
        # is the primary ordering key so the counter only matters for
        # within-ms tiebreaking.
        self._monotonic_counter = 0

    def append(self, metadata: dict, jpeg_bytes: bytes) -> Path:
        """Write the photo to disk. Returns the JPEG path.

        Filename uses a 13-digit millisecond timestamp so lexicographic
        ordering matches chronological ordering — peek_all sorts by name
        and gets oldest-first for free. 13 digits covers the epoch
        through year 2286.

        Atomic write: the JPEG and metadata sidecar are each written to
        a .tmp file then renamed. If the process dies mid-write, peek_all
        only sees fully-written files (the orphaned .tmp is ignored
        because peek_all globs for *.jpg, not *.jpg.tmp).
        """
        ts_ms = int(time.time() * 1000)
        # Append a 4-digit monotonic counter to disambiguate same-ms
        # appends. The ms timestamp is still the primary ordering key
        # (lexicographic = chronological); the counter only matters when
        # two photos arrive in the same millisecond (rare in production
        # at ~30-min cadence but observable on fast test hosts).
        self._monotonic_counter = (self._monotonic_counter + 1) % 10000
        stem = f"{ts_ms:013d}_{self._monotonic_counter:04d}"
        jpeg_path = self._root / f"{stem}.jpg"
        metadata_path = self._root / f"{stem}.json"

        tmp_jpeg = jpeg_path.with_suffix(".jpg.tmp")
        tmp_meta = metadata_path.with_suffix(".json.tmp")
        with open(tmp_jpeg, "wb") as f:
            f.write(jpeg_bytes)
        with open(tmp_meta, "w") as f:
            json.dump(metadata, f)
        os.replace(tmp_jpeg, jpeg_path)
        os.replace(tmp_meta, metadata_path)

        self._evict_if_over_cap()
        return jpeg_path

    def peek_all(self) -> list[BufferedPhoto]:
        """Return all buffered photos in upload order (oldest first).

        Sorted by filename — since filenames start with a millisecond
        timestamp, name order = chronological order. Orphaned JPEGs (no
        sidecar) and corrupt metadata files are skipped with a warning
        rather than crashing the replay loop.
        """
        out = []
        for jpeg in sorted(self._root.glob("*.jpg")):
            metadata_path = jpeg.with_suffix(".json")
            if not metadata_path.exists():
                # Orphaned JPEG (sidecar missing): skip but log
                log.warning(
                    "orphan JPEG %s (no metadata sidecar); skipping",
                    jpeg,
                )
                continue
            try:
                with open(metadata_path) as f:
                    metadata = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                log.warning(
                    "failed to read metadata for %s: %s; skipping",
                    jpeg, exc,
                )
                continue
            try:
                stat = jpeg.stat()
            except OSError as exc:
                log.warning(
                    "failed to stat %s: %s; skipping", jpeg, exc,
                )
                continue
            out.append(BufferedPhoto(
                jpeg_path=jpeg, metadata_path=metadata_path,
                metadata=metadata, size_bytes=stat.st_size,
                mtime=stat.st_mtime,
            ))
        return out

    def delete(self, photo: BufferedPhoto) -> None:
        """Delete one buffered photo + its sidecar. Idempotent.

        Per-photo delete (not bulk) is the durability guarantee — the
        replay loop deletes only after a successful send, so a mid-replay
        disconnect leaves un-sent photos in place for the next reconnect.
        Same protocol as LocalBuffer.delete.
        """
        for path in (photo.jpeg_path, photo.metadata_path):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("failed to delete %s: %s", path, exc)

    def size(self) -> int:
        """Return the count of currently-buffered photos (JPEGs only)."""
        return len(list(self._root.glob("*.jpg")))

    def total_bytes(self) -> int:
        """Return the on-disk byte total (JPEG only, sidecars are small)."""
        total = 0
        for p in self._root.glob("*.jpg"):
            try:
                total += p.stat().st_size
            except OSError:
                # Race: file was deleted between glob and stat. Skip.
                continue
        return total

    def summary(self) -> dict:
        """Return a structured summary of buffered photos.

        Mirrors LocalBuffer.summary() so the diagnostics-panel renderer
        can treat both buffers uniformly. The shape intentionally OMITS
        ``kinds`` (photos are all the same kind — there's no msg_type
        equivalent), so the JS render branches on its presence to
        decide whether to draw the per-kind list.

        Shape::

            {
                "size": 12,
                "total_bytes": 4_800_000,
                "oldest_ts": "2026-05-07T03:00:00Z",  # from metadata
                "newest_ts": "2026-05-07T05:30:00Z",
            }

        Empty buffer → all zero/None.
        """
        photos = self.peek_all()
        if not photos:
            return {
                "size": 0,
                "total_bytes": 0,
                "oldest_ts": None,
                "newest_ts": None,
            }
        return {
            "size": len(photos),
            "total_bytes": sum(p.size_bytes for p in photos),
            # Camera writes "taken_at" into the metadata sidecar (see
            # SafetyLoop.tick where it's assigned just before the photo
            # is emitted). Falls back to None if absent — the renderer
            # already handles the dash case.
            "oldest_ts": photos[0].metadata.get("taken_at"),
            "newest_ts": photos[-1].metadata.get("taken_at"),
        }

    def prune(self, retention_days: Optional[int] = None) -> int:
        """Delete photos older than retention_days. Returns count deleted.

        Uses the configured retention_days when called without an arg.
        Mirrors LocalBuffer.prune's role: runs on every successful
        reconnect so a long outage doesn't accumulate forever.
        """
        if retention_days is None:
            retention_days = self._retention_days
        cutoff = time.time() - retention_days * 86400
        deleted = 0
        for photo in self.peek_all():
            if photo.mtime < cutoff:
                self.delete(photo)
                deleted += 1
        if deleted > 0:
            log.info(
                "pruned %d photos older than %d days",
                deleted, retention_days,
            )
        return deleted

    def _evict_if_over_cap(self) -> None:
        """FIFO drop oldest photos when the byte cap is exceeded.

        Same rationale as LocalBuffer._evict_if_over_cap: newer photos
        have more diagnostic value than week-old ones already past the
        retention cliff. Called from append() after each write.
        """
        photos = self.peek_all()
        total = sum(p.size_bytes for p in photos)
        if total <= self._max_bytes:
            return
        evicted = 0
        for photo in photos:  # oldest first (peek_all returns sorted)
            if total <= self._max_bytes:
                break
            self.delete(photo)
            total -= photo.size_bytes
            evicted += 1
        if evicted > 0:
            log.warning(
                "photo buffer evicted %d oldest photos (over byte cap %d)",
                evicted, self._max_bytes,
            )
            if self._on_eviction is not None:
                try:
                    self._on_eviction(
                        reason="byte_cap", evicted_count=evicted,
                    )
                except Exception as exc:  # noqa: BLE001
                    # Don't let a buggy callback break the buffer.
                    log.warning(
                        "photo buffer on_eviction callback failed: %s", exc,
                    )
