"""Outbox integration tests for the grow HTTP-route writers.

Phase 2 Task 10 wires every UPDATE / INSERT performed by the operator-facing
grow API routes through ``outbox.enqueue_row`` (and, for the strict-mirror
``grow_light_windows`` replace, ``outbox.enqueue_delete_scope`` before the
DELETE). These tests assert the enqueue side-effect on each writer rather
than the user-visible response shape — the CRUD-shape assertions live in
``test_api_grow_config.py`` etc. and are unchanged by this refactor.

Tables in scope:
  * ``grow_units``           via /profile, /pid, /calibration, /safety_override,
                                  /photo_schedule, /enroll, /rotate-token,
                                  /decommission (DELETE)
  * ``grow_light_windows``   strict-mirror via /light_windows
                              (enqueue_delete_scope({unit_id, phase}) before
                               the DELETE+INSERT replace)
  * ``grow_errors``          via the /safety_override audit row
  * ``grow_plant_profiles``  via /api/grow/plant-profiles/<id>

clear_photos DELETE on grow_photos is append-mostly: NO outbox enqueue. We
have a regression test for that here too so future contributors can't quietly
add one.
"""
import asyncio
import json
import sqlite3
import tempfile
import threading
from datetime import datetime, timedelta

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _outbox_rows(db_path: str, *, table: str | None = None):
    conn = sqlite3.connect(db_path)
    try:
        if table is None:
            return list(conn.execute(
                "SELECT table_name, pk FROM outbox_changes ORDER BY id"))
        return list(conn.execute(
            "SELECT table_name, pk FROM outbox_changes "
            "WHERE table_name=? ORDER BY id", (table,)))
    finally:
        conn.close()


def _delete_scope_rows(db_path: str, *, table: str | None = None):
    conn = sqlite3.connect(db_path)
    try:
        if table is None:
            return list(conn.execute(
                "SELECT table_name, scope_json FROM outbox_delete_scope ORDER BY id"))
        return list(conn.execute(
            "SELECT table_name, scope_json FROM outbox_delete_scope "
            "WHERE table_name=? ORDER BY id", (table,)))
    finally:
        conn.close()


def _set_session(c, *, role="admin", user="test-admin"):
    with c.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_role"] = role
        sess["user"] = user


def _seed_unit(db_path: str, unit_id: int = 1, *, label="Tom 1",
               current_phase="vegetative") -> None:
    """Seed a single active grow_unit row directly (bypasses the API)."""
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, current_phase) "
        "VALUES (?, ?, ?, ?, 'h', ?, ?)",
        (unit_id, f"hw-{unit_id}", label, now, now, current_phase),
    )
    conn.commit()
    conn.close()


def _start_fake_ws_loop(unit_id: int = 1):
    """Spin up a fake WS registry + listener loop so synchronous pushes land.

    Returns (fake_ws, loop) — the caller is responsible for stopping the loop
    and clearing ``state.grow_ws_*`` in teardown.
    """
    from mlss_monitor.grow.ws_registry import WSRegistry
    from mlss_monitor import state
    state.grow_ws_registry = WSRegistry()

    class FakeWS:
        def __init__(self):
            self.sent = []

        async def send(self, m):
            self.sent.append(m)

    fake_ws = FakeWS()
    state.grow_ws_registry.register(unit_id, fake_ws)

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
    return fake_ws, captured["loop"]


