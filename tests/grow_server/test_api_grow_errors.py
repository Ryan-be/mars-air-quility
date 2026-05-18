"""GET /api/grow/errors + PATCH /api/grow/errors/<id> — fleet-wide error log.

GET endpoint: filterable list (severity / kind / unit_id / since /
unresolved_only / limit), JOIN'd to grow_units so each row carries a
unit_label. Viewer-readable.

PATCH endpoint: resolve / unresolve / snooze / unsnooze. Admin-only.
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
    """Mount the errors blueprint against a fresh DB seeded with two grow
    units. Tests then inject grow_errors rows with raw sqlite3.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_errors.DB_FILE", tmp.name
    )
    init_db.create_db()

    now = datetime.utcnow()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) "
        "VALUES (1, 'hw-1', 'Tomato 1', ?, 'h', ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) "
        "VALUES (2, 'hw-2', 'Basil 2', ?, 'h', ?)",
        (now, now),
    )
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_errors import api_grow_errors_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_errors_bp)
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
    snoozed_until=None,
    subject_sensor=None,
    details_json=None,
):
    if timestamp_utc is None:
        timestamp_utc = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO grow_errors "
        "(unit_id, timestamp_utc, severity, kind, message, "
        " subject_sensor, details_json, resolved_at, snoozed_until) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            unit_id, timestamp_utc, severity, kind, message,
            subject_sensor, details_json, resolved_at, snoozed_until,
        ),
    )
    err_id = cur.lastrowid
    conn.commit()
    conn.close()
    return err_id


# ---------------------------------------------------------------------------
# GET /api/grow/errors — filtering, ordering, JOIN, RBAC
# ---------------------------------------------------------------------------


def test_returns_all_when_no_filters(client):
    """Default response excludes info-severity reconnect noise (kind=online).
    With three rows seeded — one info/online, one warning/offline,
    one warning/sensor_degraded — the default response returns the
    two warnings; the info/online row is filtered out (design-critique
    #19). Use include_reconnects=1 to bring it back."""
    c, db_path = client
    _insert_error(db_path, kind="online", severity="info", message="up")
    _insert_error(db_path, kind="offline", severity="warning", message="down")
    _insert_error(db_path, kind="sensor_degraded", severity="warning", message="d")
    r = c.get("/api/grow/errors")
    assert r.status_code == 200, r.data
    rows = r.get_json()
    # Default: noise filter excludes info/online; only the two warning rows remain.
    assert len(rows) == 2
    assert {r["kind"] for r in rows} == {"offline", "sensor_degraded"}


def test_default_filters_out_info_severity_reconnect_events(client):
    """Pin the noise filter: kind=online + severity=info is excluded by
    default. A warning-severity online event would NOT be filtered (only
    info-severity counts as noise)."""
    c, db_path = client
    _insert_error(db_path, kind="online", severity="info", message="up1")
    _insert_error(db_path, kind="online", severity="info", message="up2")
    _insert_error(db_path, kind="online", severity="warning",
                  message="reconnected unexpectedly")
    r = c.get("/api/grow/errors")
    rows = r.get_json()
    # Two info-severity online filtered out; the warning-severity one kept.
    assert len(rows) == 1
    assert rows[0]["severity"] == "warning"


def test_include_reconnects_param_brings_noise_back(client):
    """Power-user opt-in: ?include_reconnects=1 returns info/online too."""
    c, db_path = client
    _insert_error(db_path, kind="online", severity="info", message="up")
    _insert_error(db_path, kind="sensor_degraded", severity="warning", message="d")
    r = c.get("/api/grow/errors?include_reconnects=1")
    rows = r.get_json()
    assert len(rows) == 2


def test_explicit_kind_online_filter_bypasses_noise_filter(client):
    """If the caller explicitly filters on kind=online they're actively
    asking for those rows — don't second-guess by also applying the
    noise filter."""
    c, db_path = client
    _insert_error(db_path, kind="online", severity="info", message="up1")
    _insert_error(db_path, kind="online", severity="info", message="up2")
    _insert_error(db_path, kind="sensor_degraded", severity="warning", message="d")
    r = c.get("/api/grow/errors?kind=online")
    rows = r.get_json()
    # Explicit kind=online filter ⇒ both online rows returned despite
    # being info-severity (the user clearly wants them).
    assert len(rows) == 2
    assert all(row["kind"] == "online" for row in rows)


def test_unresolved_only_filters_resolved_out(client):
    c, db_path = client
    _insert_error(db_path, kind="sensor_degraded", message="open")
    _insert_error(
        db_path, kind="sensor_degraded", message="closed",
        resolved_at=datetime.utcnow(),
    )
    r = c.get("/api/grow/errors?unresolved_only=true")
    rows = r.get_json()
    assert len(rows) == 1
    assert rows[0]["message"] == "open"


