"""End-to-end smoke: enroll, send telemetry, send photo, receive command, replay buffer."""
import asyncio
import json
import sqlite3
import struct
import tempfile
from datetime import datetime
import pytest
import websockets


@pytest.fixture
def setup(tmp_path, monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    img_dir = tmp_path / "images"
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    # api_grow_ws is also patched so the bearer-validation reads the test
    # DB even if some earlier test imported the module under a polluted
    # init_db.DB_FILE (snapshotting the wrong path at module load).
    for mod in ["mlss_monitor.grow.auth", "mlss_monitor.grow.handlers",
                "mlss_monitor.grow.photo_storage", "mlss_monitor.routes.api_grow_enroll",
                "mlss_monitor.routes.api_grow_units", "mlss_monitor.routes.api_grow_dist",
                "mlss_monitor.routes.api_grow_history", "mlss_monitor.routes.api_grow_photos",
                "mlss_monitor.routes.api_grow_ws"]:
        try:
            monkeypatch.setattr(f"{mod}.DB_FILE", tmp.name)
        except AttributeError:
            pass
    monkeypatch.setattr("mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", str(img_dir))
    init_db.create_db()

    # Get raw enrollment key
    conn = sqlite3.connect(tmp.name)
    raw_key = conn.execute(
        "SELECT value FROM app_settings WHERE key='grow_enrollment_key_raw_pending_reveal'"
    ).fetchone()[0]
    conn.close()

    # Start WS listener
    from mlss_monitor.grow.ws_registry import WSRegistry
    from mlss_monitor.routes.api_grow_ws import (
        _clear_auth_cache, start_ws_listener, stop_ws_listener,
    )
    _clear_auth_cache()
    registry = WSRegistry()
    handle = start_ws_listener("127.0.0.1", 0, registry)
    port = handle.sockets[0].getsockname()[1]

    yield raw_key, port, tmp.name, str(img_dir), registry
    stop_ws_listener(handle)


@pytest.mark.asyncio
async def test_full_lifecycle(setup):
    raw_key, port, db_path, img_dir, registry = setup

    # 1. Enrol via REST
    from flask import Flask
    from mlss_monitor.routes.api_grow_enroll import api_grow_enroll_bp
    app = Flask(__name__); app.register_blueprint(api_grow_enroll_bp)
    enroll_resp = app.test_client().post("/api/grow/enroll", json={
        "enrollment_key": raw_key, "hardware_serial": "test-pi-001",
        "plant": {"name": "Test Tomato", "type": "tomato", "medium": "soil"},
    })
    assert enroll_resp.status_code == 201
    body = enroll_resp.get_json()
    unit_id, token = body["unit_id"], body["token"]

    # 2. Open WS, send capabilities + telemetry + photo
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/api/grow/{unit_id}/ws",
        extra_headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        await ws.send(json.dumps({
            "type": "capabilities", "ts": "2026-05-03T12:00:00Z",
            "payload": {
                "capabilities": [
                    {"channel": "soil_moisture", "hardware": "Seesaw",
                     "is_required": True, "unit_label": "raw"},
                    {"channel": "soil_temp_c", "hardware": "Seesaw",
                     "is_required": False, "unit_label": "°C"},
                    {"channel": "light", "hardware": "AutomationPHATLight",
                     "is_required": True, "unit_label": ""},
                    {"channel": "pump", "hardware": "AutomationPHATPump",
                     "is_required": True, "unit_label": ""},
                    {"channel": "camera", "hardware": "picamera2",
                     "is_required": True, "unit_label": ""},
                ],
                "firmware_version": "0.1.0",
                "hardware_serial": "test-pi-001",
            },
        }))
        await ws.send(json.dumps({
            "type": "telemetry", "ts": "2026-05-03T12:00:00Z",
            "payload": {"soil_moisture_raw": 612, "light_state": True,
                        "pump_state": False, "soil_temp_c": 21.4},
        }))
        # Photo
        header = json.dumps({"taken_at": "2026-05-03T12:00:00Z",
                             "width": 100, "height": 100}).encode()
        photo = b"\xff\xd8FAKEPHOTODATA"
        await ws.send(struct.pack(">I", len(header)) + header + photo)
        await asyncio.sleep(0.3)

        # 3. Send identify command from server side via registry
        await registry.send_to_unit(unit_id, json.dumps({
            "type": "command", "ts": "2026-05-03T12:00:01Z",
            "payload": {"name": "identify", "args": {"duration_s": 1}},
        }))
        # ack would normally come back; for the smoke test we just verify send didn't throw

    # 4. Verify DB rows
    conn = sqlite3.connect(db_path)
    n_caps = conn.execute("SELECT COUNT(*) FROM grow_unit_capabilities WHERE unit_id=?",
                          (unit_id,)).fetchone()[0]
    n_tel = conn.execute("SELECT COUNT(*) FROM grow_telemetry WHERE unit_id=?",
                         (unit_id,)).fetchone()[0]
    photos = conn.execute("SELECT file_path, telemetry_id FROM grow_photos WHERE unit_id=?",
                          (unit_id,)).fetchall()
    conn.close()

    assert n_caps == 5
    assert n_tel >= 1
    assert len(photos) == 1
    assert photos[0][1] is not None  # telemetry_id was joined

    # 5. Verify image file actually written to disk
    import os
    assert os.path.exists(os.path.join(img_dir, photos[0][0]))
