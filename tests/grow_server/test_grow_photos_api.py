"""GET /api/grow/units/<id>/photo/latest serves the latest photo file."""
import os
import sqlite3
import tempfile
from datetime import datetime
import pytest


@pytest.fixture
def setup(tmp_path, monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.routes.api_grow_photos.DB_FILE", tmp.name)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_photos.GROW_IMAGES_DIR", str(tmp_path / "imgs"))
    init_db.create_db()
    img_dir = tmp_path / "imgs" / "unit_001" / "2026-05-03"
    img_dir.mkdir(parents=True)
    (img_dir / "120000.jpg").write_bytes(b"\xff\xd8FAKEJPEG")

    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, 'h', ?)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.execute(
        "INSERT INTO grow_photos (unit_id, taken_at, file_path, width_px, "
        "height_px, size_bytes) VALUES (1, ?, ?, 100, 100, 9)",
        (datetime(2026, 5, 3, 12, 0, 0), "unit_001/2026-05-03/120000.jpg"),
    )
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_photos import api_grow_photos_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_photos_bp)
    return app.test_client()


def test_latest_serves_jpeg(setup):
    r = setup.get("/api/grow/units/1/photo/latest")
    assert r.status_code == 200
    assert r.mimetype == "image/jpeg"
    assert r.data == b"\xff\xd8FAKEJPEG"


def test_latest_404_for_unit_with_no_photos(setup):
    r = setup.get("/api/grow/units/9999/photo/latest")
    assert r.status_code == 404
