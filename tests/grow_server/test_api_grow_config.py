"""CRUD-shaped tests for the per-unit Configure-tab PUT endpoints (Task 2).

Covers PUT /api/grow/units/<id>/profile and /api/grow/units/<id>/pid:
  * happy-path partial updates write the right columns
  * pydantic rejects bad input → 400 with detail
  * unknown unit_id → 404
  * phase change stamps phase_set_by='user' + phase_set_at
  * best-effort WS push: when send_to_unit raises (unit not connected),
    the request still returns 200 — firmware re-pulls on reconnect
  * deadband_pct on /pid is silently accepted (no override column exists
    for it in the schema; documented in api_grow_config._PID_COLUMN_MAP)
"""
import asyncio
import json
import sqlite3
import tempfile
import threading
from datetime import datetime

import pytest


# ---------------------------------------------------------------------------
# Fixture: real Flask blueprint mounted on a fresh test app, with a fake WS
# registry + listener loop wired to state. Mirrors test_grow_commands.client.
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    init_db.create_db()

    # Seed one unit. plant_type+medium_type left as defaults ('generic'/'soil')
    # so we can prove partial-update doesn't clobber unrelated fields.
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, current_phase) "
        "VALUES (1, 'hw1', 'Original Label', ?, 'h', ?, 'vegetative')",
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

    # Patch the route module's DB_FILE so its sqlite3.connect goes to tmp
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_config.DB_FILE", tmp.name
    )

    from flask import Flask
    from mlss_monitor.routes.api_grow_config import api_grow_config_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_config_bp)

    test_client = app.test_client()
    with test_client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_role"] = "admin"

    yield test_client, fake_ws, tmp.name

    captured["loop"].call_soon_threadsafe(captured["loop"].stop)
    state.grow_ws_loop = None
    state.grow_ws_registry = None


def _row(db_path, unit_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM grow_units WHERE id=?", (unit_id,)
    ).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# /profile happy path + edge cases
# ---------------------------------------------------------------------------


def test_put_profile_updates_label_and_phase(client):
    c, _, db_path = client
    r = c.put(
        "/api/grow/units/1/profile",
        json={"label": "Tom 1", "current_phase": "flowering"},
    )
    assert r.status_code == 200, r.data
    assert r.get_json() == {"ok": True}
    row = _row(db_path, 1)
    assert row["label"] == "Tom 1"
    assert row["current_phase"] == "flowering"
    # Phase change stamps user attribution + a fresh phase_set_at.
    assert row["phase_set_by"] == "user"
    assert row["phase_set_at"] is not None


def test_put_profile_partial_update_does_not_clobber_other_fields(client):
    c, _, db_path = client
    # Update label only — plant_type / medium_type / current_phase must stay
    # at their seeded defaults.
    r = c.put(
        "/api/grow/units/1/profile",
        json={"label": "Just a label change"},
    )
    assert r.status_code == 200
    row = _row(db_path, 1)
    assert row["label"] == "Just a label change"
    assert row["plant_type"] == "generic"        # unchanged default
    assert row["medium_type"] == "soil"          # unchanged default
    assert row["current_phase"] == "vegetative"  # unchanged seed


def test_put_profile_rejects_bad_phase(client):
    c, _, _ = client
    r = c.put(
        "/api/grow/units/1/profile",
        json={"current_phase": "bogus"},
    )
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "invalid_payload"
    assert "detail" in body  # pydantic errors list


def test_put_profile_returns_404_for_unknown_unit(client):
    c, _, _ = client
    r = c.put(
        "/api/grow/units/99999/profile",
        json={"label": "X"},
    )
    assert r.status_code == 404
    assert r.get_json()["error"] == "unit_not_found"


def test_put_profile_pushes_config_changed_via_ws(client):
    c, fake_ws, _ = client
    r = c.put(
        "/api/grow/units/1/profile",
        json={"label": "Tom 1"},
    )
    assert r.status_code == 200
    assert len(fake_ws.sent) == 1
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["type"] == "command"
    assert cmd["payload"]["kind"] == "config_changed"
    assert cmd["payload"]["section"] == "profile"


def test_put_profile_succeeds_when_unit_not_connected(monkeypatch):
    """If the unit isn't in the registry (or send raises), the DB write
    still committed — return 200 anyway. Firmware will re-pull on reconnect.
    """
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

    # No registry/loop wired up — _push_config_changed should silently no-op.
    from mlss_monitor import state
    state.grow_ws_registry = None
    state.grow_ws_loop = None

    from flask import Flask
    from mlss_monitor.routes.api_grow_config import api_grow_config_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_config_bp)
    tc = app.test_client()
    with tc.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_role"] = "admin"

    r = tc.put("/api/grow/units/1/profile", json={"label": "Y"})
    assert r.status_code == 200
    # DB was still updated even though no WS push was possible.
    conn = sqlite3.connect(tmp.name)
    label = conn.execute("SELECT label FROM grow_units WHERE id=1").fetchone()[0]
    conn.close()
    assert label == "Y"


