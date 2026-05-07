"""Tests for the Settings → Grow blueprint (api_grow_settings).

Three sections, mirroring the three sub-tasks of Phase 2 Task 2:
  * 2a — POST /api/grow/enrollment-key/rotate  (admin)
  * 2b — GET/PUT /api/grow/plant-profiles      (controller+admin / admin)
  * 2c — GET/PUT /api/grow/settings/holiday-mode

The fixture mounts only the settings blueprint plus the dist blueprint so
the rotation tests can also exercise peek-once consumption end-to-end
(rotate → peek → rotate again → new key shows up).
"""
import sqlite3
import tempfile

import pytest


def _set_session(c, *, logged_in=True, role="admin"):
    with c.session_transaction() as sess:
        sess["logged_in"] = logged_in
        sess["user_role"] = role


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.routes.api_grow_settings.DB_FILE", tmp.name)
    monkeypatch.setattr("mlss_monitor.routes.api_grow_dist.DB_FILE", tmp.name)
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    init_db.create_db()

    from flask import Flask
    from mlss_monitor.routes.api_grow_settings import api_grow_settings_bp
    from mlss_monitor.routes.api_grow_dist import api_grow_dist_bp

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_settings_bp)
    app.register_blueprint(api_grow_dist_bp)
    return app.test_client(), tmp.name


# ---------------------------------------------------------------------------
# 2a. Enrollment key rotation
# ---------------------------------------------------------------------------


def test_rotate_enrollment_key_replaces_hash_and_returns_raw(client):
    c, db_path = client
    _set_session(c, role="admin")

    # Snapshot the original argon2 hash before rotation.
    conn = sqlite3.connect(db_path)
    orig_hash = conn.execute(
        "SELECT value FROM app_settings WHERE key='grow_enrollment_key_hash'"
    ).fetchone()[0]
    conn.close()

    r = c.post("/api/grow/enrollment-key/rotate")
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert "key" in body
    assert isinstance(body["key"], str) and len(body["key"]) >= 30

    conn = sqlite3.connect(db_path)
    new_hash = conn.execute(
        "SELECT value FROM app_settings WHERE key='grow_enrollment_key_hash'"
    ).fetchone()[0]
    new_raw = conn.execute(
        "SELECT value FROM app_settings "
        "WHERE key='grow_enrollment_key_raw_pending_reveal'"
    ).fetchone()[0]
    conn.close()
    assert new_hash != orig_hash, "hash must be rotated"
    assert new_raw == body["key"], "raw stash matches response key"


def test_rotate_enrollment_key_invalidates_old_key(client):
    """The old key (whatever it was before rotation) must no longer verify
    against the new hash. We rotate, capture the new key, rotate again,
    and assert the captured key from rotation #1 fails verification.
    """
    from mlss_monitor.grow.auth import verify_enrollment_key
    c, _ = client
    _set_session(c, role="admin")

    r1 = c.post("/api/grow/enrollment-key/rotate")
    first_key = r1.get_json()["key"]
    assert verify_enrollment_key(first_key) is True

    r2 = c.post("/api/grow/enrollment-key/rotate")
    second_key = r2.get_json()["key"]
    assert second_key != first_key
    # Old key no longer verifies
    assert verify_enrollment_key(first_key) is False
    assert verify_enrollment_key(second_key) is True


def test_rotate_enrollment_key_admin_only_anonymous(client):
    c, _ = client
    _set_session(c, logged_in=False, role="viewer")
    r = c.post("/api/grow/enrollment-key/rotate")
    assert r.status_code == 401


def test_rotate_enrollment_key_admin_only_viewer(client):
    c, _ = client
    _set_session(c, logged_in=True, role="viewer")
    r = c.post("/api/grow/enrollment-key/rotate")
    assert r.status_code == 403


def test_rotate_enrollment_key_admin_only_controller(client):
    c, _ = client
    _set_session(c, logged_in=True, role="controller")
    r = c.post("/api/grow/enrollment-key/rotate")
    assert r.status_code == 403


