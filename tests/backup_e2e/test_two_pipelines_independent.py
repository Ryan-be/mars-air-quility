"""E2E failure mode #4: db pipeline paused → files pipeline keeps
flowing.

The whole point of running two BackupWorker instances (one per
pipeline) is that a Postgres outage doesn't stall S3 shipping, and
vice versa. This test pauses Postgres + writes a photo and asserts
the JPEG still lands on MinIO. Two workers run in parallel for the
duration of the test.
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
    h_bytes = json.dumps(header).encode("utf-8")
    return struct.pack(">I", len(h_bytes)) + h_bytes + jpeg_bytes


def test_files_pipeline_unaffected_by_db_outage(
    configured_backup, db_worker, files_worker, monkeypatch, tmp_path,
):
    """Pause Postgres → db worker goes BACKOFF → write a photo → files
    worker still ships it to MinIO.

    Both workers are running (``db_worker`` + ``files_worker`` fixtures).
    """
    from database.init_db import DB_FILE  # pylint: disable=import-outside-toplevel
    from mlss_monitor.grow.photo_storage import (  # pylint: disable=import-outside-toplevel
        handle_photo_frame,
    )

    pg_container = configured_backup["postgres_params"]["container"]
    raw = pg_container.get_wrapped_container()

    images_dir = tmp_path / "images"
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", str(images_dir),
    )

    # Seed unit_5 so grow_photos FK is satisfied.
    now = datetime.utcnow()
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.execute(
            "INSERT INTO grow_units "
            "(id, hardware_serial, label, enrolled_at, bearer_token_hash, "
            " phase_set_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (5, "test-serial", "unit-5", now, "hash", now),
        )
        conn.commit()

    # Write a sentinel sensor row BEFORE pausing so the db worker has
    # something to fail on. Without a row in the outbox, the worker's
    # drain is a no-op (peek_rows returns empty) and it stays IDLE
    # even with Postgres paused. We need it to actually attempt a
    # connect to observe BACKOFF.
    from database.db_logger import log_sensor_data  # pylint: disable=import-outside-toplevel
    log_sensor_data(20.0, 50.0, 400, 100)

    # Pause Postgres. The db_worker will enter BACKOFF on its next
    # tick. The files_worker is untouched and keeps polling.
    raw.pause()
    try:
        # Wait until the db worker has observed a failure — we check
        # ``last_error`` rather than ``state`` because the state
        # oscillates between BACKOFF and DRAINING during the retry
        # loop (DRAINING is the "about to re-attempt" phase). The
        # ``last_error`` field is the durable signal.
        wait_until(
            lambda: db_worker.last_error is not None,
            timeout=30.0,
            message="db worker did not record a ship failure with Postgres paused",
        )

        # Write a photo. The blob enqueue + file write are local
        # SQLite/disk, so they succeed despite Postgres being down.
        # The files worker drains the blob outbox to MinIO.
        fake_jpeg = b"\xff\xd8\xff\xe0JFIF" + b"\x00" * 256
        frame = _frame({
            "taken_at": "2026-05-19T11:22:33Z",
            "width": 640, "height": 480, "jpeg_quality": 85,
        }, fake_jpeg)
        handle_photo_frame(unit_id=5, frame=frame)

        s3 = configured_backup["s3"]
        expected_key = "unit_005/2026-05-19/112233_000.jpg"
        wait_until(
            lambda: s3.head(bucket_suffix="photos", key=expected_key),
            timeout=20.0,
            message=(f"photo {expected_key!r} did not ship to S3 while "
                     "Postgres was down — files pipeline should be "
                     "independent of db pipeline outages"),
        )
    finally:
        raw.unpause()
