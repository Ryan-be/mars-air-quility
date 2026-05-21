"""Tests for /api/notifications/* endpoints."""

import sqlite3

import pytest

from database.init_db import create_db


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("MLSS_DB_FILE", str(db_path))
    from config import config as _config
    _config.reload()
    monkeypatch.setattr("database.init_db.DB_FILE", str(db_path))
    create_db()
    # Seed a user
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "INSERT INTO users (github_username, role, created_at) "
        "VALUES ('alice', 'admin', '2026-05-20T10:00:00Z')"
    )
    user_id = cur.lastrowid
    conn.commit()
    conn.close()

    # Import the Flask app late so monkeypatch wins.
    # Use a minimal Flask app fixture, not the real one (which boots hardware).
    from flask import Flask
    from mlss_monitor.routes.api_notifications import api_notifications_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "test"
    app.register_blueprint(api_notifications_bp)
    app.config["_DB_PATH"] = str(db_path)
    app.config["_USER_ID"] = user_id

    return app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user"] = "alice"
            sess["user_role"] = "admin"
            sess["user_id"] = app.config["_USER_ID"]
        yield c


def test_vapid_key_returns_public_key(client, monkeypatch):
    monkeypatch.setattr(
        "mlss_monitor.notifications.vapid.get_public_key",
        lambda: "test-pubkey-fake",
    )
    r = client.get("/api/notifications/vapid-key")
    assert r.status_code == 200
    assert r.get_json() == {"public_key": "test-pubkey-fake"}


def test_subscriptions_get_empty(client):
    r = client.get("/api/notifications/subscriptions")
    assert r.status_code == 200
    assert r.get_json() == []


def test_subscribe_then_list(client, app):
    payload = {"endpoint": "https://push.example/abc",
               "p256dh": "pk", "auth": "ak",
               "device_label": "Alice's iPhone"}
    r = client.post("/api/notifications/subscriptions", json=payload)
    assert r.status_code == 200
    sub_id = r.get_json()["id"]

    r = client.get("/api/notifications/subscriptions")
    rows = r.get_json()
    assert len(rows) == 1
    assert rows[0]["id"] == sub_id
    assert rows[0]["device_label"] == "Alice's iPhone"
    # Sensitive fields must NOT be exposed:
    assert "endpoint" not in rows[0]
    assert "p256dh"   not in rows[0]
    assert "auth"     not in rows[0]


def test_subscribe_same_endpoint_updates_existing(client, app):
    payload1 = {"endpoint": "https://push.example/abc",
                "p256dh": "pk1", "auth": "ak1"}
    payload2 = {"endpoint": "https://push.example/abc",
                "p256dh": "pk2", "auth": "ak2"}
    client.post("/api/notifications/subscriptions", json=payload1)
    client.post("/api/notifications/subscriptions", json=payload2)

    conn = sqlite3.connect(app.config["_DB_PATH"])
    rows = conn.execute(
        "SELECT p256dh FROM push_subscriptions"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "pk2"


def test_delete_subscription(client, app):
    payload = {"endpoint": "https://push.example/abc",
               "p256dh": "pk", "auth": "ak"}
    sub_id = client.post(
        "/api/notifications/subscriptions", json=payload
    ).get_json()["id"]

    r = client.delete(f"/api/notifications/subscriptions/{sub_id}")
    assert r.status_code == 200

    conn = sqlite3.connect(app.config["_DB_PATH"])
    rows = conn.execute("SELECT * FROM push_subscriptions").fetchall()
    conn.close()
    assert len(rows) == 0


def test_delete_other_users_subscription_404(client, app):
    # Insert a sub for a different user_id
    conn = sqlite3.connect(app.config["_DB_PATH"])
    cur = conn.execute(
        "INSERT INTO users (github_username, created_at) "
        "VALUES ('bob', '2026-05-20T10:00:00Z')"
    )
    bob_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at) "
        "VALUES (?, 'https://push.example/bob', 'p', 'a', '2026-05-20T10:00:00Z')",
        (bob_id,),
    )
    sub_id = cur.lastrowid
    conn.commit()
    conn.close()

    r = client.delete(f"/api/notifications/subscriptions/{sub_id}")
    assert r.status_code == 404


def test_preferences_get_defaults(client):
    r = client.get("/api/notifications/preferences")
    assert r.status_code == 200
    assert r.get_json() == {
        "air_quality":     "warning",
        "grow_units":      "warning",
        "system_health":   "warning",
        "backup_pipeline": "warning",
    }


def test_preferences_patch_one(client):
    r = client.patch("/api/notifications/preferences",
                     json={"air_quality": "critical"})
    assert r.status_code == 200

    r = client.get("/api/notifications/preferences")
    assert r.get_json()["air_quality"] == "critical"
    # Other categories untouched
    assert r.get_json()["grow_units"] == "warning"


