"""End-to-end WS listener: connect with bearer token, send messages, verify dispatch."""
import asyncio
import json
import sqlite3
import struct
import tempfile
from datetime import datetime
import pytest
import websockets


@pytest.fixture
def server(monkeypatch):
    """Start the WS listener on a random port with a freshly-enrolled unit."""
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp_db.name
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp_db.name)
    monkeypatch.setattr("mlss_monitor.grow.handlers.DB_FILE", tmp_db.name)
    monkeypatch.setattr("mlss_monitor.grow.photo_storage.DB_FILE", tmp_db.name)
    monkeypatch.setattr("mlss_monitor.routes.api_grow_ws.DB_FILE", tmp_db.name)
    init_db.create_db()

    from mlss_monitor.grow.auth import generate_token, hash_secret
    raw = generate_token()
    conn = sqlite3.connect(tmp_db.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'hw1', 'X', ?, ?, ?)",
        (datetime.utcnow(), hash_secret(raw), datetime.utcnow()),
    )
    conn.commit()
    conn.close()

    from mlss_monitor.grow.ws_registry import WSRegistry
    from mlss_monitor.routes.api_grow_ws import start_ws_listener

    registry = WSRegistry()
    server_obj = start_ws_listener(host="127.0.0.1", port=0, registry=registry)
    port = server_obj.sockets[0].getsockname()[1]

    yield port, raw, tmp_db.name, registry

    server_obj.close()


@pytest.mark.asyncio
async def test_connect_with_valid_bearer_token_succeeds(server):
    port, token, _, registry = server
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/api/grow/1/ws",
        extra_headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        await asyncio.sleep(0.1)
        assert registry.is_connected(1) is True


@pytest.mark.asyncio
async def test_connect_with_wrong_token_rejected(server):
    port, _, _, _ = server
    with pytest.raises(websockets.InvalidStatusCode):
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/api/grow/1/ws",
            extra_headers={"Authorization": "Bearer wrong"},
        ):
            pass


@pytest.mark.asyncio
async def test_telemetry_message_persisted(server):
    port, token, db_path, _ = server
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/api/grow/1/ws",
        extra_headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        await ws.send(json.dumps({
            "type": "telemetry",
            "ts": "2026-05-03T12:34:18Z",
            "payload": {"soil_moisture_raw": 612, "light_state": True,
                        "pump_state": False},
        }))
        await asyncio.sleep(0.2)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT soil_moisture_raw FROM grow_telemetry WHERE unit_id=1"
    ).fetchone()
    assert row[0] == 612


@pytest.mark.asyncio
async def test_binary_frame_dispatched_to_photo_storage(server, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", str(tmp_path / "imgs"))
    port, token, db_path, _ = server
    fake = b"\xff\xd8FAKEJPEG"
    header = json.dumps({"taken_at": "2026-05-03T12:34:18Z",
                         "width": 100, "height": 100}).encode()
    frame = struct.pack(">I", len(header)) + header + fake
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/api/grow/1/ws",
        extra_headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        await ws.send(frame)
        await asyncio.sleep(0.2)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT size_bytes FROM grow_photos WHERE unit_id=1"
    ).fetchone()
    assert row[0] == len(fake)
