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
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
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
        def __init__(self):
            self.sent = []
        async def send(self, m):
            self.sent.append(m)

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
    app.secret_key = "test-secret"   # required for session_transaction
    app.register_blueprint(api_grow_units_bp)

    test_client = app.test_client()
    # Default session: admin (existing tests assume access)
    with test_client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_role"] = "admin"

    yield test_client, fake_ws

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
    c.post("/api/grow/units/1/water-now", json={})
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["payload"]["args"]["duration_s"] == 5


def test_water_now_clamps_to_30s_safety_cap(client):
    c, fake_ws = client
    c.post("/api/grow/units/1/water-now", json={"duration_s": 999})
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["payload"]["args"]["duration_s"] == 30


def test_identify_send_failure_returns_503(monkeypatch):
    """If ws.send raises (e.g. peer dropped between lookup and send),
    the endpoint surfaces 503 with send_failed instead of crashing 500."""
    from mlss_monitor.grow.ws_registry import WSRegistry
    from mlss_monitor import state
    from flask import Flask
    from mlss_monitor.routes.api_grow_units import api_grow_units_bp

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
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
        app.secret_key = "test-secret"
        app.register_blueprint(api_grow_units_bp)
        c = app.test_client()
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user_role"] = "admin"
        r = c.post("/api/grow/units/1/identify")
        assert r.status_code == 503
        assert r.get_json()["error"] == "send_failed"
    finally:
        captured["loop"].call_soon_threadsafe(captured["loop"].stop)
        state.grow_ws_loop = None


# ---------------------------------------------------------------------------
# RBAC: identify and water-now must require controller/admin role
# ---------------------------------------------------------------------------


def _set_session(test_client, *, logged_in=True, role="admin"):
    """Open a session on the test client with given auth state."""
    with test_client.session_transaction() as sess:
        sess["logged_in"] = logged_in
        sess["user_role"] = role


def test_identify_rejects_unauthenticated(client):
    """Anonymous request must be rejected with 401."""
    c, _ = client
    _set_session(c, logged_in=False, role="viewer")
    r = c.post("/api/grow/units/1/identify")
    assert r.status_code == 401
    assert r.get_json()["error"] == "Unauthorised"


def test_identify_rejects_viewer_role(client):
    """Viewers cannot actuate hardware - must be forbidden with 403."""
    c, _ = client
    _set_session(c, logged_in=True, role="viewer")
    r = c.post("/api/grow/units/1/identify")
    assert r.status_code == 403
    assert "Forbidden" in r.get_json()["error"]


def test_identify_allows_controller_role(client):
    """Controllers (and admins) can call identify."""
    c, _ = client
    _set_session(c, logged_in=True, role="controller")
    r = c.post("/api/grow/units/1/identify")
    assert r.status_code == 202


def test_identify_allows_admin_role(client):
    """Admin role can call identify."""
    c, _ = client
    _set_session(c, logged_in=True, role="admin")
    r = c.post("/api/grow/units/1/identify")
    assert r.status_code == 202


def test_water_now_rejects_unauthenticated(client):
    """Anonymous water-now must be rejected with 401 - this is the
    high-impact vuln (LAN attacker drowning a plant)."""
    c, _ = client
    _set_session(c, logged_in=False, role="viewer")
    r = c.post("/api/grow/units/1/water-now", json={"duration_s": 30})
    assert r.status_code == 401


def test_water_now_rejects_viewer_role(client):
    """Even authenticated viewers must be forbidden - household member with
    viewer credentials cannot trigger the pump."""
    c, _ = client
    _set_session(c, logged_in=True, role="viewer")
    r = c.post("/api/grow/units/1/water-now", json={"duration_s": 5})
    assert r.status_code == 403


def test_water_now_allows_controller_role(client):
    """Controller role can fire water-now."""
    c, _ = client
    _set_session(c, logged_in=True, role="controller")
    r = c.post("/api/grow/units/1/water-now", json={"duration_s": 5})
    assert r.status_code == 202


def test_unauthenticated_request_does_not_reach_command_dispatch(client):
    """Defence in depth: even if route is hit unauthenticated, the WS registry
    must NOT have received a command frame (proves the decorator short-circuits
    BEFORE _push_command_blocking)."""
    c, fake_ws = client
    _set_session(c, logged_in=False, role="viewer")
    fake_ws.sent.clear()
    c.post("/api/grow/units/1/water-now", json={"duration_s": 30})
    assert fake_ws.sent == []


# ── snap-photo ────────────────────────────────────────────────────────

def test_snap_photo_pushes_command(client):
    """POST /snap-photo pushes name=snap_photo so the firmware
    dispatcher's `name == "snap_photo"` branch fires + captures via
    picamera2 + uploads as a binary WS frame."""
    c, fake_ws = client
    r = c.post("/api/grow/units/1/snap-photo")
    assert r.status_code == 202
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["type"] == "command"
    assert cmd["payload"]["name"] == "snap_photo"


def test_snap_photo_offline_unit_returns_503(client):
    c, _ = client
    r = c.post("/api/grow/units/9999/snap-photo")
    assert r.status_code == 503


def test_snap_photo_viewer_denied(client):
    c, _ = client
    _set_session(c, logged_in=True, role="viewer")
    r = c.post("/api/grow/units/1/snap-photo")
    assert r.status_code == 403


# ── light-toggle ──────────────────────────────────────────────────────

def test_light_toggle_pushes_on_when_no_telemetry(client):
    """No telemetry yet → defaults to turning the light ON. Operator
    clicked Toggle, SOMETHING should happen."""
    c, fake_ws = client
    r = c.post("/api/grow/units/1/light-toggle")
    assert r.status_code == 202
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["payload"]["name"] == "light_override"
    assert cmd["payload"]["args"]["state"] == "on"
    assert cmd["payload"]["args"]["duration_min"] == 60


def test_light_toggle_pushes_off_when_light_is_on(client, monkeypatch):
    """Light currently on per latest telemetry → toggle to off.

    The light-toggle route reads grow_telemetry from DB_FILE. The
    `client` fixture patches init_db.DB_FILE but api_grow_units imports
    DB_FILE at module load (a separate binding) so we need to patch it
    there too AND seed telemetry into THAT path.
    """
    import sqlite3
    from datetime import datetime
    import mlss_monitor.routes.api_grow_units as api_grow_units
    c, fake_ws = client
    # Reach into the route module to find which DB it'll read
    db_path = api_grow_units.DB_FILE  # bound at module load — typically prod
    # Patch BOTH bindings so the route reads from the same tmp DB the
    # client fixture seeded the unit row into.
    import database.init_db as init_db
    monkeypatch.setattr(api_grow_units, "DB_FILE", init_db.DB_FILE)
    db_path = init_db.DB_FILE
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_telemetry "
        "(unit_id, timestamp_utc, soil_moisture_raw, light_state, pump_state) "
        "VALUES (1, ?, 500, 1, 0)", (datetime.utcnow(),),
    )
    conn.commit()
    conn.close()
    r = c.post("/api/grow/units/1/light-toggle")
    assert r.status_code == 202
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["payload"]["args"]["state"] == "off"


def test_light_toggle_viewer_denied(client):
    c, _ = client
    _set_session(c, logged_in=True, role="viewer")
    r = c.post("/api/grow/units/1/light-toggle")
    assert r.status_code == 403