def test_rotate_enrollment_key_after_reveal_consumed(client):
    """Rotate, consume the reveal via peek-once, rotate again — second
    rotation must produce a fresh raw stash even though peek-once had
    deleted the previous one.
    """
    c, db_path = client
    _set_session(c, role="admin")
    r1 = c.post("/api/grow/enrollment-key/rotate")
    first_key = r1.get_json()["key"]

    # Consume the reveal (uses the existing peek-once endpoint)
    rp = c.get("/api/grow/enrollment-key/peek-once")
    assert rp.status_code == 200
    assert rp.get_json()["key"] == first_key

    # raw stash gone
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT value FROM app_settings "
        "WHERE key='grow_enrollment_key_raw_pending_reveal'"
    ).fetchone()
    conn.close()
    assert row is None or not row[0]

    # Rotate again — fresh raw should appear
    r2 = c.post("/api/grow/enrollment-key/rotate")
    assert r2.status_code == 201
    second_key = r2.get_json()["key"]
    assert second_key != first_key

    conn = sqlite3.connect(db_path)
    new_raw = conn.execute(
        "SELECT value FROM app_settings "
        "WHERE key='grow_enrollment_key_raw_pending_reveal'"
    ).fetchone()[0]
    conn.close()
    assert new_raw == second_key


# ---------------------------------------------------------------------------
# 2b. Plant profiles editor
# ---------------------------------------------------------------------------


def test_list_plant_profiles_returns_all_seeded(client):
    c, _ = client
    _set_session(c, role="admin")
    r = c.get("/api/grow/plant-profiles")
    assert r.status_code == 200
    rows = r.get_json()
    assert len(rows) == 11, f"expected 11 shipped profiles, got {len(rows)}"
    # Each row has every field the editor needs
    expected_keys = {
        "id", "plant_type", "phase", "target_moisture_pct", "deadband_pct",
        "kp", "ki", "kd", "min_pulse_s", "max_pulse_s", "soak_window_min",
        "default_light_hours", "is_shipped", "notes",
    }
    for row in rows:
        assert expected_keys.issubset(row.keys())
        assert row["is_shipped"] == 1
    # And the seeded combos are present
    combos = {(r["plant_type"], r["phase"]) for r in rows}
    assert ("tomato", "vegetative") in combos
    assert ("generic", "seedling") in combos


def test_list_plant_profiles_requires_controller_or_admin(client):
    c, _ = client
    _set_session(c, logged_in=False, role="viewer")
    assert c.get("/api/grow/plant-profiles").status_code == 401
    _set_session(c, logged_in=True, role="viewer")
    assert c.get("/api/grow/plant-profiles").status_code == 403
    _set_session(c, logged_in=True, role="controller")
    assert c.get("/api/grow/plant-profiles").status_code == 200
    _set_session(c, logged_in=True, role="admin")
    assert c.get("/api/grow/plant-profiles").status_code == 200


def _profile_id(db_path, plant_type, phase):
    conn = sqlite3.connect(db_path)
    pid = conn.execute(
        "SELECT id FROM grow_plant_profiles WHERE plant_type=? AND phase=?",
        (plant_type, phase),
    ).fetchone()[0]
    conn.close()
    return pid


def _profile_row(db_path, profile_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM grow_plant_profiles WHERE id=?", (profile_id,)
    ).fetchone()
    conn.close()
    return row


def test_update_plant_profile_writes_fields(client):
    c, db_path = client
    _set_session(c, role="admin")
    pid = _profile_id(db_path, "tomato", "vegetative")
    r = c.put(
        f"/api/grow/plant-profiles/{pid}",
        json={"target_moisture_pct": 62, "kp": 0.5, "notes": "tweaked"},
    )
    assert r.status_code == 200, r.data
    assert r.get_json() == {"ok": True}
    row = _profile_row(db_path, pid)
    assert row["target_moisture_pct"] == 62
    assert row["kp"] == 0.5
    assert row["notes"] == "tweaked"