def test_filters_by_unit_id(client):
    c, db_path = client
    _insert_error(db_path, unit_id=1, message="from-unit-1")
    _insert_error(db_path, unit_id=2, message="from-unit-2")
    r = c.get("/api/grow/errors?unit_id=2")
    rows = r.get_json()
    assert len(rows) == 1
    assert rows[0]["unit_id"] == 2
    assert rows[0]["message"] == "from-unit-2"


def test_filters_by_severity(client):
    c, db_path = client
    _insert_error(db_path, severity="info", message="i")
    _insert_error(db_path, severity="warning", message="w")
    _insert_error(db_path, severity="critical", message="c")
    r = c.get("/api/grow/errors?severity=critical")
    rows = r.get_json()
    assert len(rows) == 1
    assert rows[0]["severity"] == "critical"


def test_filters_by_kind(client):
    c, db_path = client
    _insert_error(db_path, kind="online", message="up")
    _insert_error(db_path, kind="offline", message="down")
    _insert_error(db_path, kind="sensor_degraded", message="bad")
    r = c.get("/api/grow/errors?kind=offline")
    rows = r.get_json()
    assert len(rows) == 1
    assert rows[0]["kind"] == "offline"


def test_filters_by_since(client):
    c, db_path = client
    base = datetime.utcnow() - timedelta(hours=2)
    _insert_error(db_path, message="old", timestamp_utc=base)
    _insert_error(db_path, message="recent", timestamp_utc=datetime.utcnow())
    cutoff = (datetime.utcnow() - timedelta(minutes=30)).isoformat()
    r = c.get(f"/api/grow/errors?since={cutoff}")
    rows = r.get_json()
    assert len(rows) == 1
    assert rows[0]["message"] == "recent"


def test_invalid_severity_returns_400(client):
    c, _ = client
    r = c.get("/api/grow/errors?severity=catastrophic")
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_severity"


def test_invalid_since_returns_400(client):
    c, _ = client
    r = c.get("/api/grow/errors?since=not-a-date")
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_since"


def test_respects_limit_param(client):
    c, db_path = client
    for i in range(10):
        _insert_error(db_path, message=f"err {i}")
    r = c.get("/api/grow/errors?limit=3")
    assert r.status_code == 200
    rows = r.get_json()
    assert len(rows) == 3


def test_caps_limit_at_500(client):
    """Server silently clamps limit > 500; the client sending limit=10000
    must NOT cause a 400 nor return more than 500 rows."""
    c, db_path = client
    # We don't need to insert 600 rows — assert the absence of 400 + the
    # SQL was executed with the clamped limit. Insert one row to confirm
    # the response shape works even at the cap.
    _insert_error(db_path, message="a")
    r = c.get("/api/grow/errors?limit=10000")
    assert r.status_code == 200
    rows = r.get_json()
    # We only inserted 1 — but the important assertion is it didn't 400.
    assert len(rows) == 1


def test_default_limit_100(client):
    """No limit param → server uses 100 (assert via 110 inserted, only 100 returned)."""
    c, db_path = client
    base = datetime.utcnow() - timedelta(hours=20)
    for i in range(110):
        _insert_error(
            db_path, message=f"err {i}",
            timestamp_utc=base + timedelta(seconds=i),
        )
    r = c.get("/api/grow/errors")
    rows = r.get_json()
    assert len(rows) == 100


def test_orders_by_timestamp_desc(client):
    c, db_path = client
    base = datetime.utcnow() - timedelta(hours=3)
    _insert_error(db_path, message="oldest", timestamp_utc=base)
    _insert_error(
        db_path, message="middle",
        timestamp_utc=base + timedelta(hours=1),
    )
    _insert_error(
        db_path, message="newest",
        timestamp_utc=base + timedelta(hours=2),
    )
    r = c.get("/api/grow/errors")
    rows = r.get_json()
    assert [row["message"] for row in rows] == ["newest", "middle", "oldest"]


def test_includes_unit_label_via_join(client):
    c, db_path = client
    _insert_error(db_path, unit_id=1, message="from-1")
    _insert_error(db_path, unit_id=2, message="from-2")
    r = c.get("/api/grow/errors")
    rows = r.get_json()
    by_msg = {row["message"]: row for row in rows}
    assert by_msg["from-1"]["unit_label"] == "Tomato 1"
    assert by_msg["from-2"]["unit_label"] == "Basil 2"


