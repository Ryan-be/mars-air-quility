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
    from mlss_monitor.routes.api_grow_ws import (
        _clear_auth_cache, start_ws_listener, stop_ws_listener,
    )

    # Drop any cached verifications from a previous test — fresh DB, fresh
    # tokens, but the cache is keyed on (unit_id, raw_token) which could
    # collide across runs of the same fixture.
    _clear_auth_cache()
    registry = WSRegistry()
    handle = start_ws_listener(host="127.0.0.1", port=0, registry=registry)
    port = handle.sockets[0].getsockname()[1]

    yield port, raw, tmp_db.name, registry

    stop_ws_listener(handle)


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


def _make_fake_serve(captured):
    """Build a fake websockets.serve() that captures kwargs and returns a
    mock server whose wait_closed() blocks forever (until close() fires the
    event), so the listener's _run() loop doesn't exit prematurely and
    leak a pending stop_ws_listener task."""
    from unittest.mock import MagicMock

    async def fake_serve(handler, host, port, **kwargs):
        captured["kwargs"] = kwargs
        captured["host"] = host
        captured["port"] = port
        closed = asyncio.Event()
        srv = MagicMock()
        srv.sockets = []
        srv.close = lambda: closed.set()

        async def _wait_closed():
            await closed.wait()

        srv.wait_closed = _wait_closed
        return srv

    return fake_serve


def test_start_ws_listener_passes_ssl_context_to_serve(monkeypatch):
    """When given an ssl_context, start_ws_listener must forward it to
    websockets.serve as the ssl= kwarg. Production hardening: this is
    the bridge between the documented wss:// threat model and the actual
    listener binding."""
    import ssl
    import time
    import mlss_monitor.routes.api_grow_ws as mod

    captured = {}
    monkeypatch.setattr(mod.websockets, "serve", _make_fake_serve(captured))
    fake_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

    from mlss_monitor.grow.ws_registry import WSRegistry
    handle = mod.start_ws_listener(
        host="127.0.0.1", port=0,
        registry=WSRegistry(),
        ssl_context=fake_ctx,
    )

    # Wait for the listener to call websockets.serve
    for _ in range(50):
        if "kwargs" in captured:
            break
        time.sleep(0.05)

    assert "kwargs" in captured, "websockets.serve was never called"
    assert captured["kwargs"].get("ssl") is fake_ctx, \
        "ssl_context must be forwarded as the ssl= kwarg to websockets.serve"
    mod.stop_ws_listener(handle)


def test_start_ws_listener_omits_ssl_kwarg_when_no_context(monkeypatch):
    """When no ssl_context provided (dev/test mode), the ssl kwarg must
    NOT be passed at all (passing ssl=None changes websockets behavior in
    some versions)."""
    import time
    import mlss_monitor.routes.api_grow_ws as mod

    captured = {}
    monkeypatch.setattr(mod.websockets, "serve", _make_fake_serve(captured))

    from mlss_monitor.grow.ws_registry import WSRegistry
    handle = mod.start_ws_listener(
        host="127.0.0.1", port=0,
        registry=WSRegistry(),
        # ssl_context omitted (defaults to None)
    )

    for _ in range(50):
        if "kwargs" in captured:
            break
        time.sleep(0.05)

    assert "kwargs" in captured
    assert "ssl" not in captured["kwargs"], \
        "ssl kwarg must be absent when ssl_context is None"
    mod.stop_ws_listener(handle)
