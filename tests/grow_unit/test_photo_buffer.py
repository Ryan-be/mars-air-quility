"""PhotoBuffer: append photos to disk, peek oldest-first, delete after upload,
prune by age, evict oldest when over byte cap.

Mirrors test_buffer.py for the telemetry buffer. The shared protocol with
LocalBuffer (peek_all + delete-per-success, FIFO eviction, on_eviction
callback) is the durability contract WSClient relies on.
"""
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from mlss_grow.photo_buffer import PhotoBuffer, BufferedPhoto


# ---------------------------------------------------------------------------
# append: writes JPEG + sidecar atomically
# ---------------------------------------------------------------------------


def test_append_writes_jpeg_and_metadata_sidecar(tmp_path):
    """append() must produce both a .jpg and a .json sidecar with the
    metadata payload preserved verbatim."""
    buf = PhotoBuffer(root_dir=str(tmp_path))
    metadata = {"taken_at": "2026-05-06T12:00:00Z", "exposure_ms": 30}
    jpeg = b"\xff\xd8\xff\xe0fake-jpeg-bytes"

    jpeg_path = buf.append(metadata, jpeg)

    assert jpeg_path.exists(), "JPEG file must be on disk after append"
    sidecar_path = jpeg_path.with_suffix(".json")
    assert sidecar_path.exists(), "metadata sidecar must be on disk too"
    # JPEG bytes preserved
    assert jpeg_path.read_bytes() == jpeg
    # Metadata preserved
    with open(sidecar_path) as f:
        assert json.load(f) == metadata


def test_append_atomic_via_tmp_rename(tmp_path):
    """Both writes (JPEG + sidecar) must go through tmp + os.replace so a
    crash mid-write never leaves a half-written .jpg/.json visible to
    peek_all (which globs *.jpg, not *.jpg.tmp)."""
    buf = PhotoBuffer(root_dir=str(tmp_path))
    with patch("mlss_grow.photo_buffer.os.replace") as mock_replace:
        buf.append({"k": "v"}, b"jpeg-bytes")
    # Two replaces: one for .jpg, one for .json
    assert mock_replace.call_count == 2
    suffixes = sorted(
        Path(str(call.args[0])).suffix for call in mock_replace.call_args_list
    )
    # tmp paths end in `.jpg.tmp` / `.json.tmp` -> Path.suffix returns `.tmp`
    assert suffixes == [".tmp", ".tmp"]


# ---------------------------------------------------------------------------
# peek_all: oldest-first ordering, robustness to corruption
# ---------------------------------------------------------------------------


def test_peek_all_returns_oldest_first(tmp_path):
    """peek_all sorts by filename — and filenames start with millisecond
    timestamps — so iteration order is chronological. Replay must
    upload oldest-first to keep server-side photo order coherent."""
    buf = PhotoBuffer(root_dir=str(tmp_path))
    paths = []
    for i in range(3):
        paths.append(buf.append({"i": i}, b"jpeg" + bytes([i])))
        # Sleep enough that millisecond timestamps differ even on fast
        # systems. 5ms keeps the test fast but unambiguous.
        time.sleep(0.005)

    photos = buf.peek_all()
    assert len(photos) == 3
    # The metadata "i" field should be 0,1,2 in that order — proving
    # chronological iteration.
    assert [p.metadata["i"] for p in photos] == [0, 1, 2]


def test_peek_all_skips_orphan_jpeg(tmp_path, caplog):
    """A JPEG without its sidecar (e.g. partial cleanup, sidecar deleted
    out of band) must be skipped — not crash the replay loop. We log a
    warning so an operator can investigate."""
    # Write an orphaned .jpg directly (no sidecar)
    orphan = tmp_path / "1234567890123.jpg"
    orphan.write_bytes(b"jpeg")

    buf = PhotoBuffer(root_dir=str(tmp_path))
    with caplog.at_level("WARNING"):
        photos = buf.peek_all()

    assert photos == []
    assert any("orphan" in rec.message.lower() for rec in caplog.records)


def test_peek_all_skips_corrupt_metadata(tmp_path, caplog):
    """A JPEG with a malformed JSON sidecar must be skipped + logged
    rather than raising. Same robustness rationale as the orphan case —
    one bad photo shouldn't abort the entire replay batch."""
    stem = "1234567890124"
    (tmp_path / f"{stem}.jpg").write_bytes(b"jpeg")
    # Invalid JSON — peek_all's json.load should raise + we should skip
    (tmp_path / f"{stem}.json").write_text("not-json {")

    buf = PhotoBuffer(root_dir=str(tmp_path))
    with caplog.at_level("WARNING"):
        photos = buf.peek_all()

    assert photos == []
    assert any("metadata" in rec.message.lower() for rec in caplog.records)


# ---------------------------------------------------------------------------
# delete: per-photo + idempotent
# ---------------------------------------------------------------------------


def test_delete_removes_jpeg_and_sidecar(tmp_path):
    """delete() must remove both the JPEG and its sidecar — leaving an
    orphan would silently consume disk forever."""
    buf = PhotoBuffer(root_dir=str(tmp_path))
    buf.append({"i": 1}, b"jpeg")
    photo = buf.peek_all()[0]

    buf.delete(photo)

    assert not photo.jpeg_path.exists()
    assert not photo.metadata_path.exists()
    assert buf.size() == 0