def test_update_plant_profile_validates_min_pulse_le_max(client):
    c, db_path = client
    _set_session(c, role="admin")
    pid = _profile_id(db_path, "tomato", "vegetative")
    r = c.put(
        f"/api/grow/plant-profiles/{pid}",
        json={"min_pulse_s": 10, "max_pulse_s": 5},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_payload"


def test_update_plant_profile_validates_field_ranges(client):
    c, db_path = client
    _set_session(c, role="admin")
    pid = _profile_id(db_path, "tomato", "vegetative")
    r = c.put(
        f"/api/grow/plant-profiles/{pid}",
        json={"target_moisture_pct": 120},
    )
    assert r.status_code == 400


def test_update_plant_profile_404_unknown_id(client):
    c, _ = client
    _set_session(c, role="admin")
    r = c.put(
        "/api/grow/plant-profiles/99999",
        json={"target_moisture_pct": 50},
    )
    assert r.status_code == 404
    assert r.get_json()["error"] == "profile_not_found"


def test_update_plant_profile_admin_only(client):
    c, db_path = client
    pid = _profile_id(db_path, "tomato", "vegetative")
    _set_session(c, logged_in=True, role="controller")
    r = c.put(
        f"/api/grow/plant-profiles/{pid}",
        json={"target_moisture_pct": 50},
    )
    assert r.status_code == 403


def test_update_plant_profile_empty_body_is_no_op(client):
    c, db_path = client
    _set_session(c, role="admin")
    pid = _profile_id(db_path, "tomato", "vegetative")
    before = _profile_row(db_path, pid)
    r = c.put(f"/api/grow/plant-profiles/{pid}", json={})
    assert r.status_code == 200
    after = _profile_row(db_path, pid)
    # All numeric fields unchanged
    assert before["target_moisture_pct"] == after["target_moisture_pct"]
    assert before["kp"] == after["kp"]


# ---------------------------------------------------------------------------
# 2c. Holiday mode
# ---------------------------------------------------------------------------


def test_get_holiday_mode_returns_default_off(client):
    c, _ = client
    _set_session(c, logged_in=True, role="viewer")
    r = c.get("/api/grow/settings/holiday-mode")
    assert r.status_code == 200
    assert r.get_json() == {"enabled": False}


def test_put_holiday_mode_writes_setting(client):
    c, db_path = client
    _set_session(c, role="admin")
    r = c.put("/api/grow/settings/holiday-mode", json={"enabled": True})
    assert r.status_code == 200
    body = r.get_json()
    assert body == {"ok": True, "enabled": True}

    conn = sqlite3.connect(db_path)
    val = conn.execute(
        "SELECT value FROM app_settings WHERE key='grow_holiday_mode'"
    ).fetchone()[0]
    conn.close()
    assert val == "1"

    # GET reflects the new state
    r = c.get("/api/grow/settings/holiday-mode")
    assert r.get_json() == {"enabled": True}

    # And turning it off round-trips
    r = c.put("/api/grow/settings/holiday-mode", json={"enabled": False})
    assert r.status_code == 200
    assert r.get_json()["enabled"] is False
    r = c.get("/api/grow/settings/holiday-mode")
    assert r.get_json() == {"enabled": False}


def test_put_holiday_mode_admin_only(client):
    c, _ = client
    for role in ("viewer", "controller"):
        _set_session(c, logged_in=True, role=role)
        r = c.put("/api/grow/settings/holiday-mode", json={"enabled": True})
        assert r.status_code == 403
    _set_session(c, logged_in=False, role="viewer")
    r = c.put("/api/grow/settings/holiday-mode", json={"enabled": True})
    assert r.status_code == 401


def test_put_holiday_mode_validates_body_shape(client):
    c, _ = client
    _set_session(c, role="admin")
    # Missing 'enabled'
    r = c.put("/api/grow/settings/holiday-mode", json={})
    assert r.status_code == 400
    # Wrong type
    r = c.put("/api/grow/settings/holiday-mode", json={"enabled": "yes"})
    assert r.status_code == 400
    r = c.put("/api/grow/settings/holiday-mode", json={"enabled": 1})
    assert r.status_code == 400
