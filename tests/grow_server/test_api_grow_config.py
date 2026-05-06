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


# ---------------------------------------------------------------------------
# /light_windows happy path + edge cases (Task 3)
# ---------------------------------------------------------------------------


def _seed_window(db_path, unit_id, phase, start, end, sort_order):
    """Seed a row directly into grow_light_windows for setup."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_light_windows "
        "(unit_id, phase, start_hh_mm, end_hh_mm, sort_order) "
        "VALUES (?, ?, ?, ?, ?)",
        (unit_id, phase, start, end, sort_order),
    )
    conn.commit()
    conn.close()


def _windows_for(db_path, unit_id, phase):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM grow_light_windows "
        "WHERE unit_id=? AND phase=? ORDER BY sort_order",
        (unit_id, phase),
    ).fetchall()
    conn.close()
    return rows


def test_put_light_windows_inserts_windows(client):
    c, _, db_path = client
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
    assert r.get_json() == {"ok": True}
    rows = _windows_for(db_path, 1, "vegetative")
    assert len(rows) == 2
    assert rows[0]["start_hh_mm"] == "06:00"
    assert rows[0]["end_hh_mm"] == "10:00"
    assert rows[0]["sort_order"] == 0
    assert rows[1]["start_hh_mm"] == "14:00"
    assert rows[1]["end_hh_mm"] == "20:00"
    assert rows[1]["sort_order"] == 1


def test_put_light_windows_replaces_existing(client):
    c, _, db_path = client
    # Seed 3 existing windows for vegetative.
    _seed_window(db_path, 1, "vegetative", "06:00", "08:00", 0)
    _seed_window(db_path, 1, "vegetative", "10:00", "12:00", 1)
    _seed_window(db_path, 1, "vegetative", "14:00", "16:00", 2)

    r = c.put(
        "/api/grow/units/1/light_windows",
        json={
            "phase": "vegetative",
            "windows": [{"start": "07:00", "end": "21:00"}],
        },
    )
    assert r.status_code == 200, r.data
    rows = _windows_for(db_path, 1, "vegetative")
    assert len(rows) == 1
    assert rows[0]["start_hh_mm"] == "07:00"
    assert rows[0]["end_hh_mm"] == "21:00"


def test_put_light_windows_does_not_touch_other_phases(client):
    c, _, db_path = client
    # Seed both vegetative AND flowering.
    _seed_window(db_path, 1, "vegetative", "06:00", "08:00", 0)
    _seed_window(db_path, 1, "vegetative", "10:00", "12:00", 1)
    _seed_window(db_path, 1, "flowering", "07:00", "19:00", 0)
    _seed_window(db_path, 1, "flowering", "20:00", "22:00", 1)

    # PUT new vegetative-only windows.
    r = c.put(
        "/api/grow/units/1/light_windows",
        json={
            "phase": "vegetative",
            "windows": [{"start": "09:00", "end": "17:00"}],
        },
    )
    assert r.status_code == 200, r.data

    # Vegetative replaced.
    veg_rows = _windows_for(db_path, 1, "vegetative")
    assert len(veg_rows) == 1
    assert veg_rows[0]["start_hh_mm"] == "09:00"

    # Flowering untouched.
    flower_rows = _windows_for(db_path, 1, "flowering")
    assert len(flower_rows) == 2
    assert flower_rows[0]["start_hh_mm"] == "07:00"
    assert flower_rows[0]["end_hh_mm"] == "19:00"
    assert flower_rows[1]["start_hh_mm"] == "20:00"
    assert flower_rows[1]["end_hh_mm"] == "22:00"


def test_put_light_windows_clears_when_empty_list(client):
    c, _, db_path = client
    # Seed 2 windows.
    _seed_window(db_path, 1, "vegetative", "06:00", "08:00", 0)
    _seed_window(db_path, 1, "vegetative", "10:00", "12:00", 1)

    r = c.put(
        "/api/grow/units/1/light_windows",
        json={"phase": "vegetative", "windows": []},
    )
    assert r.status_code == 200, r.data
    rows = _windows_for(db_path, 1, "vegetative")
    assert len(rows) == 0


def test_put_light_windows_validates_hhmm(client):
    c, _, _ = client
    r = c.put(
        "/api/grow/units/1/light_windows",
        json={
            "phase": "vegetative",
            "windows": [{"start": "6am", "end": "10pm"}],
        },
    )
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "invalid_payload"
    assert "detail" in body


def test_put_light_windows_rejects_zero_length(client):
    c, _, _ = client
    r = c.put(
        "/api/grow/units/1/light_windows",
        json={
            "phase": "vegetative",
            "windows": [{"start": "06:00", "end": "06:00"}],
        },
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_payload"


def test_put_light_windows_returns_404_for_unknown_unit(client):
    c, _, _ = client
    r = c.put(
        "/api/grow/units/99999/light_windows",
        json={
            "phase": "vegetative",
            "windows": [{"start": "06:00", "end": "20:00"}],
        },
    )
    assert r.status_code == 404
    assert r.get_json()["error"] == "unit_not_found"


def test_put_light_windows_pushes_config_changed_via_ws(client):
    c, fake_ws, _ = client
    r = c.put(
        "/api/grow/units/1/light_windows",
        json={
            "phase": "vegetative",
            "windows": [{"start": "06:00", "end": "20:00"}],
        },
    )
    assert r.status_code == 200
    assert len(fake_ws.sent) == 1
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["type"] == "command"
    assert cmd["payload"]["kind"] == "config_changed"
    assert cmd["payload"]["section"] == "light_windows"


def test_put_light_windows_caps_at_8_windows(client):
    c, _, _ = client
    # 9 windows — exceeds max_length=8 from LightWindowsUpdate.
    nine = [
        {"start": f"{h:02d}:00", "end": f"{h:02d}:30"}
        for h in range(9)
    ]
    r = c.put(
        "/api/grow/units/1/light_windows",
        json={"phase": "vegetative", "windows": nine},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_payload"


def test_put_light_windows_rejects_bad_phase(client):
    c, _, _ = client
    r = c.put(
        "/api/grow/units/1/light_windows",
        json={
            "phase": "winter",
            "windows": [{"start": "06:00", "end": "20:00"}],
        },
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_payload"


# ---------------------------------------------------------------------------
# /calibration happy path + edge cases (Task 4)
# ---------------------------------------------------------------------------


def test_put_calibration_writes_dry_and_wet_raw(client):
    c, _, db_path = client
    r = c.put(
        "/api/grow/units/1/calibration",
        json={"dry_raw": 300, "wet_raw": 1500},
    )
    assert r.status_code == 200, r.data
    assert r.get_json() == {"ok": True}
    row = _row(db_path, 1)
    assert row["soil_dry_raw"] == 300
    assert row["soil_wet_raw"] == 1500


def test_put_calibration_rejects_dry_ge_wet(client):
    c, _, _ = client
    r = c.put(
        "/api/grow/units/1/calibration",
        json={"dry_raw": 1500, "wet_raw": 300},
    )
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "invalid_payload"
    assert "detail" in body


def test_put_calibration_rejects_out_of_range(client):
    c, _, _ = client
    r = c.put(
        "/api/grow/units/1/calibration",
        json={"dry_raw": -1, "wet_raw": 5000},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_payload"


def test_put_calibration_returns_404_for_unknown_unit(client):
    c, _, _ = client
    r = c.put(
        "/api/grow/units/99999/calibration",
        json={"dry_raw": 300, "wet_raw": 1500},
    )
    assert r.status_code == 404
    assert r.get_json()["error"] == "unit_not_found"


def test_put_calibration_pushes_config_changed_via_ws(client):
    c, fake_ws, _ = client
    r = c.put(
        "/api/grow/units/1/calibration",
        json={"dry_raw": 300, "wet_raw": 1500},
    )
    assert r.status_code == 200
    assert len(fake_ws.sent) == 1
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["type"] == "command"
    assert cmd["payload"]["kind"] == "config_changed"
    assert cmd["payload"]["section"] == "calibration"


def test_put_calibration_succeeds_when_unit_not_connected(monkeypatch):
    """Best-effort: calibration write is durable even if the unit is offline."""
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

    r = tc.put(
        "/api/grow/units/1/calibration",
        json={"dry_raw": 250, "wet_raw": 1700},
    )
    assert r.status_code == 200
    # DB was still updated even though no WS push was possible.
    conn = sqlite3.connect(tmp.name)
    row = conn.execute(
        "SELECT soil_dry_raw, soil_wet_raw FROM grow_units WHERE id=1"
    ).fetchone()
    conn.close()
    assert row[0] == 250
    assert row[1] == 1700


# ---------------------------------------------------------------------------
# /safety_override happy path + edge cases (Task 4, admin-only)
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_user_client(client):
    """Client with an explicit `user` in session for triggered_by audit."""
    c, fake_ws, db_path = client
    with c.session_transaction() as sess:
        sess["user"] = "test-admin"
        # role already 'admin' from the parent fixture
    return c, fake_ws, db_path


def test_post_safety_override_returns_202_on_successful_push(admin_user_client):
    c, _, _ = admin_user_client
    r = c.post(
        "/api/grow/units/1/safety_override",
        json={
            "action": "force_pump_on",
            "duration_s": 10,
            "acknowledged_warnings": ["wont_overwater"],
        },
    )
    assert r.status_code == 202, r.data
    assert r.get_json() == {"ok": True}


def test_post_safety_override_returns_503_when_unit_not_connected(monkeypatch):
    """If the listener loop/registry isn't wired up (e.g. unit not online),
    safety_override returns 503 — unlike best-effort config_changed pushes,
    a safety override is intent-to-act-now and a missed push is a real fail.
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
        sess["user"] = "test-admin"

    r = tc.post(
        "/api/grow/units/1/safety_override",
        json={"action": "force_pump_on", "duration_s": 5},
    )
    assert r.status_code == 503
    body = r.get_json()
    assert "error" in body
    # Audit row must NOT be written when the push fails (safety: action didn't
    # actually happen, so no audit trail entry).
    conn = sqlite3.connect(tmp.name)
    row_count = conn.execute(
        "SELECT COUNT(*) FROM grow_errors WHERE unit_id=1"
    ).fetchone()[0]
    conn.close()
    assert row_count == 0