def _stop_fake_ws_loop(loop):
    from mlss_monitor import state
    if loop is not None:
        loop.call_soon_threadsafe(loop.stop)
    state.grow_ws_loop = None
    state.grow_ws_registry = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config_client(monkeypatch):
    """Flask test client mounting api_grow_config + a fake WS loop.

    Seeds one active unit (id=1, current_phase='vegetative'). Yields
    ``(client, db_path)`` — most enqueue assertions don't need the WS body.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_config.DB_FILE", tmp.name
    )
    init_db.create_db()
    _seed_unit(tmp.name, unit_id=1)

    _, loop = _start_fake_ws_loop(unit_id=1)

    from flask import Flask
    from mlss_monitor.routes.api_grow_config import api_grow_config_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_config_bp)
    tc = app.test_client()
    _set_session(tc, role="admin")

    yield tc, tmp.name

    _stop_fake_ws_loop(loop)


@pytest.fixture
def units_client(monkeypatch):
    """Flask test client mounting api_grow_units + a fake WS loop.

    Used by rotate_token / decommission / clear_photos tests.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_units.DB_FILE", tmp.name
    )
    monkeypatch.setattr("mlss_monitor.grow.health_watchdog.DB_FILE", tmp.name)
    init_db.create_db()
    _seed_unit(tmp.name, unit_id=1)

    _, loop = _start_fake_ws_loop(unit_id=1)

    from flask import Flask
    from mlss_monitor.routes.api_grow_units import api_grow_units_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_units_bp)
    tc = app.test_client()
    _set_session(tc, role="admin")

    yield tc, tmp.name

    _stop_fake_ws_loop(loop)


@pytest.fixture
def enroll_client(monkeypatch):
    """Flask test client mounting api_grow_enroll. No seed — enroll creates
    the unit. Pulls the seeded raw enrollment key for use in tests.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_enroll.DB_FILE", tmp.name
    )
    init_db.create_db()

    conn = sqlite3.connect(tmp.name)
    raw_key = conn.execute(
        "SELECT value FROM app_settings "
        "WHERE key='grow_enrollment_key_raw_pending_reveal'"
    ).fetchone()[0]
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_enroll import api_grow_enroll_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_enroll_bp)
    yield app.test_client(), raw_key, tmp.name


@pytest.fixture
def settings_client(monkeypatch):
    """Flask test client mounting api_grow_settings."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_settings.DB_FILE", tmp.name
    )
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    init_db.create_db()

    from flask import Flask
    from mlss_monitor.routes.api_grow_settings import api_grow_settings_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_settings_bp)
    tc = app.test_client()
    _set_session(tc, role="admin")

    yield tc, tmp.name


# ---------------------------------------------------------------------------
# api_grow_config.py — PUT /profile
# ---------------------------------------------------------------------------


def test_put_profile_enqueues_grow_units(config_client):
    c, db_path = config_client
    r = c.put("/api/grow/units/1/profile", json={"label": "Renamed"})
    assert r.status_code == 200, r.data
    assert ("grow_units", "1") in _outbox_rows(db_path, table="grow_units")


def test_put_profile_unknown_unit_does_not_enqueue(config_client):
    """A 404 must roll back without leaving an outbox pointer behind —
    otherwise the backup server would chase a row that never existed."""
    c, db_path = config_client
    r = c.put("/api/grow/units/99999/profile", json={"label": "X"})
    assert r.status_code == 404
    assert _outbox_rows(db_path, table="grow_units") == []


# ---------------------------------------------------------------------------
# api_grow_config.py — PUT /pid
# ---------------------------------------------------------------------------


def test_put_pid_enqueues_grow_units(config_client):
    c, db_path = config_client
    r = c.put("/api/grow/units/1/pid", json={"kp": 0.5})
    assert r.status_code == 200, r.data
    assert ("grow_units", "1") in _outbox_rows(db_path, table="grow_units")


def test_put_pid_deadband_only_does_not_enqueue(config_client):
    """deadband_pct has no override column, so the route's "verify exists +
    no-op" branch must NOT enqueue — there was no live write to mirror."""
    c, db_path = config_client
    r = c.put("/api/grow/units/1/pid", json={"deadband_pct": 5.0})
    assert r.status_code == 200
    assert _outbox_rows(db_path, table="grow_units") == []


# ---------------------------------------------------------------------------
# api_grow_config.py — PUT /calibration
# ---------------------------------------------------------------------------


def test_put_calibration_enqueues_grow_units(config_client):
    c, db_path = config_client
    r = c.put(
        "/api/grow/units/1/calibration",
        json={"dry_raw": 300, "wet_raw": 1500},
    )
    assert r.status_code == 200, r.data
    assert ("grow_units", "1") in _outbox_rows(db_path, table="grow_units")


