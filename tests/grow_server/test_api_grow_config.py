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
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
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


# ---------------------------------------------------------------------------
# GET /api/grow/units/<id>/config — bearer-authenticated firmware pull endpoint
#
# Task 8 introduces this endpoint so a Plant Grow Unit firmware (which has
# no GitHub OAuth identity) can pull the latest overrides + calibration +
# light_windows after the server pushes a `config_changed` command. Auth
# uses the per-unit bearer token (same secret used to authenticate the WS
# upgrade) — NOT a session cookie.
#
# Plant-profile resolution: the route resolves null override fields against
# the seeded `grow_plant_profiles` row matching (plant_type, current_phase)
# BEFORE responding, so the firmware always sees concrete numbers rather
# than having to maintain its own profile table. This is the "easier path"
# documented in the Task 8 plan.
# ---------------------------------------------------------------------------


@pytest.fixture
def bearer_client(monkeypatch):
    """Mirror the `client` fixture but mint a real bearer token + hash so
    the bearer-auth flow can be exercised end-to-end. Yields
    (test_client, bearer_token_raw, db_path).
    """
    # Capture the PRODUCTION value of api_grow_ws.DB_FILE before any
    # mutation. Importing api_grow_ws now (pre-mutation) ensures the
    # module's snapshot is the production value. We hold this for an
    # explicit reset at teardown — monkeypatch's "restore to value at
    # patch time" isn't sufficient because earlier tests may have
    # imported api_grow_ws AFTER mutating init_db.DB_FILE, leaving
    # api_grow_ws.DB_FILE pointing at a now-deleted tempfile.
    from database.init_db import DB_FILE as _PROD_DB_FILE
    import mlss_monitor.routes.api_grow_ws as _api_grow_ws

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    # Use monkeypatch (not direct assignment) so init_db.DB_FILE is
    # restored at teardown — pollutes downstream tests otherwise.
    monkeypatch.setattr(init_db, "DB_FILE", tmp.name)
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    init_db.create_db()

    # Mint a real token + argon2 hash so _validate_bearer can verify it.
    from mlss_monitor.grow.auth import generate_token, hash_secret
    raw_token = generate_token()
    token_hash = hash_secret(raw_token)

    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, current_phase, plant_type, "
        "medium_type, watering_kp_override, soak_window_min_override, "
        "soil_dry_raw, soil_wet_raw) "
        "VALUES (1, 'hw1', 'Tom 1', ?, ?, ?, "
        "'vegetative', 'tomato', 'soil', 0.5, 60, 220, 1600)",
        (datetime.utcnow(), token_hash, datetime.utcnow()),
    )
    # Seed two light windows for vegetative phase.
    conn.execute(
        "INSERT INTO grow_light_windows "
        "(unit_id, phase, start_hh_mm, end_hh_mm, sort_order) "
        "VALUES (1, 'vegetative', '06:00', '12:00', 0)"
    )
    conn.execute(
        "INSERT INTO grow_light_windows "
        "(unit_id, phase, start_hh_mm, end_hh_mm, sort_order) "
        "VALUES (1, 'vegetative', '14:00', '20:00', 1)"
    )
    conn.commit()
    conn.close()

    # Patch the route's DB_FILE — same pattern as the parent fixture.
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_config.DB_FILE", tmp.name
    )
    # _validate_bearer (in api_grow_ws.py) imported DB_FILE at module load
    # time; patch its module-local copy so the bearer lookup hits the test
    # DB rather than the production path.
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_ws.DB_FILE", tmp.name
    )
    # ws_registry/loop irrelevant for the GET path — null is fine.
    from mlss_monitor import state
    state.grow_ws_registry = None
    state.grow_ws_loop = None

    # Drop any cached bearer-validations from earlier tests so a fresh
    # token doesn't get rejected by a stale cache entry.
    from mlss_monitor.routes.api_grow_ws import _clear_auth_cache
    _clear_auth_cache()

    from flask import Flask
    from mlss_monitor.routes.api_grow_config import api_grow_config_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_config_bp)
    yield app.test_client(), raw_token, tmp.name

    # Teardown:
    # 1. Drop the bearer-cache so the next test that mints a new token
    #    under a previously-cached unit_id doesn't fail on a stale entry.
    _clear_auth_cache()
    # 2. Force-reset api_grow_ws.DB_FILE to the production default. See
    #    fixture preamble — monkeypatch can't reliably restore this on
    #    its own because the snapshot at patch-time may itself be a
    #    tempfile leaked from an earlier fixture.
    _api_grow_ws.DB_FILE = _PROD_DB_FILE