def test_post_safety_override_pushes_command_payload_with_action_and_duration(
    admin_user_client,
):
    c, fake_ws, _ = admin_user_client
    r = c.post(
        "/api/grow/units/1/safety_override",
        json={
            "action": "force_pump_on",
            "duration_s": 10,
            "acknowledged_warnings": ["wont_overwater"],
        },
    )
    assert r.status_code == 202, r.data
    assert len(fake_ws.sent) == 1
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["type"] == "command"
    assert "ts" in cmd
    payload = cmd["payload"]
    assert payload["kind"] == "safety_override"
    assert payload["action"] == "force_pump_on"
    assert payload["duration_s"] == 10


def test_post_safety_override_records_audit_row_in_grow_errors(
    admin_user_client,
):
    c, _, db_path = admin_user_client
    r = c.post(
        "/api/grow/units/1/safety_override",
        json={
            "action": "force_pump_on",
            "duration_s": 10,
            "acknowledged_warnings": ["wont_overwater", "user_present"],
        },
    )
    assert r.status_code == 202, r.data

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM grow_errors WHERE unit_id=1 AND kind='safety_override_invoked'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    row = rows[0]
    assert row["severity"] == "info"
    details = json.loads(row["details_json"])
    assert details["action"] == "force_pump_on"
    assert details["duration_s"] == 10
    assert details["acknowledged_warnings"] == ["wont_overwater", "user_present"]