def test_put_profile_succeeds_when_send_raises(monkeypatch):
    """Best-effort: if send_to_unit raises for any reason, swallow and 200."""
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

    class FailingWS:
        async def send(self, m):
            raise RuntimeError("simulated peer disconnect")

    state.grow_ws_registry.register(1, FailingWS())

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

    try:
        from flask import Flask
        from mlss_monitor.routes.api_grow_config import api_grow_config_bp
        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.register_blueprint(api_grow_config_bp)
        tc = app.test_client()
        with tc.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user_role"] = "admin"

        r = tc.put("/api/grow/units/1/profile", json={"label": "Z"})
        assert r.status_code == 200
        conn = sqlite3.connect(tmp.name)
        label = conn.execute("SELECT label FROM grow_units WHERE id=1").fetchone()[0]
        conn.close()
        assert label == "Z"
    finally:
        captured["loop"].call_soon_threadsafe(captured["loop"].stop)
        state.grow_ws_loop = None
        state.grow_ws_registry = None


def test_put_profile_empty_body_is_noop_200(client):
    """Empty PUT body — no fields → no-op, return 200."""
    c, _, db_path = client
    r = c.put("/api/grow/units/1/profile", json={})
    assert r.status_code == 200
    row = _row(db_path, 1)
    # Nothing changed
    assert row["label"] == "Original Label"


# ---------------------------------------------------------------------------
# /pid happy path + edge cases
# ---------------------------------------------------------------------------


def test_put_pid_writes_override_columns(client):
    c, _, db_path = client
    r = c.put(
        "/api/grow/units/1/pid",
        json={"kp": 0.5, "soak_window_min": 60},
    )
    assert r.status_code == 200, r.data
    row = _row(db_path, 1)
    assert row["watering_kp_override"] == 0.5
    assert row["soak_window_min_override"] == 60
    # Untouched override columns stay NULL.
    assert row["watering_ki_override"] is None
    assert row["pulse_min_s_override"] is None


def test_put_pid_partial_update(client):
    """Only kp specified → other override columns remain NULL."""
    c, _, db_path = client
    r = c.put("/api/grow/units/1/pid", json={"kp": 0.42})
    assert r.status_code == 200
    row = _row(db_path, 1)
    assert row["watering_kp_override"] == 0.42
    assert row["watering_ki_override"] is None
    assert row["watering_kd_override"] is None
    assert row["watering_target_override"] is None
    assert row["soak_window_min_override"] is None


def test_put_pid_writes_target_pct(client):
    c, _, db_path = client
    r = c.put("/api/grow/units/1/pid", json={"target_pct": 55})
    assert r.status_code == 200
    row = _row(db_path, 1)
    assert row["watering_target_override"] == 55


def test_put_pid_writes_pulse_columns(client):
    c, _, db_path = client
    r = c.put(
        "/api/grow/units/1/pid",
        json={"min_pulse_s": 2, "max_pulse_s": 8},
    )
    assert r.status_code == 200
    row = _row(db_path, 1)
    assert row["pulse_min_s_override"] == 2
    assert row["pulse_max_s_override"] == 8


def test_put_pid_returns_400_when_min_gt_max(client):
    c, _, _ = client
    r = c.put(
        "/api/grow/units/1/pid",
        json={"min_pulse_s": 10, "max_pulse_s": 5},
    )
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "invalid_payload"


def test_put_pid_rejects_negative_kp(client):
    """Pydantic rejects kp<0; the route must surface 400 (not 500)."""
    c, _, _ = client
    r = c.put("/api/grow/units/1/pid", json={"kp": -0.1})
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_payload"


def test_put_pid_rejects_kp_over_10(client):
    c, _, _ = client
    r = c.put("/api/grow/units/1/pid", json={"kp": 10.5})
    assert r.status_code == 400


def test_put_pid_returns_404_for_unknown_unit(client):
    c, _, _ = client
    r = c.put("/api/grow/units/99999/pid", json={"kp": 0.5})
    assert r.status_code == 404
    assert r.get_json()["error"] == "unit_not_found"


def test_put_pid_pushes_config_changed_via_ws(client):
    c, fake_ws, _ = client
    r = c.put("/api/grow/units/1/pid", json={"kp": 0.5})
    assert r.status_code == 200
    assert len(fake_ws.sent) == 1
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["type"] == "command"
    assert cmd["payload"]["kind"] == "config_changed"
    assert cmd["payload"]["section"] == "pid"


def test_put_pid_deadband_pct_silently_ignored(client):
    """deadband_pct has no override column in grow_units. We accept the
    field for forward-compat (so firmware/UI can send it without 400) but
    silently drop it — a future migration can add the column.
    """
    c, _, db_path = client
    # deadband_pct alone — nothing to write, but no error
    r = c.put("/api/grow/units/1/pid", json={"deadband_pct": 5.0})
    assert r.status_code == 200
    # deadband_pct combined with a real field — real field still persists
    r = c.put(
        "/api/grow/units/1/pid",
        json={"deadband_pct": 7.0, "kp": 0.3},
    )
    assert r.status_code == 200
    row = _row(db_path, 1)
    assert row["watering_kp_override"] == 0.3
