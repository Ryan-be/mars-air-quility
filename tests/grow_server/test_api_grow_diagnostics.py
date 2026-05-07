"""GET /api/grow/units/<id>/diagnostics — consolidated payload for the Diagnostics tab.

Single-fetch surface area covers four data lanes:
  * firmware_version / uptime_s / buffer_size from grow_units
  * connection_log: last 20 online/offline rows from grow_errors
  * sensor_sanity: per-capability staleness driven by last_seen_at vs
    a configurable app_settings threshold
  * open_errors: unresolved grow_errors EXCLUDING online/offline meta
    rows (those live in connection_log; mixing would double-render
    every disconnect)

RBAC: viewer-readable (it's pure observability).
"""
import sqlite3
import tempfile
from datetime import datetime, timedelta

import pytest


def _set_session(c, *, logged_in=True, role="admin"):
    with c.session_transaction() as sess:
        sess["logged_in"] = logged_in
        sess["user_role"] = role


@pytest.fixture
def client(monkeypatch):
    """Mount only the diagnostics blueprint against a fresh DB seeded with
    one grow_unit (id=1). Tests then inject the rows they need (caps,
    grow_errors) with raw sqlite3 to keep the seed surface tiny."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_diagnostics.DB_FILE", tmp.name
    )
    init_db.create_db()

    now = datetime.utcnow()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, firmware_version, last_uptime_s, "
        "last_buffer_size) "
        "VALUES (1, 'hw-1', 'X', ?, 'h', ?, '2.0.0', 3600, 5)",
        (now, now),
    )
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_diagnostics import api_grow_diagnostics_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_diagnostics_bp)
    c = app.test_client()
    _set_session(c, role="admin")
    yield c, tmp.name


def _insert_error(
    db_path,
    *,
    unit_id=1,
    kind="sensor_degraded",
    severity="warning",
    message="msg",
    timestamp_utc=None,
    resolved_at=None,
    subject_sensor=None,
):
    if timestamp_utc is None:
        timestamp_utc = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO grow_errors "
        "(unit_id, timestamp_utc, severity, kind, message, "
        " subject_sensor, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            unit_id, timestamp_utc, severity, kind, message,
            subject_sensor, resolved_at,
        ),
    )
    err_id = cur.lastrowid
    conn.commit()
    conn.close()
    return err_id


def _insert_capability(db_path, *, unit_id=1, channel, last_seen_at=None):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_unit_capabilities "
        "(unit_id, channel, hardware, is_required, installed_at, last_seen_at) "
        "VALUES (?, ?, 'hw', 0, ?, ?)",
        (unit_id, channel, datetime.utcnow(), last_seen_at),
    )
    conn.commit()
    conn.close()


def _set_threshold(db_path, value):
    """Overwrite the seeded grow_sensor_stale_threshold_min — tests need
    to hit specific values to assert custom thresholds."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
        ("grow_sensor_stale_threshold_min", value),
    )
    conn.commit()
    conn.close()


