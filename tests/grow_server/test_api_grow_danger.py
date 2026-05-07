"""Phase 3 Task 4 — Diagnostics tab "Danger Zone" actions.

Two admin-only endpoints:

  * DELETE /api/grow/units/<id>          — soft-delete (is_active=0).
                                           Telemetry history + grow_photos
                                           are preserved.
  * POST   /api/grow/units/<id>/clear-buffer — synchronous WS push of a
                                           {"name": "clear_buffer"} command.

Tests cover:
  * RBAC (admin-only — viewer + controller get 403)
  * 404 handling
  * Soft-delete preserves grow_telemetry rows (no cascade)
  * clear-buffer pushes the right WS payload + returns 503 when the unit
    is disconnected
"""
import asyncio
import json
import sqlite3
import tempfile
import threading
from datetime import datetime

import pytest


def _set_session(c, *, logged_in=True, role="admin"):
    with c.session_transaction() as sess:
        sess["logged_in"] = logged_in
        sess["user_role"] = role


@pytest.fixture
def client(monkeypatch):
    """Mount the api_grow_units blueprint against a fresh DB seeded with
    one active grow_unit (id=1). For clear-buffer tests the fixture also
    spins up a fake WS registry + listener loop so the synchronous push
    path has somewhere to send to.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_units.DB_FILE", tmp.name
    )
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    monkeypatch.setattr("mlss_monitor.grow.health_watchdog.DB_FILE", tmp.name)
    init_db.create_db()

    now = datetime.utcnow()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, is_active) "
        "VALUES (1, 'hw-1', 'Tom 1', ?, 'h', ?, 1)",
        (now, now),
    )
    conn.commit()
    conn.close()

    # Fake WS registry + listener loop so the clear-buffer push path has
    # somewhere to land — same plumbing as test_api_grow_config.client.
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

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()
    loop_ready.wait(timeout=2)
    state.grow_ws_loop = captured["loop"]

    from flask import Flask
    from mlss_monitor.routes.api_grow_units import api_grow_units_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_units_bp)
    test_client = app.test_client()
    _set_session(test_client, role="admin")

    yield test_client, fake_ws, tmp.name

    captured["loop"].call_soon_threadsafe(captured["loop"].stop)
    state.grow_ws_loop = None
    state.grow_ws_registry = None


# ---------------------------------------------------------------------------
# DELETE /api/grow/units/<id> — soft-delete
# ---------------------------------------------------------------------------


def test_delete_unit_soft_deletes_sets_is_active_zero(client):
    """admin DELETE → 200 + grow_units.is_active flips to 0.

    No cascade — telemetry + photo rows must NOT be deleted (proven by
    test_delete_unit_preserves_telemetry_history below).
    """
    c, _, db_path = client
    r = c.delete("/api/grow/units/1")
    assert r.status_code == 200, r.data
    assert r.get_json() == {"ok": True}
    conn = sqlite3.connect(db_path)
    is_active = conn.execute(
        "SELECT is_active FROM grow_units WHERE id=1"
    ).fetchone()[0]
    conn.close()
    assert is_active == 0


def test_delete_unit_admin_only_viewer_gets_403(client):
    c, _, _ = client
    _set_session(c, role="viewer")
    r = c.delete("/api/grow/units/1")
    assert r.status_code == 403


def test_delete_unit_admin_only_controller_gets_403(client):
    c, _, _ = client
    _set_session(c, role="controller")
    r = c.delete("/api/grow/units/1")
    assert r.status_code == 403


def test_delete_unit_404_for_unknown(client):
    c, _, _ = client
    r = c.delete("/api/grow/units/9999")
    assert r.status_code == 404
    assert r.get_json()["error"] == "unit_not_found"


def test_delete_unit_404_for_already_deleted(client):
    """Idempotency check: a unit already soft-deleted (is_active=0) is
    indistinguishable from a never-existed unit — both return 404.

    This means a retried DELETE doesn't accidentally touch a different
    row that happens to have the same id (impossible in SQLite, but the
    is_active=1 guard makes the contract explicit)."""
    c, _, db_path = client
    # Soft-delete first, then try again.
    r1 = c.delete("/api/grow/units/1")
    assert r1.status_code == 200
    r2 = c.delete("/api/grow/units/1")
    assert r2.status_code == 404


def test_delete_unit_preserves_telemetry_history(client):
    """Soft-delete must NOT cascade-delete grow_telemetry rows.

    Audit + forensics: even after a unit is decommissioned, the telemetry
    it produced while live remains queryable. This is the rationale for
    soft-delete vs hard-DELETE — the operator can revive the unit later
    via a manual UPDATE without losing context.
    """
    c, _, db_path = client
    # Seed one telemetry row + one watering event for unit 1
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_telemetry "
        "(unit_id, timestamp_utc, soil_moisture_raw, soil_moisture_pct, "
        " light_state, pump_state) "
        "VALUES (1, ?, 612, 58, 1, 0)",
        (now,),
    )
    conn.execute(
        "INSERT INTO grow_watering_events "
        "(unit_id, timestamp_utc, trigger, duration_s, triggered_by) "
        "VALUES (1, ?, 'manual', 5, 'user')",
        (now,),
    )
    conn.commit()
    conn.close()

    r = c.delete("/api/grow/units/1")
    assert r.status_code == 200

    # Telemetry + watering history rows still present
    conn = sqlite3.connect(db_path)
    tel_count = conn.execute(
        "SELECT COUNT(*) FROM grow_telemetry WHERE unit_id=1"
    ).fetchone()[0]
    we_count = conn.execute(
        "SELECT COUNT(*) FROM grow_watering_events WHERE unit_id=1"
    ).fetchone()[0]
    conn.close()
    assert tel_count == 1, "telemetry must survive soft-delete"
    assert we_count == 1, "watering events must survive soft-delete"


def test_delete_unit_drops_unit_from_list_endpoint(client):
    """End-to-end: after DELETE, the unit no longer appears in
    GET /api/grow/units (which filters on is_active=1)."""
    c, _, _ = client
    # Confirm baseline visibility
    r0 = c.get("/api/grow/units")
    assert any(u["id"] == 1 for u in r0.get_json()["units"])
    # Soft-delete + re-list
    c.delete("/api/grow/units/1")
    r1 = c.get("/api/grow/units")
    assert all(u["id"] != 1 for u in r1.get_json()["units"]), \
        "soft-deleted unit must disappear from fleet view"


# ---------------------------------------------------------------------------
# POST /api/grow/units/<id>/clear-buffer — synchronous WS push
# ---------------------------------------------------------------------------


def test_clear_buffer_pushes_command_via_ws(client):
    """admin POST → fake registry's send received {"name":"clear_buffer"}.

    Mirrors the safety_override push contract: 202 + {"queued": true} on
    confirmed delivery; the payload uses the legacy `name`-keyed shape
    (same as identify / water_now) since the firmware dispatcher routes
    on `name=="clear_buffer"`.
    """
    c, fake_ws, _ = client
    r = c.post("/api/grow/units/1/clear-buffer")
    assert r.status_code == 202, r.data
    assert r.get_json() == {"queued": True}
    assert len(fake_ws.sent) == 1
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["type"] == "command"
    assert cmd["payload"] == {"name": "clear_buffer"}


def test_clear_buffer_returns_503_when_unit_disconnected(monkeypatch):
    """Unit not in registry → 503 unit_not_connected.

    Same contract as safety_override: clear-buffer is intent-to-act-now,
    so a 503 surfaces clearly rather than silently being best-effort.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_units.DB_FILE", tmp.name
    )
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    init_db.create_db()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, is_active) "
        "VALUES (1, 'hw-1', 'Tom 1', ?, 'h', ?, 1)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.commit()
    conn.close()

    from mlss_monitor import state
    state.grow_ws_registry = None
    state.grow_ws_loop = None

    from flask import Flask
    from mlss_monitor.routes.api_grow_units import api_grow_units_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_units_bp)
    tc = app.test_client()
    with tc.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_role"] = "admin"

    r = tc.post("/api/grow/units/1/clear-buffer")
    assert r.status_code == 503
    assert r.get_json()["error"] == "unit_not_connected"


def test_clear_buffer_admin_only_viewer_gets_403(client):
    c, _, _ = client
    _set_session(c, role="viewer")
    r = c.post("/api/grow/units/1/clear-buffer")
    assert r.status_code == 403


def test_clear_buffer_admin_only_controller_gets_403(client):
    c, _, _ = client
    _set_session(c, role="controller")
    r = c.post("/api/grow/units/1/clear-buffer")
    assert r.status_code == 403
