"""RBAC tests for the per-unit Configure-tab PUT endpoints (Task 2).

`/profile` and `/pid` use `require_role("controller", "admin")` — anonymous
sessions get 401, viewer sessions get 403, controller and admin sessions
get 200. Mirrors the pattern in tests/grow_server/test_grow_commands.py
for the existing `/identify` and `/water-now` endpoints.
"""
import asyncio
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
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_config.DB_FILE", tmp.name
    )

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

    loop_ready = threading.Event()
    captured = {}

    def _run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        captured["loop"] = loop
        loop_ready.set()
        loop.run_forever()

    threading.Thread(target=_run_loop, daemon=True).start()
    loop_ready.wait(timeout=2)
    state.grow_ws_loop = captured["loop"]

    from flask import Flask
    from mlss_monitor.routes.api_grow_config import api_grow_config_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_config_bp)
    tc = app.test_client()
    yield tc, fake_ws

    captured["loop"].call_soon_threadsafe(captured["loop"].stop)
    state.grow_ws_loop = None
    state.grow_ws_registry = None


def _set_session(test_client, *, logged_in=True, role="admin"):
    with test_client.session_transaction() as sess:
        sess["logged_in"] = logged_in
        sess["user_role"] = role


# ---------------------------------------------------------------------------
# /profile RBAC
# ---------------------------------------------------------------------------


def test_profile_put_denies_anonymous(client):
    c, _ = client
    _set_session(c, logged_in=False, role="viewer")
    r = c.put("/api/grow/units/1/profile", json={"label": "X"})
    assert r.status_code == 401
    assert r.get_json()["error"] == "Unauthorised"


def test_profile_put_denies_viewer(client):
    c, _ = client
    _set_session(c, logged_in=True, role="viewer")
    r = c.put("/api/grow/units/1/profile", json={"label": "X"})
    assert r.status_code == 403
    assert "Forbidden" in r.get_json()["error"]


def test_profile_put_allows_controller(client):
    c, _ = client
    _set_session(c, logged_in=True, role="controller")
    r = c.put("/api/grow/units/1/profile", json={"label": "X"})
    assert r.status_code == 200


def test_profile_put_allows_admin(client):
    c, _ = client
    _set_session(c, logged_in=True, role="admin")
    r = c.put("/api/grow/units/1/profile", json={"label": "X"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# /pid RBAC
# ---------------------------------------------------------------------------


def test_pid_put_denies_anonymous(client):
    c, _ = client
    _set_session(c, logged_in=False, role="viewer")
    r = c.put("/api/grow/units/1/pid", json={"kp": 0.5})
    assert r.status_code == 401
    assert r.get_json()["error"] == "Unauthorised"


def test_pid_put_denies_viewer(client):
    c, _ = client
    _set_session(c, logged_in=True, role="viewer")
    r = c.put("/api/grow/units/1/pid", json={"kp": 0.5})
    assert r.status_code == 403


def test_pid_put_allows_controller(client):
    c, _ = client
    _set_session(c, logged_in=True, role="controller")
    r = c.put("/api/grow/units/1/pid", json={"kp": 0.5})
    assert r.status_code == 200


def test_pid_put_allows_admin(client):
    c, _ = client
    _set_session(c, logged_in=True, role="admin")
    r = c.put("/api/grow/units/1/pid", json={"kp": 0.5})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# /light_windows RBAC (Task 3)
# ---------------------------------------------------------------------------


_LIGHT_WINDOWS_BODY = {
    "phase": "vegetative",
    "windows": [{"start": "06:00", "end": "20:00"}],
}


def test_light_windows_put_denies_anonymous(client):
    c, _ = client
    _set_session(c, logged_in=False, role="viewer")
    r = c.put("/api/grow/units/1/light_windows", json=_LIGHT_WINDOWS_BODY)
    assert r.status_code == 401
    assert r.get_json()["error"] == "Unauthorised"


def test_light_windows_put_denies_viewer(client):
    c, _ = client
    _set_session(c, logged_in=True, role="viewer")
    r = c.put("/api/grow/units/1/light_windows", json=_LIGHT_WINDOWS_BODY)
    assert r.status_code == 403
    assert "Forbidden" in r.get_json()["error"]


def test_light_windows_put_allows_controller(client):
    c, _ = client
    _set_session(c, logged_in=True, role="controller")
    r = c.put("/api/grow/units/1/light_windows", json=_LIGHT_WINDOWS_BODY)
    assert r.status_code == 200


def test_light_windows_put_allows_admin(client):
    c, _ = client
    _set_session(c, logged_in=True, role="admin")
    r = c.put("/api/grow/units/1/light_windows", json=_LIGHT_WINDOWS_BODY)
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Defence-in-depth: an unauthenticated request must NOT reach the DB.
# ---------------------------------------------------------------------------


def test_unauthenticated_pid_does_not_persist(client):
    """Even if the route is hit unauthenticated, the override columns must
    remain NULL — proves the decorator short-circuits BEFORE the UPDATE.
    """
    c, _ = client
    _set_session(c, logged_in=False, role="viewer")
    c.put("/api/grow/units/1/pid", json={"kp": 0.99})

    # Verify the DB row is untouched.
    from mlss_monitor.routes.api_grow_config import DB_FILE
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute(
        "SELECT watering_kp_override FROM grow_units WHERE id=1"
    ).fetchone()
    conn.close()
    assert row[0] is None