def _delete_threshold(db_path):
    """Remove the seeded threshold so the endpoint must use its default."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "DELETE FROM app_settings WHERE key='grow_sensor_stale_threshold_min'"
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Unit-row fields
# ---------------------------------------------------------------------------


def test_diagnostics_returns_firmware_version_and_uptime_from_unit_row(client):
    """The fixture seeds firmware_version='2.0.0', last_uptime_s=3600,
    last_buffer_size=5 — verify the endpoint surfaces those exact values."""
    c, _ = client
    r = c.get("/api/grow/units/1/diagnostics")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["firmware_version"] == "2.0.0"
    assert body["uptime_s"] == 3600
    assert body["buffer_size"] == 5


def test_diagnostics_unit_not_found_returns_404(client):
    c, _ = client
    r = c.get("/api/grow/units/9999/diagnostics")
    assert r.status_code == 404
    assert r.get_json()["error"] == "unit_not_found"


# ---------------------------------------------------------------------------
# Connection log
# ---------------------------------------------------------------------------


def test_diagnostics_connection_log_returns_last_20_events_descending(client):
    """Seed 25 alternating online/offline rows; assert exactly 20 returned,
    ordered by id DESC (newest first)."""
    c, db_path = client
    base = datetime.utcnow() - timedelta(hours=25)
    ids = []
    for i in range(25):
        kind = "online" if i % 2 == 0 else "offline"
        severity = "info" if kind == "online" else "warning"
        ids.append(_insert_error(
            db_path,
            kind=kind,
            severity=severity,
            message=f"event {i}",
            timestamp_utc=base + timedelta(hours=i),
        ))
    r = c.get("/api/grow/units/1/diagnostics")
    assert r.status_code == 200
    log = r.get_json()["connection_log"]
    assert len(log) == 20, f"expected last 20, got {len(log)}"
    returned_ids = [e["id"] for e in log]
    # Newest first → strictly descending
    assert returned_ids == sorted(returned_ids, reverse=True)
    # And they're the 20 most-recent inserts
    assert returned_ids == sorted(ids, reverse=True)[:20]


def test_diagnostics_connection_log_filters_online_offline_only(client):
    """Mix online + offline + sensor_degraded rows; only online/offline
    are allowed in connection_log. The sensor_degraded row must NOT
    appear there (it belongs in open_errors)."""
    c, db_path = client
    _insert_error(db_path, kind="online", severity="info", message="up")
    _insert_error(db_path, kind="offline", severity="warning", message="down")
    _insert_error(
        db_path, kind="sensor_degraded", severity="warning",
        message="bad sensor", subject_sensor="ambient_lux",
    )
    r = c.get("/api/grow/units/1/diagnostics")
    body = r.get_json()
    kinds = {entry["kind"] for entry in body["connection_log"]}
    assert kinds == {"online", "offline"}
    assert "sensor_degraded" not in kinds


# ---------------------------------------------------------------------------
# Sensor sanity
# ---------------------------------------------------------------------------


def test_diagnostics_sensor_sanity_marks_stale_when_last_seen_old(client):
    """Capability with last_seen_at=10 minutes ago, threshold=5 → is_stale=True."""
    c, db_path = client
    _set_threshold(db_path, "5")
    ten_min_ago = datetime.utcnow() - timedelta(minutes=10)
    _insert_capability(db_path, channel="soil_moisture", last_seen_at=ten_min_ago)
    r = c.get("/api/grow/units/1/diagnostics")
    sanity = r.get_json()["sensor_sanity"]
    assert len(sanity) == 1
    assert sanity[0]["channel"] == "soil_moisture"
    assert sanity[0]["is_stale"] is True
    # Roughly 10 minutes — allow a small wall-clock drift between fixture
    # row insert and assertion
    assert 9.5 < sanity[0]["minutes_ago"] < 11.0


def test_diagnostics_sensor_sanity_handles_never_seen_sensor(client):
    """Capability with last_seen_at=NULL: is_stale=True, minutes_ago=None."""
    c, db_path = client
    _insert_capability(db_path, channel="ambient_lux", last_seen_at=None)
    r = c.get("/api/grow/units/1/diagnostics")
    sanity = r.get_json()["sensor_sanity"]
    assert len(sanity) == 1
    entry = sanity[0]
    assert entry["channel"] == "ambient_lux"
    assert entry["last_seen_at"] is None
    assert entry["minutes_ago"] is None
    assert entry["is_stale"] is True


def test_diagnostics_sensor_sanity_uses_default_threshold_when_setting_missing(client):
    """No app_settings row for grow_sensor_stale_threshold_min → endpoint
    falls back to 5 minutes."""
    c, db_path = client
    _delete_threshold(db_path)
    _insert_capability(
        db_path, channel="soil_moisture",
        last_seen_at=datetime.utcnow() - timedelta(minutes=1),
    )
    r = c.get("/api/grow/units/1/diagnostics")
    sanity = r.get_json()["sensor_sanity"]
    assert len(sanity) == 1
    assert sanity[0]["stale_threshold_min"] == 5


def test_diagnostics_sensor_sanity_uses_custom_threshold_from_app_settings(client):
    """app_settings.grow_sensor_stale_threshold_min='10' → threshold=10
    surfaces in the response."""
    c, db_path = client
    _set_threshold(db_path, "10")
    _insert_capability(
        db_path, channel="soil_moisture",
        last_seen_at=datetime.utcnow() - timedelta(minutes=8),
    )
    r = c.get("/api/grow/units/1/diagnostics")
    sanity = r.get_json()["sensor_sanity"]
    assert len(sanity) == 1
    assert sanity[0]["stale_threshold_min"] == 10
    # 8 min < 10 min threshold → not stale
    assert sanity[0]["is_stale"] is False


# ---------------------------------------------------------------------------
# Open errors
# ---------------------------------------------------------------------------


def test_diagnostics_open_errors_excludes_resolved(client):
    """Two errors, one with resolved_at populated → only the unresolved
    one comes back in open_errors."""
    c, db_path = client
    _insert_error(db_path, kind="sensor_degraded", message="open")
    _insert_error(
        db_path, kind="sensor_degraded", message="closed",
        resolved_at=datetime.utcnow(),
    )
    r = c.get("/api/grow/units/1/diagnostics")
    open_errs = r.get_json()["open_errors"]
    assert len(open_errs) == 1
    assert open_errs[0]["message"] == "open"


def test_diagnostics_open_errors_excludes_online_offline_meta_events(client):
    """Unresolved offline rows live in connection_log, NOT open_errors —
    mixing would double-render every disconnect as both a connection
    event and an open error."""
    c, db_path = client
    _insert_error(db_path, kind="offline", severity="warning", message="down")
    r = c.get("/api/grow/units/1/diagnostics")
    body = r.get_json()
    open_kinds = {e["kind"] for e in body["open_errors"]}
    assert "offline" not in open_kinds
    assert "online" not in open_kinds
    # The offline row IS in connection_log
    conn_kinds = {e["kind"] for e in body["connection_log"]}
    assert "offline" in conn_kinds


def test_diagnostics_open_errors_includes_subject_sensor(client):
    """sensor_degraded rows carry subject_sensor — the Diagnostics tab
    needs that string to render the per-channel badge correctly."""
    c, db_path = client
    _insert_error(
        db_path, kind="sensor_degraded", severity="warning",
        message="ambient_lux read failed", subject_sensor="ambient_lux",
    )
    r = c.get("/api/grow/units/1/diagnostics")
    open_errs = r.get_json()["open_errors"]
    assert len(open_errs) == 1
    assert open_errs[0]["subject_sensor"] == "ambient_lux"
    assert open_errs[0]["kind"] == "sensor_degraded"
    assert open_errs[0]["severity"] == "warning"


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


def test_diagnostics_requires_session(client):
    """Anonymous (no logged_in flag) → 401."""
    c, _ = client
    _set_session(c, logged_in=False, role="viewer")
    r = c.get("/api/grow/units/1/diagnostics")
    assert r.status_code == 401


def test_diagnostics_works_for_viewer_role(client):
    """Diagnostics is observability — viewer can read."""
    c, _ = client
    _set_session(c, logged_in=True, role="viewer")
    r = c.get("/api/grow/units/1/diagnostics")
    assert r.status_code == 200
    body = r.get_json()
    # All four lanes are present in the payload (even if empty for some)
    assert "firmware_version" in body
    assert "connection_log" in body
    assert "sensor_sanity" in body
    assert "open_errors" in body
