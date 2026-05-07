"""Reveal the raw enrollment key once (then it's deleted from app_settings).

The endpoint is admin-only (Vuln 2 fix): the master enrollment key authorises
POST /api/grow/enroll, which is idempotent by hardware_serial — meaning anyone
holding the key can re-POST a known serial to rotate that unit's bearer token.
Only admins should ever see it.
"""
import tempfile
import pytest


def _set_session(c, *, logged_in=True, role="admin"):
    """Open a Flask test session with the given auth state."""
    with c.session_transaction() as sess:
        sess["logged_in"] = logged_in
        sess["user_role"] = role


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.routes.api_grow_dist.DB_FILE", tmp.name)
    init_db.create_db()
    from flask import Flask
    from mlss_monitor.routes.api_grow_dist import api_grow_dist_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"  # required for session_transaction
    app.register_blueprint(api_grow_dist_bp)
    return app.test_client(), tmp.name


def test_peek_once_returns_raw_key_first_time(client):
    c, _ = client
    _set_session(c, role="admin")
    r = c.get("/api/grow/enrollment-key/peek-once")
    assert r.status_code == 200
    assert "key" in r.get_json()


def test_peek_once_deletes_after_reveal(client):
    c, _ = client
    _set_session(c, role="admin")
    c.get("/api/grow/enrollment-key/peek-once")
    r = c.get("/api/grow/enrollment-key/peek-once")
    assert r.status_code == 410  # Gone — already revealed
    body = r.get_json()
    assert "already_revealed" in body.get("error", "")


# ---------------------------------------------------------------------------
# RBAC tests for the peek endpoint (Vuln 2 — admin-only)
# ---------------------------------------------------------------------------

def test_peek_denies_anonymous(client):
    """Unauthenticated must be rejected with 401."""
    c, _ = client
    _set_session(c, logged_in=False, role="viewer")
    r = c.get("/api/grow/enrollment-key/peek-once")
    assert r.status_code == 401


def test_peek_denies_viewer_role(client):
    """Viewer must be forbidden with 403 — viewers can't see the master key."""
    c, _ = client
    _set_session(c, logged_in=True, role="viewer")
    r = c.get("/api/grow/enrollment-key/peek-once")
    assert r.status_code == 403


def test_peek_denies_controller_role(client):
    """Controller must also be forbidden — only admin should see the master key."""
    c, _ = client
    _set_session(c, logged_in=True, role="controller")
    r = c.get("/api/grow/enrollment-key/peek-once")
    assert r.status_code == 403


def test_peek_allows_admin_role(client):
    """Only admin can fetch the key."""
    c, _ = client
    _set_session(c, logged_in=True, role="admin")
    r = c.get("/api/grow/enrollment-key/peek-once")
    assert r.status_code == 200
    assert "key" in r.get_json()


def test_unauthenticated_does_not_consume_the_key(client):
    """Defence in depth: a 401 must not delete the key.

    The decorator short-circuits BEFORE the DELETE — verify the key
    is still claimable by an admin afterwards.
    """
    c, _ = client
    _set_session(c, logged_in=False, role="viewer")
    r = c.get("/api/grow/enrollment-key/peek-once")
    assert r.status_code == 401

    # Admin claim still works — the key was NOT consumed by the failed attempt
    _set_session(c, logged_in=True, role="admin")
    r = c.get("/api/grow/enrollment-key/peek-once")
    assert r.status_code == 200
    assert "key" in r.get_json()


def test_viewer_attempt_does_not_consume_the_key(client):
    """Same as above but with logged-in viewer (403 path)."""
    c, _ = client
    _set_session(c, logged_in=True, role="viewer")
    r = c.get("/api/grow/enrollment-key/peek-once")
    assert r.status_code == 403

    _set_session(c, logged_in=True, role="admin")
    r = c.get("/api/grow/enrollment-key/peek-once")
    assert r.status_code == 200
    assert "key" in r.get_json()