def test_get_unit_config_returns_overrides_calibration_light_windows(bearer_client):
    """Happy path: with a valid bearer header, the endpoint returns the
    five top-level keys and the override values from the DB."""
    c, token, _ = bearer_client
    r = c.get(
        "/api/grow/units/1/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.data
    body = r.get_json()
    # Required top-level keys.
    assert "overrides" in body
    assert "calibration" in body
    assert "light_windows" in body
    assert body["current_phase"] == "vegetative"
    assert body["plant_type"] == "tomato"

    # Overrides came from the seeded grow_units row. Resolved numbers (not
    # null) — null fields are filled from grow_plant_profiles defaults.
    overrides = body["overrides"]
    assert overrides["kp"] == 0.5                    # explicit override
    assert overrides["soak_window_min"] == 60        # explicit override
    # ki/kd had no override → resolved to seeded tomato/vegetative defaults
    # (ki=0, kd=0 per _SHIPPED_PROFILES).
    assert overrides["ki"] == 0
    assert overrides["kd"] == 0
    # watering_target had no override → resolved to 55 (tomato/vegetative).
    assert overrides["watering_target"] == 55

    # Calibration: explicit raw values from the seeded row.
    assert body["calibration"]["dry_raw"] == 220
    assert body["calibration"]["wet_raw"] == 1600

    # Light windows: dict keyed by phase, list of {start, end} per phase.
    assert "vegetative" in body["light_windows"]
    veg = body["light_windows"]["vegetative"]
    assert len(veg) == 2
    assert veg[0]["start"] == "06:00"
    assert veg[1]["end"] == "20:00"


def test_get_unit_config_resolves_plant_profile_defaults_for_null_overrides(
    monkeypatch
):
    """When override fields are NULL, the response substitutes values from
    grow_plant_profiles for (plant_type, current_phase). The firmware can
    then apply the result directly without its own profile lookup table."""
    import mlss_monitor.routes.api_grow_ws  # noqa: F401  pylint: disable=unused-import  # warm-up; see fixture comment
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    monkeypatch.setattr(init_db, "DB_FILE", tmp.name)
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    init_db.create_db()

    from mlss_monitor.grow.auth import generate_token, hash_secret
    raw_token = generate_token()
    conn = sqlite3.connect(tmp.name)
    # Unit with NO overrides set — every override column is NULL. The
    # response should fall back to plant_profile (basil/vegetative).
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, current_phase, plant_type) "
        "VALUES (1, 'hw1', 'Basil 1', ?, ?, ?, 'vegetative', 'basil')",
        (datetime.utcnow(), hash_secret(raw_token), datetime.utcnow()),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_config.DB_FILE", tmp.name
    )
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_ws.DB_FILE", tmp.name
    )
    from mlss_monitor.routes.api_grow_ws import _clear_auth_cache
    _clear_auth_cache()

    from flask import Flask
    from mlss_monitor.routes.api_grow_config import api_grow_config_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_config_bp)
    tc = app.test_client()

    r = tc.get(
        "/api/grow/units/1/config",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert r.status_code == 200, r.data
    body = r.get_json()
    overrides = body["overrides"]
    # basil/vegetative profile: target=60, kp=0.4, ki=0, kd=0,
    # min_pulse=2, max_pulse=6, soak=30
    assert overrides["watering_target"] == 60
    assert overrides["kp"] == 0.4
    assert overrides["min_pulse_s"] == 2
    assert overrides["max_pulse_s"] == 6
    assert overrides["soak_window_min"] == 30


def test_get_unit_config_requires_bearer_token(bearer_client):
    """No Authorization header → 401 missing_bearer."""
    c, _, _ = bearer_client
    r = c.get("/api/grow/units/1/config")
    assert r.status_code == 401
    body = r.get_json()
    assert body["error"] == "missing_bearer"


def test_get_unit_config_invalid_bearer_returns_401(bearer_client):
    """Bearer header present but token doesn't match the unit → 401."""
    c, _, _ = bearer_client
    r = c.get(
        "/api/grow/units/1/config",
        headers={"Authorization": "Bearer not-the-real-token-just-43-chars-padded-x"},
    )
    assert r.status_code == 401
    body = r.get_json()
    assert body["error"] == "invalid_token"


def test_get_unit_config_wrong_unit_id_returns_401(bearer_client):
    """A valid token for unit 1 must NOT validate against unit 99 — the
    cache key is (unit_id, token), so cross-unit reuse fails verification.
    """
    c, token, _ = bearer_client
    r = c.get(
        "/api/grow/units/99/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


def test_get_unit_config_works_without_session(bearer_client):
    """Proves the endpoint is reachable without a Flask session (firmware
    only ever has its bearer token, no session cookie)."""
    c, token, _ = bearer_client
    # Explicitly do NOT call session_transaction — no logged_in flag set.
    r = c.get(
        "/api/grow/units/1/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, (
        f"GET /config must work without a session (firmware has none); "
        f"got status={r.status_code}, body={r.data!r}"
    )


def test_get_unit_config_endpoint_is_in_public_endpoints():
    """Anchor the public-endpoints set so a future renamer flags here too."""
    from mlss_monitor.app import _PUBLIC_ENDPOINTS
    assert "api_grow_config.get_unit_config" in _PUBLIC_ENDPOINTS


def test_get_unit_config_includes_holiday_mode(bearer_client):
    """The firmware reads holiday_mode from the GET /config response and
    short-circuits pump pulses when True. Default seed value is OFF.
    """
    c, token, db_path = bearer_client
    # Default seed has grow_holiday_mode='0' — so holiday_mode is False.
    r = c.get(
        "/api/grow/units/1/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert "holiday_mode" in body
    assert body["holiday_mode"] is False

    # Flip the flag and re-pull
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
        ("grow_holiday_mode", "1"),
    )
    conn.commit()
    conn.close()
    r = c.get(
        "/api/grow/units/1/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    body = r.get_json()
    assert body["holiday_mode"] is True


# ---------------------------------------------------------------------------
# C2: GET /config now surfaces buffer_retention_days from the per-unit row.
# Firmware reads it and applies on every reconnect-pull via the
# buffer_retention_days_provider closure. NULL means "use the firmware
# default" (currently 7 days, mirroring grow_default_buffer_retention_days).
# ---------------------------------------------------------------------------


def test_get_unit_config_buffer_retention_days_null_when_not_set(bearer_client):
    """Default — no per-unit override — surfaces NULL so the firmware
    falls back to its built-in default (mirrors the
    grow_default_buffer_retention_days app_setting).
    """
    c, token, _ = bearer_client
    r = c.get(
        "/api/grow/units/1/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert "buffer_retention_days" in body
    assert body["buffer_retention_days"] is None


def test_get_unit_config_includes_buffer_retention_days_when_set(bearer_client):
    """When the unit row has a buffer_retention_days override, the GET
    response surfaces it as an integer for the firmware to apply on
    reconnect.
    """
    c, token, db_path = bearer_client
    # Set a per-unit override.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE grow_units SET buffer_retention_days=14 WHERE id=1"
    )
    conn.commit()
    conn.close()
    r = c.get(
        "/api/grow/units/1/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["buffer_retention_days"] == 14


# ---------------------------------------------------------------------------
# _PID_FIELDS registry shape — pins the consolidated registry that replaced
# the four parallel _PID_* dicts. Adding a future PID field is one tuple in
# _PID_FIELDS instead of four scattered entries; these tests anchor that
# shape so a future contributor sees a clear failure if they break it.
# ---------------------------------------------------------------------------


def test_pid_field_registry_has_all_expected_fields():
    """Pins the 7 PID-tunable fields and their declaration order.

    Order matters: _resolve_overrides outputs in _PID_FIELDS order and the
    firmware expects that canonical order in UnitConfig.overrides.
    """
    from mlss_monitor.routes.api_grow_config import _PID_FIELDS
    assert len(_PID_FIELDS) == 7
    response_keys = [f.response_key for f in _PID_FIELDS]
    assert response_keys == [
        "watering_target",
        "kp",
        "ki",
        "kd",
        "soak_window_min",
        "min_pulse_s",
        "max_pulse_s",
    ]


def test_pid_field_registry_response_keys_unique():
    """Two PID fields with the same response_key would silently overwrite
    each other in the firmware response — pin uniqueness so a typo in
    _PID_FIELDS surfaces here rather than at runtime."""
    from mlss_monitor.routes.api_grow_config import _PID_FIELDS
    keys = [f.response_key for f in _PID_FIELDS]
    assert len(keys) == len(set(keys))


def test_pid_field_registry_override_columns_unique():
    """Two PID fields mapped to the same override column would mean a
    PUT to one would clobber the other. Pin uniqueness defensively."""
    from mlss_monitor.routes.api_grow_config import _PID_FIELDS
    cols = [f.override_column for f in _PID_FIELDS]
    assert len(cols) == len(set(cols))


def test_pid_field_registry_profile_columns_unique():
    """The profile column is read on the GET-side fallback. Two fields
    pointing at the same profile column would resolve to identical values
    when both are NULL on the unit row — almost certainly a registry bug."""
    from mlss_monitor.routes.api_grow_config import _PID_FIELDS
    cols = [f.profile_column for f in _PID_FIELDS]
    assert len(cols) == len(set(cols))


# ---------------------------------------------------------------------------
# /photo_schedule (Phase 4 polish)
# ---------------------------------------------------------------------------


def test_put_photo_schedule_writes_both_columns(client):
    """admin PUT with explicit window → both columns set."""
    c, _fake_ws, db_path = client
    r = c.put(
        "/api/grow/units/1/photo_schedule",
        data=json.dumps({"start_hour": 6, "end_hour": 22}),
        content_type="application/json",
    )
    assert r.status_code == 200, r.data
    row = _row(db_path, 1)
    assert row["photo_active_start_hour"] == 6
    assert row["photo_active_end_hour"] == 22


def test_put_photo_schedule_24x7_clears_columns(client):
    """Both null → both columns set to NULL (24/7 capture)."""
    c, _ws, db_path = client
    # First set a window
    c.put(
        "/api/grow/units/1/photo_schedule",
        data=json.dumps({"start_hour": 6, "end_hour": 22}),
        content_type="application/json",
    )
    # Then clear it
    r = c.put(
        "/api/grow/units/1/photo_schedule",
        data=json.dumps({"start_hour": None, "end_hour": None}),
        content_type="application/json",
    )
    assert r.status_code == 200, r.data
    row = _row(db_path, 1)
    assert row["photo_active_start_hour"] is None
    assert row["photo_active_end_hour"] is None


def test_put_photo_schedule_wrap_midnight_accepted(client):
    """22..6 (overnight capture) is a valid window — server stores it."""
    c, _ws, db_path = client
    r = c.put(
        "/api/grow/units/1/photo_schedule",
        data=json.dumps({"start_hour": 22, "end_hour": 6}),
        content_type="application/json",
    )
    assert r.status_code == 200
    row = _row(db_path, 1)
    assert row["photo_active_start_hour"] == 22
    assert row["photo_active_end_hour"] == 6


def test_put_photo_schedule_only_one_set_400(client):
    c, _ws, _db_path = client
    r = c.put(
        "/api/grow/units/1/photo_schedule",
        data=json.dumps({"start_hour": 6, "end_hour": None}),
        content_type="application/json",
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_payload"


def test_put_photo_schedule_equal_hours_400(client):
    c, _ws, _db_path = client
    r = c.put(
        "/api/grow/units/1/photo_schedule",
        data=json.dumps({"start_hour": 12, "end_hour": 12}),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_put_photo_schedule_out_of_range_400(client):
    c, _ws, _db_path = client
    r = c.put(
        "/api/grow/units/1/photo_schedule",
        data=json.dumps({"start_hour": 6, "end_hour": 24}),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_put_photo_schedule_404_unknown_unit(client):
    c, _ws, _db_path = client
    r = c.put(
        "/api/grow/units/9999/photo_schedule",
        data=json.dumps({"start_hour": 6, "end_hour": 22}),
        content_type="application/json",
    )
    assert r.status_code == 404


def test_put_photo_schedule_pushes_config_changed(client):
    """Successful PUT should fire a best-effort config_changed WS push
    with section='photo_schedule' (mirrors how /pid + /profile work)."""
    c, fake_ws, _db_path = client
    r = c.put(
        "/api/grow/units/1/photo_schedule",
        data=json.dumps({"start_hour": 6, "end_hour": 22}),
        content_type="application/json",
    )
    assert r.status_code == 200
    # Listener loop is async-threadsafe-scheduled; give it a moment
    import time
    for _ in range(20):
        if fake_ws.sent:
            break
        time.sleep(0.05)
    assert fake_ws.sent, "config_changed should be pushed"
    cmd = json.loads(fake_ws.sent[-1])
    assert cmd["payload"]["kind"] == "config_changed"
    assert cmd["payload"]["section"] == "photo_schedule"


def test_get_unit_config_includes_photo_active_hours(client, monkeypatch):
    """Firmware GET /config response must include photo_active_hours so
    config_sync can populate LoopConfig.photo_active_hours."""
    c, _ws, _db_path = client
    # Set a window on the unit
    c.put(
        "/api/grow/units/1/photo_schedule",
        data=json.dumps({"start_hour": 6, "end_hour": 22}),
        content_type="application/json",
    )
    # Bearer auth: the bearer hash on the unit row is just 'h' from the
    # fixture (not a real argon2 hash), so we monkeypatch _validate_bearer
    # to accept anything for this single-purpose test.
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_config._validate_bearer",
        lambda uid, tok: True,
    )
    r = c.get(
        "/api/grow/units/1/config",
        headers={"Authorization": "Bearer fake"},
    )
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["photo_active_hours"] == [6, 22]


def test_get_unit_config_photo_active_hours_null_for_24x7(client, monkeypatch):
    """When both columns are NULL, the response field is null (firmware
    sees None → keeps its default 24/7 behaviour)."""
    c, _ws, _db_path = client
    # Default unit has both NULL — confirm response is null
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_config._validate_bearer",
        lambda uid, tok: True,
    )
    r = c.get(
        "/api/grow/units/1/config",
        headers={"Authorization": "Bearer fake"},
    )
    assert r.status_code == 200
    assert r.get_json()["photo_active_hours"] is None