def test_post_safety_override_rejects_excessive_duration(admin_user_client):
    c, _, _ = admin_user_client
    r = c.post(
        "/api/grow/units/1/safety_override",
        json={"action": "force_pump_on", "duration_s": 600},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_payload"


def test_post_safety_override_rejects_unknown_action(admin_user_client):
    c, _, _ = admin_user_client
    r = c.post(
        "/api/grow/units/1/safety_override",
        json={"action": "nuke_plant", "duration_s": 10},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_payload"


def test_post_safety_override_returns_404_for_unknown_unit(admin_user_client):
    c, _, _ = admin_user_client
    r = c.post(
        "/api/grow/units/99999/safety_override",
        json={"action": "force_pump_on", "duration_s": 10},
    )
    assert r.status_code == 404
    assert r.get_json()["error"] == "unit_not_found"


def test_post_safety_override_audit_row_records_user(admin_user_client):
    c, _, db_path = admin_user_client
    r = c.post(
        "/api/grow/units/1/safety_override",
        json={"action": "force_pump_on", "duration_s": 10},
    )
    assert r.status_code == 202, r.data

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT details_json FROM grow_errors "
        "WHERE unit_id=1 AND kind='safety_override_invoked'"
    ).fetchone()
    conn.close()
    assert row is not None
    details = json.loads(row[0])
    assert details["triggered_by"] == "test-admin"
