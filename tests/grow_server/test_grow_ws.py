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


# ───────────────────────────────────────────────────────────────────────────
# Pydantic validation in the WS listener (I1)
#
# The connection handler in api_grow_ws.py validates inbound payloads via
# the shared mlss_contracts pydantic models BEFORE dispatching to the
# stateful handlers. Bad payloads are dropped with a warning — the
# connection stays up so a unit running buggy firmware doesn't get torn
# down for one malformed frame.
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handler_telemetry_accepts_valid_payload(server, monkeypatch):
    """Pinning the happy path: a fully-valid telemetry envelope must
    reach handle_telemetry exactly once with the parsed payload."""
    import mlss_monitor.routes.api_grow_ws as mod
    calls = []
    monkeypatch.setattr(
        mod, "handle_telemetry",
        lambda unit_id, ts, payload: calls.append((unit_id, ts, payload)),
    )
    port, token, _, _ = server
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/api/grow/1/ws",
        extra_headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        await ws.send(json.dumps({
            "type": "telemetry",
            "ts": "2026-05-03T12:34:18Z",
            "payload": {"soil_moisture_raw": 612, "light_state": True,
                        "pump_state": False, "soil_temp_c": 21.4},
        }))
        await asyncio.sleep(0.2)
    assert len(calls) == 1, f"expected one handler call, got {len(calls)}"
    assert calls[0][0] == 1
    assert calls[0][2]["soil_moisture_raw"] == 612


@pytest.mark.asyncio
async def test_handler_telemetry_rejects_invalid_payload(server, monkeypatch, caplog):
    """A telemetry payload missing required `soil_moisture_raw` must NOT
    reach handle_telemetry — pydantic rejects it in the listener and the
    listener logs a warning and continues."""
    import logging
    import mlss_monitor.routes.api_grow_ws as mod
    calls = []
    monkeypatch.setattr(
        mod, "handle_telemetry",
        lambda unit_id, ts, payload: calls.append((unit_id, ts, payload)),
    )
    port, token, _, _ = server
    with caplog.at_level(logging.WARNING, logger="mlss_monitor.routes.api_grow_ws"):
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/api/grow/1/ws",
            extra_headers={"Authorization": f"Bearer {token}"},
        ) as ws:
            # Missing required field soil_moisture_raw
            await ws.send(json.dumps({
                "type": "telemetry",
                "ts": "2026-05-03T12:34:18Z",
                "payload": {"light_state": True, "pump_state": False},
            }))
            # Connection should stay open — send a valid follow-up to prove it
            await ws.send(json.dumps({
                "type": "telemetry",
                "ts": "2026-05-03T12:34:19Z",
                "payload": {"soil_moisture_raw": 700, "light_state": True,
                            "pump_state": False},
            }))
            await asyncio.sleep(0.3)

    # Only the valid follow-up must have reached the handler
    assert len(calls) == 1, (
        f"only the valid follow-up should reach the handler; "
        f"calls={[c[2] for c in calls]}"
    )
    assert calls[0][2]["soil_moisture_raw"] == 700

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("validation" in r.getMessage().lower() or
               "invalid" in r.getMessage().lower() or
               "telemetry" in r.getMessage().lower()
               for r in warnings), (
        f"expected a warning about the rejected payload; got {[r.getMessage() for r in warnings]}"
    )


@pytest.mark.asyncio
async def test_handler_capabilities_accepts_valid_payload(server, monkeypatch):
    """Pinning happy-path capabilities flow."""
    import mlss_monitor.routes.api_grow_ws as mod
    calls = []
    monkeypatch.setattr(
        mod, "handle_capabilities",
        lambda unit_id, ts, payload: calls.append((unit_id, ts, payload)),
    )
    port, token, _, _ = server
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/api/grow/1/ws",
        extra_headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        await ws.send(json.dumps({
            "type": "capabilities",
            "ts": "2026-05-03T12:34:18Z",
            "payload": {
                "capabilities": [
                    {"channel": "soil_moisture", "hardware": "Seesaw",
                     "is_required": True, "unit_label": "raw",
                     "details": {"i2c_address": "0x36"}},
                ],
                "firmware_version": "0.1.0",
                "hardware_serial": "hw1",
            },
        }))
        await asyncio.sleep(0.2)
    assert len(calls) == 1
    assert calls[0][2]["firmware_version"] == "0.1.0"