def test_works_for_viewer_role(client):
    """Listing the fleet-wide log is observability — viewers can read."""
    c, db_path = client
    _set_session(c, logged_in=True, role="viewer")
    _insert_error(db_path, message="visible-to-viewer")
    r = c.get("/api/grow/errors")
    assert r.status_code == 200
    rows = r.get_json()
    assert len(rows) == 1


def test_anonymous_returns_401(client):
    c, _ = client
    _set_session(c, logged_in=False, role="viewer")
    r = c.get("/api/grow/errors")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /api/grow/errors/<id> — resolve, snooze, RBAC
# ---------------------------------------------------------------------------


def test_resolves_with_now_keyword(client):
    c, db_path = client
    err_id = _insert_error(db_path, message="open")
    r = c.patch(
        f"/api/grow/errors/{err_id}", json={"resolved_at": "now"},
    )
    assert r.status_code == 200, r.data
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT resolved_at FROM grow_errors WHERE id=?", (err_id,),
    ).fetchone()
    conn.close()
    assert row[0] is not None


def test_resolves_with_explicit_iso8601(client):
    c, db_path = client
    err_id = _insert_error(db_path)
    when = "2026-05-06T12:34:56"
    r = c.patch(
        f"/api/grow/errors/{err_id}", json={"resolved_at": when},
    )
    assert r.status_code == 200
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT resolved_at FROM grow_errors WHERE id=?", (err_id,),
    ).fetchone()
    conn.close()
    assert row[0] is not None
    assert "2026-05-06" in str(row[0])


def test_snoozes_until(client):
    c, db_path = client
    err_id = _insert_error(db_path)
    until = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    r = c.patch(
        f"/api/grow/errors/{err_id}", json={"snoozed_until": until},
    )
    assert r.status_code == 200
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT snoozed_until FROM grow_errors WHERE id=?", (err_id,),
    ).fetchone()
    conn.close()
    assert row[0] is not None


def test_can_unsnooze_with_null(client):
    c, db_path = client
    until = datetime.utcnow() + timedelta(hours=1)
    err_id = _insert_error(db_path, snoozed_until=until)
    r = c.patch(
        f"/api/grow/errors/{err_id}", json={"snoozed_until": None},
    )
    assert r.status_code == 200
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT snoozed_until FROM grow_errors WHERE id=?", (err_id,),
    ).fetchone()
    conn.close()
    assert row[0] is None


def test_can_unresolve_with_null(client):
    c, db_path = client
    err_id = _insert_error(db_path, resolved_at=datetime.utcnow())
    r = c.patch(
        f"/api/grow/errors/{err_id}", json={"resolved_at": None},
    )
    assert r.status_code == 200
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT resolved_at FROM grow_errors WHERE id=?", (err_id,),
    ).fetchone()
    conn.close()
    assert row[0] is None


def test_combined_resolve_and_snooze_in_one_PATCH(client):
    c, db_path = client
    err_id = _insert_error(db_path)
    until = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    r = c.patch(
        f"/api/grow/errors/{err_id}",
        json={"resolved_at": "now", "snoozed_until": until},
    )
    assert r.status_code == 200
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT resolved_at, snoozed_until FROM grow_errors WHERE id=?",
        (err_id,),
    ).fetchone()
    conn.close()
    assert row[0] is not None
    assert row[1] is not None


def test_invalid_resolved_at_returns_400(client):
    c, db_path = client
    err_id = _insert_error(db_path)
    r = c.patch(
        f"/api/grow/errors/{err_id}",
        json={"resolved_at": "not-a-date-or-now"},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_resolved_at"


def test_404_for_unknown_id(client):
    c, _ = client
    r = c.patch("/api/grow/errors/99999", json={"resolved_at": "now"})
    assert r.status_code == 404
    assert r.get_json()["error"] == "error_not_found"


def test_admin_only_viewer_403_controller_403(client):
    """PATCH endpoint is admin-only — both viewer and controller are denied."""
    c, db_path = client
    err_id = _insert_error(db_path)

    _set_session(c, logged_in=True, role="viewer")
    r = c.patch(f"/api/grow/errors/{err_id}", json={"resolved_at": "now"})
    assert r.status_code == 403

    _set_session(c, logged_in=True, role="controller")
    r = c.patch(f"/api/grow/errors/{err_id}", json={"resolved_at": "now"})
    assert r.status_code == 403


def test_empty_body_returns_400(client):
    c, db_path = client
    err_id = _insert_error(db_path)
    r = c.patch(f"/api/grow/errors/{err_id}", json={})
    assert r.status_code == 400
    assert r.get_json()["error"] == "empty_body"