def test_delete_idempotent_on_missing_files(tmp_path):
    """delete() must NOT raise when the underlying files are already
    gone — the replay loop may retry after a transient and we don't
    want a missing file to break the next cycle."""
    buf = PhotoBuffer(root_dir=str(tmp_path))
    buf.append({"i": 1}, b"jpeg")
    photo = buf.peek_all()[0]
    # Delete twice — second call must be a no-op
    buf.delete(photo)
    buf.delete(photo)  # must not raise


# ---------------------------------------------------------------------------
# size + total_bytes accounting
# ---------------------------------------------------------------------------


def test_size_counts_jpegs_only(tmp_path):
    """size() counts JPEGs, not sidecars or stray files. A misleading
    count would break the dashboard's buffered-photo badge."""
    buf = PhotoBuffer(root_dir=str(tmp_path))
    buf.append({"a": 1}, b"jpeg1")
    # Sleep enough that the millisecond-timestamp filename differs.
    # On fast Windows hosts two appends in the same ms collide and the
    # second overwrites the first.
    time.sleep(0.005)
    buf.append({"b": 2}, b"jpeg2")
    # Drop a stray non-jpeg into the dir — must not be counted
    (tmp_path / "stray.txt").write_text("not a photo")
    assert buf.size() == 2


def test_total_bytes_sums_jpeg_sizes(tmp_path):
    """total_bytes() sums JPEG byte sizes (not sidecars). Drives the
    eviction decision — if it overcounted by including sidecars we'd
    evict aggressively under the byte cap."""
    buf = PhotoBuffer(root_dir=str(tmp_path))
    buf.append({"a": 1}, b"x" * 100)
    time.sleep(0.005)  # ensure distinct ms-timestamp filenames
    buf.append({"b": 2}, b"x" * 250)
    assert buf.total_bytes() == 350


# ---------------------------------------------------------------------------
# prune: age-based deletion
# ---------------------------------------------------------------------------


def test_prune_deletes_photos_older_than_retention(tmp_path):
    """Photos older than retention_days must be deleted. We touch mtime
    backwards to simulate age (vs. waiting actual days in test)."""
    buf = PhotoBuffer(root_dir=str(tmp_path))
    p_old = buf.append({"k": "old"}, b"jpeg-old")
    p_new = buf.append({"k": "new"}, b"jpeg-new")
    # Set the old JPEG's mtime to 10 days ago. Sidecar mtime is
    # irrelevant — peek_all uses the JPEG's stat.
    ten_days_ago = time.time() - 10 * 86400
    os.utime(p_old, (ten_days_ago, ten_days_ago))

    deleted = buf.prune(retention_days=7)

    assert deleted == 1
    surviving = buf.peek_all()
    assert len(surviving) == 1
    assert surviving[0].metadata["k"] == "new"


def test_prune_uses_default_retention_when_none_passed(tmp_path):
    """prune() with no arg falls back to the configured retention_days.
    Mirrors LocalBuffer.prune semantics so the WS client can call it
    uniformly without re-passing the cap each time."""
    buf = PhotoBuffer(
        root_dir=str(tmp_path), retention_days=3,
    )
    p_old = buf.append({"k": "old"}, b"jpeg")
    five_days_ago = time.time() - 5 * 86400
    os.utime(p_old, (five_days_ago, five_days_ago))

    deleted = buf.prune()  # no arg → uses configured 3 days

    assert deleted == 1


# ---------------------------------------------------------------------------
# Byte-cap eviction: FIFO + on_eviction callback
# ---------------------------------------------------------------------------


def test_evict_if_over_cap_drops_oldest_FIFO(tmp_path):
    """Hard byte cap: when total bytes exceeds max_bytes, oldest photos
    are dropped first. Tiny cap forces eviction quickly without writing
    a full 1 GB."""
    # Cap of 1500 bytes, photos 1000 bytes each → at most 1 photo fits.
    buf = PhotoBuffer(root_dir=str(tmp_path), max_bytes=1500)
    buf.append({"i": 0}, b"x" * 1000)
    time.sleep(0.005)
    buf.append({"i": 1}, b"x" * 1000)  # forces eviction of i=0

    surviving = buf.peek_all()
    # i=0 is the oldest, must have been evicted
    assert len(surviving) == 1
    assert surviving[0].metadata["i"] == 1


def test_evict_if_over_cap_calls_on_eviction_callback(tmp_path):
    """on_eviction(reason, evicted_count) must fire when the byte cap
    evicts. Same shape as LocalBuffer's callback so WSClient can hook
    both buffers with one handler."""
    calls = []

    def record(*, reason, evicted_count):
        calls.append({"reason": reason, "evicted_count": evicted_count})

    buf = PhotoBuffer(
        root_dir=str(tmp_path),
        max_bytes=1500,
        on_eviction=record,
    )
    buf.append({"i": 0}, b"x" * 1000)
    time.sleep(0.005)
    buf.append({"i": 1}, b"x" * 1000)

    assert len(calls) == 1
    assert calls[0]["reason"] == "byte_cap"
    assert calls[0]["evicted_count"] >= 1


def test_evict_callback_failure_does_not_raise(tmp_path):
    """A buggy on_eviction callback must NOT propagate the exception out
    of append(). The eviction itself already happened; losing the
    notification is the least bad outcome (matches LocalBuffer)."""
    def boom(*, reason, evicted_count):
        raise RuntimeError("simulated callback bug")

    buf = PhotoBuffer(
        root_dir=str(tmp_path),
        max_bytes=1500,
        on_eviction=boom,
    )
    buf.append({"i": 0}, b"x" * 1000)
    time.sleep(0.005)
    # Must not raise — the eviction commits, the callback failure is
    # caught and logged.
    buf.append({"i": 1}, b"x" * 1000)
    assert buf.size() == 1
