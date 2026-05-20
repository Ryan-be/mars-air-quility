"""E2E happy-path #2: photo write → outbox + blob enqueue → worker
drain → MinIO object visible.

The live ``handle_photo_frame`` writes the JPEG to a temp images
dir + inserts a ``grow_photos`` row + enqueues both a row pointer
(outbox_changes for grow_photos) and a blob pointer (outbox_blobs
for the JPEG). The ``files_worker`` thread drains the blob pointer
to MinIO; we then HEAD the object via the live S3Client to confirm.

The grow_photos row also lands in Postgres via the db pipeline, but
this test focuses on the file pipeline — see test_db_replication
for the row-shipping coverage.
"""
from __future__ import annotations

import json
import sqlite3
import struct
from datetime import datetime

import pytest

from tests.backup_e2e.conftest import wait_until

pytestmark = pytest.mark.e2e


def _frame(header: dict, jpeg_bytes: bytes) -> bytes:
    """Build a binary photo frame matching the live wire protocol —
    4-byte BE header length, JSON header, JPEG payload."""
    h_bytes = json.dumps(header).encode("utf-8")
    return struct.pack(">I", len(h_bytes)) + h_bytes + jpeg_bytes


def test_photo_lands_in_minio(configured_backup, files_worker, monkeypatch,
                              tmp_path):
    """Write one photo via the live ``handle_photo_frame`` helper. The
    blob outbox enqueue + drain ships the JPEG to ``test-photos`` at
    the expected key. HEAD-check via the test-side S3Client confirms.
    """
    from database.init_db import DB_FILE  # pylint: disable=import-outside-toplevel
    from mlss_monitor.grow.photo_storage import (  # pylint: disable=import-outside-toplevel
        handle_photo_frame,
    )

    # Photos write under MLSS_GROW_IMAGES_DIR — point it at a tmp dir
    # so the test doesn't smear the project-root data/grow_images.
    images_dir = tmp_path / "images"
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", str(images_dir),
    )

    # Seed the grow_unit row (grow_photos.unit_id REFERENCES grow_units.id).
    now = datetime.utcnow()
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.execute(
            "INSERT INTO grow_units "
            "(id, hardware_serial, label, enrolled_at, bearer_token_hash, "
            " phase_set_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (7, "test-serial", "unit-7", now, "hash", now),
        )
        conn.commit()

    fake_jpeg = b"\xff\xd8\xff\xe0JFIF" + b"\x00" * 256
    frame = _frame({
        "taken_at": "2026-05-19T10:11:12Z",
        "width": 640, "height": 480, "jpeg_quality": 85,
    }, fake_jpeg)
    handle_photo_frame(unit_id=7, frame=frame)

    s3 = configured_backup["s3"]
    expected_key = "unit_007/2026-05-19/101112_000.jpg"

    wait_until(
        lambda: s3.head(bucket_suffix="photos", key=expected_key),
        timeout=20.0,
        message=f"photo {expected_key!r} did not appear in test-photos bucket",
    )