def test_preferences_patch_all(client):
    r = client.patch("/api/notifications/preferences", json={
        "air_quality": "off", "grow_units": "info",
        "system_health": "critical", "backup_pipeline": "warning",
    })
    assert r.status_code == 200
    assert client.get("/api/notifications/preferences").get_json() == {
        "air_quality": "off", "grow_units": "info",
        "system_health": "critical", "backup_pipeline": "warning",
    }


def test_preferences_patch_invalid_value_rejected(client):
    r = client.patch("/api/notifications/preferences",
                     json={"air_quality": "EXTREME"})
    assert r.status_code == 400
    assert "error" in r.get_json()
    # Make sure nothing was changed.
    r = client.get("/api/notifications/preferences")
    assert r.get_json()["air_quality"] == "warning"


def test_preferences_patch_unknown_category_rejected(client):
    r = client.patch("/api/notifications/preferences",
                     json={"unknown_cat": "warning"})
    assert r.status_code == 400


def test_history_empty(client):
    r = client.get("/api/notifications/history")
    assert r.status_code == 200
    assert r.get_json() == []


def test_history_returns_rows_for_user(client, app):
    conn = sqlite3.connect(app.config["_DB_PATH"])
    conn.execute(
        "INSERT INTO notification_history "
        "(user_id, category, severity, title, body, deep_link, created_at) "
        "VALUES (?, 'air_quality', 'warning', 'X', 'Y', '/incidents', '2026-05-20T11:00:00')",
        (app.config["_USER_ID"],),
    )
    conn.commit()
    conn.close()
    r = client.get("/api/notifications/history")
    rows = r.get_json()
    assert len(rows) == 1
    assert rows[0]["title"] == "X"
    assert rows[0]["category"] == "air_quality"


def test_history_mark_read(client, app):
    conn = sqlite3.connect(app.config["_DB_PATH"])
    conn.execute(
        "INSERT INTO notification_history "
        "(user_id, category, severity, title, body, deep_link, created_at) "
        "VALUES (?, 'air_quality', 'warning', 'X', 'Y', '/i', '2026-05-20T11:00:00')",
        (app.config["_USER_ID"],),
    )
    conn.commit()
    conn.close()

    r = client.post("/api/notifications/history/mark-read")
    assert r.status_code == 200
    assert r.get_json()["count"] == 1

    conn = sqlite3.connect(app.config["_DB_PATH"])
    row = conn.execute(
        "SELECT read_at FROM notification_history"
    ).fetchone()
    conn.close()
    assert row[0] is not None


def test_history_days_param_caps_at_90(client):
    r = client.get("/api/notifications/history?days=99999")
    assert r.status_code == 200  # not an error — just silently capped


def test_unauthenticated_returns_401_or_redirect(app):
    with app.test_client() as c:
        # No session — should be denied. Without the full auth middleware
        # registered, require_role redirects unauthed page routes; for /api
        # paths the rbac module returns 401.
        r = c.get("/api/notifications/preferences")
        assert r.status_code in (401, 302)


def test_bootstrap_admin_without_user_row_is_lazy_created(app, monkeypatch):
    """The MLSS_ALLOWED_GITHUB_USER login intentionally skips the users
    table during auth (see routes/auth.py). When such a session hits a
    per-user API the first time, we lazy-insert a users row scoped to
    them rather than 401-ing out."""
    # Tell api_notifications who the bootstrap admin is. state.ALLOWED_GITHUB_USER
    # is normally set by app.py at startup.
    monkeypatch.setattr("mlss_monitor.state.ALLOWED_GITHUB_USER",
                        "boss", raising=False)

    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user"]      = "boss"
            sess["user_role"] = "admin"
            sess["user_id"]   = None  # the bootstrap-admin signature

        r = c.get("/api/notifications/preferences")
        assert r.status_code == 200
        assert r.get_json() == {
            "air_quality":     "warning",
            "grow_units":      "warning",
            "system_health":   "warning",
            "backup_pipeline": "warning",
        }

    # users row was created exactly once.
    conn = sqlite3.connect(app.config["_DB_PATH"])
    rows = conn.execute(
        "SELECT github_username, role FROM users "
        "WHERE lower(github_username) = 'boss'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][1] == "admin"


def test_non_bootstrap_user_without_row_returns_user_not_found(app, monkeypatch):
    """Lazy-insert ONLY fires for the bootstrap admin. Any other session
    user whose row is missing is treated as an inconsistent state and
    rejected — defence against an attacker who somehow forged a session
    cookie with an arbitrary github_username."""
    monkeypatch.setattr("mlss_monitor.state.ALLOWED_GITHUB_USER",
                        "boss", raising=False)

    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user"]      = "imposter"   # NOT the bootstrap admin
            sess["user_role"] = "admin"
            sess["user_id"]   = None

        r = c.get("/api/notifications/preferences")
        assert r.status_code in (401, 404)

    conn = sqlite3.connect(app.config["_DB_PATH"])
    rows = conn.execute(
        "SELECT * FROM users WHERE github_username = 'imposter'"
    ).fetchall()
    conn.close()
    assert rows == []
