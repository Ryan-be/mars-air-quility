"""handle_photo_frame: parse binary frame, write JPEG, insert grow_photos row."""
import json
import os
import sqlite3
import struct
import tempfile
from datetime import datetime
import pytest


@pytest.fixture
def setup(tmp_path, monkeypatch):
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp_db.name
    monkeypatch.setattr("mlss_monitor.grow.handlers.DB_FILE", tmp_db.name)
    monkeypatch.setattr("mlss_monitor.grow.photo_storage.DB_FILE", tmp_db.name)
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", str(tmp_path / "images"))
    init_db.create_db()

    # Insert a unit + a telemetry row at the same instant the photo test uses,
    # so the ±60s join window matches it deterministically regardless of when
    # the suite is run.
    now = datetime.utcnow()
    photo_ts = datetime(2026, 5, 3, 12, 34, 18)
    conn = sqlite3.connect(tmp_db.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'hw1', 'X', ?, 'h', ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO grow_telemetry (id, unit_id, timestamp_utc, "
        "soil_moisture_raw, light_state, pump_state) "
        "VALUES (100, 1, ?, 612, 1, 0)", (photo_ts,),
    )
    conn.commit()
    conn.close()
    return tmp_db.name, str(tmp_path / "images")


def _frame(header: dict, jpeg_bytes: bytes) -> bytes:
    h_bytes = json.dumps(header).encode("utf-8")
    return struct.pack(">I", len(h_bytes)) + h_bytes + jpeg_bytes


def test_handle_photo_writes_file_and_db_row(setup):
    db_path, images_dir = setup
    from mlss_monitor.grow.photo_storage import handle_photo_frame
    from datetime import datetime

    fake_jpeg = b"\xff\xd8\xff\xe0FAKEIMAGEBYTES" + b"\x00" * 200
    frame = _frame({
        "taken_at": "2026-05-03T12:34:18Z",
        "width": 1920, "height": 1080, "jpeg_quality": 85,
        "shutter_us": 16667, "iso": 100,
    }, fake_jpeg)

    handle_photo_frame(unit_id=1, frame=frame)

    # File on disk
    expected_path = os.path.join(
        images_dir, "unit_001", "2026-05-03", "123418.jpg")
    assert os.path.exists(expected_path)
    with open(expected_path, "rb") as f:
        assert f.read() == fake_jpeg

    # DB row
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT file_path, width_px, height_px, size_bytes, telemetry_id "
        "FROM grow_photos WHERE unit_id=1"
    ).fetchone()
    assert row[0] == "unit_001/2026-05-03/123418.jpg"  # relative
    assert row[1] == 1920
    assert row[2] == 1080
    assert row[3] == len(fake_jpeg)
    assert row[4] == 100  # joined to the telemetry row inserted in fixture


def test_handle_photo_no_telemetry_match_leaves_telemetry_id_null(setup, tmp_path):
    """If no telemetry row within ±60s, telemetry_id stays NULL (will not break ML join — just absent)."""
    db_path, images_dir = setup
    from mlss_monitor.grow.photo_storage import handle_photo_frame
    fake = b"\xff\xd8\xff\xe0X"
    # Far-past timestamp — outside ±60s window of the seeded telemetry row
    frame = _frame({"taken_at": "2025-01-01T00:00:00Z",
                    "width": 100, "height": 100}, fake)
    handle_photo_frame(unit_id=1, frame=frame)
    conn = sqlite3.connect(db_path)
    tid = conn.execute(
        "SELECT telemetry_id FROM grow_photos WHERE size_bytes=?", (len(fake),)
    ).fetchone()[0]
    assert tid is None