@pytest.mark.asyncio
async def test_handler_capabilities_rejects_invalid_payload(server, monkeypatch, caplog):
    """A capabilities payload missing required `firmware_version` must NOT
    reach the handler."""
    import logging
    import mlss_monitor.routes.api_grow_ws as mod
    calls = []
    monkeypatch.setattr(
        mod, "handle_capabilities",
        lambda unit_id, ts, payload: calls.append((unit_id, ts, payload)),
    )
    port, token, _, _ = server
    with caplog.at_level(logging.WARNING, logger="mlss_monitor.routes.api_grow_ws"):
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/api/grow/1/ws",
            extra_headers={"Authorization": f"Bearer {token}"},
        ) as ws:
            await ws.send(json.dumps({
                "type": "capabilities",
                "ts": "2026-05-03T12:34:18Z",
                "payload": {
                    "capabilities": [],
                    # Missing required: firmware_version, hardware_serial
                },
            }))
            await asyncio.sleep(0.3)
    assert calls == [], "invalid capabilities must not reach handler"
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


@pytest.mark.asyncio
async def test_handler_event_accepts_valid_payload(server, monkeypatch):
    """Pinning happy-path event flow."""
    import mlss_monitor.routes.api_grow_ws as mod
    calls = []
    monkeypatch.setattr(
        mod, "handle_event",
        lambda unit_id, ts, payload: calls.append((unit_id, ts, payload)),
    )
    port, token, _, _ = server
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/api/grow/1/ws",
        extra_headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        await ws.send(json.dumps({
            "type": "event",
            "ts": "2026-05-03T12:34:18Z",
            "payload": {
                "kind": "watering_pulse",
                "details": {"duration_s": 5.0, "trigger": "pid"},
            },
        }))
        await asyncio.sleep(0.2)
    assert len(calls) == 1
    assert calls[0][2]["kind"] == "watering_pulse"


@pytest.mark.asyncio
async def test_handler_event_rejects_invalid_kind(server, monkeypatch, caplog):
    """An event with a `kind` outside the EventKind enum must be dropped
    by pydantic — protects grow_errors / grow_watering_events from
    junk inserts."""
    import logging
    import mlss_monitor.routes.api_grow_ws as mod
    calls = []
    monkeypatch.setattr(
        mod, "handle_event",
        lambda unit_id, ts, payload: calls.append((unit_id, ts, payload)),
    )
    port, token, _, _ = server
    with caplog.at_level(logging.WARNING, logger="mlss_monitor.routes.api_grow_ws"):
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/api/grow/1/ws",
            extra_headers={"Authorization": f"Bearer {token}"},
        ) as ws:
            await ws.send(json.dumps({
                "type": "event",
                "ts": "2026-05-03T12:34:18Z",
                "payload": {
                    "kind": "this_is_not_a_real_event_kind",
                    "details": {},
                },
            }))
            await asyncio.sleep(0.3)
    assert calls == [], "invalid event kind must not reach handler"
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


@pytest.mark.asyncio
async def test_invalid_payload_does_not_disconnect_unit(server, monkeypatch):
    """A bad message must not tear down the connection — a unit running
    buggy firmware shouldn't get disconnected, just lose the bad frame.
    """
    import mlss_monitor.routes.api_grow_ws as mod
    calls = []
    monkeypatch.setattr(
        mod, "handle_telemetry",
        lambda unit_id, ts, payload: calls.append(payload),
    )
    port, token, _, registry = server
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/api/grow/1/ws",
        extra_headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        # bad
        await ws.send(json.dumps({
            "type": "telemetry", "ts": "2026-05-03T12:34:18Z",
            "payload": {"light_state": True},  # missing required fields
        }))
        await asyncio.sleep(0.1)
        assert registry.is_connected(1) is True, (
            "connection must survive a single invalid payload"
        )
        # And we can still send a good one
        await ws.send(json.dumps({
            "type": "telemetry", "ts": "2026-05-03T12:34:19Z",
            "payload": {"soil_moisture_raw": 612, "light_state": True,
                        "pump_state": False},
        }))
        await asyncio.sleep(0.2)
    assert len(calls) == 1
    assert calls[0]["soil_moisture_raw"] == 612


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