# ---------------------------------------------------------------------------
# api_grow_config.py — PUT /photo_schedule
# ---------------------------------------------------------------------------


def test_put_photo_schedule_enqueues_grow_units(config_client):
    c, db_path = config_client
    r = c.put(
        "/api/grow/units/1/photo_schedule",
        data=json.dumps({"start_hour": 6, "end_hour": 22}),
        content_type="application/json",
    )
    assert r.status_code == 200, r.data
    assert ("grow_units", "1") in _outbox_rows(db_path, table="grow_units")


# ---------------------------------------------------------------------------
# api_grow_config.py — PUT /light_windows (strict-mirror)
# ---------------------------------------------------------------------------


def test_put_light_windows_enqueues_delete_scope_then_row_pointers(config_client):
    """The strict-mirror replace must enqueue:
      1. exactly one outbox_delete_scope with scope={"unit_id":1,"phase":"vegetative"},
      2. one outbox_changes row per inserted grow_light_windows row.
    """
    c, db_path = config_client
    r = c.put(
        "/api/grow/units/1/light_windows",
        json={
            "phase": "vegetative",
            "windows": [
                {"start": "06:00", "end": "10:00"},
                {"start": "14:00", "end": "20:00"},
            ],
        },
    )
    assert r.status_code == 200, r.data

    expected_scope = json.dumps({"phase": "vegetative", "unit_id": 1},
                                sort_keys=True)
    scopes = _delete_scope_rows(db_path, table="grow_light_windows")
    assert scopes == [("grow_light_windows", expected_scope)], scopes

    # Two new windows → two grow_light_windows row pointers. PKs are the
    # autoincrement ids handed out by SQLite — both should be present.
    conn = sqlite3.connect(db_path)
    try:
        lw_ids = [r[0] for r in conn.execute(
            "SELECT id FROM grow_light_windows "
            "WHERE unit_id=1 AND phase='vegetative' ORDER BY sort_order"
        )]
    finally:
        conn.close()
    assert len(lw_ids) == 2
    rows = _outbox_rows(db_path, table="grow_light_windows")
    for lw_id in lw_ids:
        assert ("grow_light_windows", str(lw_id)) in rows


def test_put_light_windows_empty_list_still_enqueues_delete_scope(config_client):
    """An empty windows list clears the (unit, phase) pair — the server side
    still needs a delete-scope marker so it knows to wipe its own copy."""
    c, db_path = config_client
    r = c.put(
        "/api/grow/units/1/light_windows",
        json={"phase": "vegetative", "windows": []},
    )
    assert r.status_code == 200, r.data
    expected_scope = json.dumps({"phase": "vegetative", "unit_id": 1},
                                sort_keys=True)
    assert _delete_scope_rows(db_path, table="grow_light_windows") == [
        ("grow_light_windows", expected_scope)
    ]
    # No INSERTs → no row pointers.
    assert _outbox_rows(db_path, table="grow_light_windows") == []


def test_put_light_windows_unknown_unit_does_not_enqueue(config_client):
    """The 404 path rolls back without leaving a delete-scope behind."""
    c, db_path = config_client
    r = c.put(
        "/api/grow/units/9999/light_windows",
        json={
            "phase": "vegetative",
            "windows": [{"start": "06:00", "end": "20:00"}],
        },
    )
    assert r.status_code == 404
    assert _delete_scope_rows(db_path, table="grow_light_windows") == []
    assert _outbox_rows(db_path, table="grow_light_windows") == []


# ---------------------------------------------------------------------------
# api_grow_config.py — POST /safety_override
# ---------------------------------------------------------------------------


