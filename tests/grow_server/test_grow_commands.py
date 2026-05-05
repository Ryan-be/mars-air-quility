"""POST /api/grow/units/<id>/identify and /water-now push commands via WS registry."""
import asyncio
import json
import sqlite3
import tempfile
import threading
from datetime import datetime
import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    init_db.create_db()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'hw1', 'X', ?, 'h', ?)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.commit()
    conn.close()

    from mlss_monitor.grow.ws_registry import WSRegistry
    from mlss_monitor import state
    state.grow_ws_registry = WSRegistry()

    class FakeWS:
        def __init__(self): self.sent = []
        async def send(self, m): self.sent.append(m)

    fake_ws = FakeWS()
    state.grow_ws_registry.register(1, fake_ws)

    # Spin up a background event loop so run_coroutine_threadsafe has somewhere
    # to schedule (mimics what api_grow_ws._run does in production).
    loop_ready = threading.Event()
    captured = {}

    def _run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        captured["loop"] = loop
        loop_ready.set()
        loop.run_forever()

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()
    loop_ready.wait(timeout=2)
    state.grow_ws_loop = captured["loop"]

    from flask import Flask
    from mlss_monitor.routes.api_grow_units import api_grow_units_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_units_bp)

    yield app.test_client(), fake_ws

    captured["loop"].call_soon_threadsafe(captured["loop"].stop)
    state.grow_ws_loop = None


def test_identify_pushes_command(client):
    c, fake_ws = client
    r = c.post("/api/grow/units/1/identify")
    assert r.status_code == 202
    assert len(fake_ws.sent) == 1
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["type"] == "command"
    assert cmd["payload"]["name"] == "identify"
    assert cmd["payload"]["args"]["duration_s"] == 10


def test_identify_offline_unit_returns_503(client):
    c, _ = client
    r = c.post("/api/grow/units/9999/identify")
    assert r.status_code == 503
    body = r.get_json()
    assert body["error"] == "unit_not_connected"


def test_water_now_pushes_command_with_duration(client):
    c, fake_ws = client
    r = c.post("/api/grow/units/1/water-now", json={"duration_s": 5})
    assert r.status_code == 202
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["payload"]["name"] == "water_now"
    assert cmd["payload"]["args"]["duration_s"] == 5


def test_water_now_default_duration_is_5s(client):
    c, fake_ws = client
    r = c.post("/api/grow/units/1/water-now", json={})
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["payload"]["args"]["duration_s"] == 5


def test_water_now_clamps_to_30s_safety_cap(client):
    c, fake_ws = client
    r = c.post("/api/grow/units/1/water-now", json={"duration_s": 999})
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["payload"]["args"]["duration_s"] == 30


def test_identify_send_failure_returns_503(monkeypatch):
    """If ws.send raises (e.g. peer dropped between lookup and send),
    the endpoint surfaces 503 with send_failed instead of crashing 500."""
    from mlss_monitor.grow.ws_registry import WSRegistry
    from mlss_monitor import state
    import asyncio
    import sqlite3
    import tempfile
    from datetime import datetime
    from flask import Flask
    from mlss_monitor.routes.api_grow_units import api_grow_units_bp

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    init_db.create_db()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'hw1', 'X', ?, 'h', ?)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.commit()
    conn.close()

    state.grow_ws_registry = WSRegistry()

    class FailingWS:
        async def send(self, m):
            raise RuntimeError("simulated peer disconnect")

    state.grow_ws_registry.register(1, FailingWS())

    # Spin up a real listener loop so run_coroutine_threadsafe has somewhere
    # to schedule against. The send will be scheduled there and raise.
    import threading
    loop_ready = threading.Event()
    captured = {}

    def _run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        captured["loop"] = loop
        loop_ready.set()
        loop.run_forever()

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()
    loop_ready.wait(timeout=2)
    state.grow_ws_loop = captured["loop"]

    try:
        app = Flask(__name__)
        app.register_blueprint(api_grow_units_bp)
        c = app.test_client()
        r = c.post("/api/grow/units/1/identify")
        assert r.status_code == 503
        assert r.get_json()["error"] == "send_failed"
    finally:
        captured["loop"].call_soon_threadsafe(captured["loop"].stop)
        state.grow_ws_loop = None