def test_post_safety_override_enqueues_grow_errors_audit_row(config_client):
    c, db_path = config_client
    r = c.post(
        "/api/grow/units/1/safety_override",
        json={"action": "force_pump_on", "duration_s": 5},
    )
    assert r.status_code == 202, r.data

    # The route INSERTs into grow_errors after the push succeeds. The audit
    # row's PK is autoincremented — fetch it and match against the outbox.
    conn = sqlite3.connect(db_path)
    try:
        err_id = conn.execute(
            "SELECT id FROM grow_errors WHERE unit_id=1 "
            "AND kind='safety_override_invoked'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert ("grow_errors", str(err_id)) in _outbox_rows(
        db_path, table="grow_errors"
    )


def test_post_safety_override_503_path_does_not_enqueue(monkeypatch):
    """When the WS push fails, the safety_override route returns 503 BEFORE
    writing the audit row. No grow_errors row → no outbox entry."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_config.DB_FILE", tmp.name
    )
    init_db.create_db()
    _seed_unit(tmp.name, unit_id=1)

    # No WS registry / loop wired up → safety_override returns 503.
    from mlss_monitor import state
    state.grow_ws_registry = None
    state.grow_ws_loop = None

    from flask import Flask
    from mlss_monitor.routes.api_grow_config import api_grow_config_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_config_bp)
    tc = app.test_client()
    _set_session(tc, role="admin")

    r = tc.post(
        "/api/grow/units/1/safety_override",
        json={"action": "force_pump_on", "duration_s": 5},
    )
    assert r.status_code == 503
    assert _outbox_rows(tmp.name, table="grow_errors") == []


# ---------------------------------------------------------------------------
# api_grow_enroll.py — POST /api/grow/enroll
# ---------------------------------------------------------------------------


def test_enroll_new_unit_enqueues_grow_units(enroll_client):
    c, raw_key, db_path = enroll_client
    r = c.post("/api/grow/enroll", json={
        "enrollment_key": raw_key,
        "hardware_serial": "100000000c0a8014b",
        "plant": {"name": "Test Tomato", "type": "tomato", "medium": "soil"},
    })
    assert r.status_code == 201, r.data
    unit_id = r.get_json()["unit_id"]
    assert ("grow_units", str(unit_id)) in _outbox_rows(
        db_path, table="grow_units"
    )


def test_enroll_existing_unit_reenroll_enqueues_grow_units(enroll_client):
    """Re-enrolling the same hardware_serial UPDATEs the existing row
    rather than INSERTing — must still enqueue the row pointer."""
    c, raw_key, db_path = enroll_client
    r1 = c.post("/api/grow/enroll", json={
        "enrollment_key": raw_key,
        "hardware_serial": "100000000c0a8014b",
        "plant": {"name": "Tom", "type": "tomato"},
    })
    unit_id = r1.get_json()["unit_id"]

    # Clear the outbox so we isolate the re-enroll UPDATE.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM outbox_changes")
        conn.commit()
    finally:
        conn.close()

    r2 = c.post("/api/grow/enroll", json={
        "enrollment_key": raw_key,
        "hardware_serial": "100000000c0a8014b",
        "plant": {"name": "Tom"},
    })
    assert r2.status_code == 201
    assert r2.get_json()["unit_id"] == unit_id
    assert ("grow_units", str(unit_id)) in _outbox_rows(
        db_path, table="grow_units"
    )


# ---------------------------------------------------------------------------
# api_grow_units.py — POST /api/grow/units/<id>/rotate-token
# ---------------------------------------------------------------------------


def test_rotate_token_enqueues_grow_units(units_client):
    c, db_path = units_client
    r = c.post("/api/grow/units/1/rotate-token")
    assert r.status_code == 201, r.data
    assert ("grow_units", "1") in _outbox_rows(db_path, table="grow_units")


def test_rotate_token_unknown_unit_does_not_enqueue(units_client):
    c, db_path = units_client
    r = c.post("/api/grow/units/9999/rotate-token")
    assert r.status_code == 404
    assert _outbox_rows(db_path, table="grow_units") == []


# ---------------------------------------------------------------------------
# api_grow_units.py — DELETE /api/grow/units/<id> (soft-delete)
# ---------------------------------------------------------------------------


def test_decommission_enqueues_grow_units(units_client):
    c, db_path = units_client
    r = c.delete("/api/grow/units/1")
    assert r.status_code == 200, r.data
    assert ("grow_units", "1") in _outbox_rows(db_path, table="grow_units")


def test_decommission_unknown_unit_does_not_enqueue(units_client):
    c, db_path = units_client
    r = c.delete("/api/grow/units/9999")
    assert r.status_code == 404
    assert _outbox_rows(db_path, table="grow_units") == []


# ---------------------------------------------------------------------------
# api_grow_units.py — DELETE /api/grow/units/<id>/photos (append-mostly)
# ---------------------------------------------------------------------------


def test_clear_photos_does_not_enqueue_grow_photos(units_client):
    """grow_photos is append-mostly: the server keeps its archived copies
    even when the operator wipes the Pi. The DELETE must NOT enqueue a
    delete-scope or row pointer.
    """
    c, db_path = units_client
    # Seed two grow_photos rows so the DELETE actually has rows to wipe.
    # Distinct timestamps via timedelta — datetime.utcnow() resolution on
    # Windows is too coarse for back-to-back calls to differ, which would
    # trip the UNIQUE (unit_id, taken_at) constraint.
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_photos (unit_id, taken_at, file_path, "
        "width_px, height_px, size_bytes) VALUES (1, ?, ?, 100, 100, 9)",
        (now, "unit_001/2026-01-01/120000.jpg"),
    )
    conn.execute(
        "INSERT INTO grow_photos (unit_id, taken_at, file_path, "
        "width_px, height_px, size_bytes) VALUES (1, ?, ?, 100, 100, 9)",
        (now + timedelta(seconds=60), "unit_001/2026-01-01/120100.jpg"),
    )
    conn.commit()
    conn.close()

    r = c.delete("/api/grow/units/1/photos")
    assert r.status_code == 200, r.data

    # Append-mostly: no row pointers and no delete-scopes for grow_photos.
    assert _outbox_rows(db_path, table="grow_photos") == []
    assert _delete_scope_rows(db_path, table="grow_photos") == []


# ---------------------------------------------------------------------------
# api_grow_settings.py — PUT /api/grow/plant-profiles/<id>
# ---------------------------------------------------------------------------


def test_update_plant_profile_enqueues_grow_plant_profiles(settings_client):
    c, db_path = settings_client
    # init_db.create_db() seeds many profile rows — grab one to UPDATE.
    conn = sqlite3.connect(db_path)
    profile_id = conn.execute(
        "SELECT id FROM grow_plant_profiles "
        "WHERE plant_type='generic' AND phase='vegetative'"
    ).fetchone()[0]
    conn.close()

    r = c.put(
        f"/api/grow/plant-profiles/{profile_id}",
        json={"kp": 0.42, "notes": "tweaked"},
    )
    assert r.status_code == 200, r.data
    assert ("grow_plant_profiles", str(profile_id)) in _outbox_rows(
        db_path, table="grow_plant_profiles"
    )


def test_update_plant_profile_empty_body_does_not_enqueue(settings_client):
    """No fields → no UPDATE → no outbox entry. Idempotent no-op."""
    c, db_path = settings_client
    conn = sqlite3.connect(db_path)
    profile_id = conn.execute(
        "SELECT id FROM grow_plant_profiles "
        "WHERE plant_type='generic' AND phase='vegetative'"
    ).fetchone()[0]
    conn.close()

    r = c.put(f"/api/grow/plant-profiles/{profile_id}", json={})
    assert r.status_code == 200
    assert _outbox_rows(db_path, table="grow_plant_profiles") == []


def test_update_plant_profile_unknown_id_does_not_enqueue(settings_client):
    c, db_path = settings_client
    r = c.put("/api/grow/plant-profiles/99999", json={"kp": 0.42})
    assert r.status_code == 404
    assert _outbox_rows(db_path, table="grow_plant_profiles") == []
