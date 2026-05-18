"""Tests for per-unit bearer-token rotation (Phase 1 spec §5).

Two endpoints, both admin-only:

* POST /api/grow/units/<id>/rotate-token       — mint + stash + return new token
* GET  /api/grow/units/<id>/token/peek-once    — one-shot reveal, then 410

The fixture seeds two units so we can verify cache eviction is per-unit
(rotating unit 1 must not touch unit 2's cached verification entry).
"""
import sqlite3
import tempfile
from datetime import datetime

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
    # The blueprint module + auth helper module each capture DB_FILE at
    # import time — patch all the read sites.
    monkeypatch.setattr("mlss_monitor.routes.api_grow_units.DB_FILE", tmp.name)
    monkeypatch.setattr("mlss_monitor.routes.api_grow_ws.DB_FILE", tmp.name)
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    init_db.create_db()

    # Seed two units so the cache-isolation tests have something to compare
    # against.
    from mlss_monitor.grow.auth import generate_token, hash_secret
    raw1 = generate_token()
    raw2 = generate_token()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, is_active) "
        "VALUES (1, 'hw-1', 'Tom 1', ?, ?, ?, 1)",
        (datetime.utcnow(), hash_secret(raw1), datetime.utcnow()),
    )
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, is_active) "
        "VALUES (2, 'hw-2', 'Basil 1', ?, ?, ?, 1)",
        (datetime.utcnow(), hash_secret(raw2), datetime.utcnow()),
    )
    conn.commit()
    conn.close()

    # Drop any cached bearer-validations from earlier tests so a fresh
    # token doesn't get rejected by a stale cache entry.
    from mlss_monitor.routes.api_grow_ws import _clear_auth_cache
    _clear_auth_cache()

    from flask import Flask
    from mlss_monitor.routes.api_grow_units import api_grow_units_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_units_bp)
    yield app.test_client(), tmp.name, raw1, raw2

    _clear_auth_cache()


# ---------------------------------------------------------------------------
# POST /api/grow/units/<id>/rotate-token
# ---------------------------------------------------------------------------


def test_rotate_token_returns_201_with_raw_token(client):
    c, _, _, _ = client
    _set_session(c, role="admin")
    r = c.post("/api/grow/units/1/rotate-token")
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert "token" in body
    # secrets.token_urlsafe(32) → 43 chars (matches _EXPECTED_TOKEN_LEN
    # in api_grow_ws.py — if this drifts, the WS bearer pre-filter will
    # reject the freshly-rotated token).
    assert len(body["token"]) == 43


def test_rotate_token_replaces_hash_in_grow_units(client):
    """The new hash on the row must verify against the new raw, not the old."""
    from mlss_monitor.grow.auth import verify_secret
    c, db_path, raw1, _ = client
    _set_session(c, role="admin")

    # Snapshot the original hash
    conn = sqlite3.connect(db_path)
    orig_hash = conn.execute(
        "SELECT bearer_token_hash FROM grow_units WHERE id=1"
    ).fetchone()[0]
    conn.close()

    r = c.post("/api/grow/units/1/rotate-token")
    new_token = r.get_json()["token"]

    conn = sqlite3.connect(db_path)
    new_hash = conn.execute(
        "SELECT bearer_token_hash FROM grow_units WHERE id=1"
    ).fetchone()[0]
    conn.close()

    assert new_hash != orig_hash, "hash must be rotated"
    assert verify_secret(new_token, new_hash) is True
    # The OLD raw must NOT match the NEW hash
    assert verify_secret(raw1, new_hash) is False


def test_rotate_token_invalidates_old_token_immediately(client):
    """After rotation, the old raw token no longer verifies against the
    DB hash — even before cache eviction runs (it's a different hash)."""
    from mlss_monitor.grow.auth import verify_secret
    c, db_path, raw1, _ = client
    _set_session(c, role="admin")
    c.post("/api/grow/units/1/rotate-token")

    conn = sqlite3.connect(db_path)
    new_hash = conn.execute(
        "SELECT bearer_token_hash FROM grow_units WHERE id=1"
    ).fetchone()[0]
    conn.close()
    assert verify_secret(raw1, new_hash) is False


def test_rotate_token_stashes_raw_for_peek_once(client):
    c, db_path, _, _ = client
    _set_session(c, role="admin")
    r = c.post("/api/grow/units/1/rotate-token")
    new_token = r.get_json()["token"]

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT value FROM app_settings "
        "WHERE key='grow_unit_1_token_pending_reveal'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == new_token


def test_rotate_token_404_for_unknown_unit(client):
    c, db_path, _, _ = client
    _set_session(c, role="admin")
    r = c.post("/api/grow/units/99999/rotate-token")
    assert r.status_code == 404
    # No app_settings stash should have been created for the missing unit
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT value FROM app_settings "
        "WHERE key='grow_unit_99999_token_pending_reveal'"
    ).fetchone()
    conn.close()
    assert row is None, "404 must not stash anything"


def test_rotate_token_admin_only(client):
    c, _, _, _ = client
    # Anonymous → 401
    _set_session(c, logged_in=False, role="viewer")
    r = c.post("/api/grow/units/1/rotate-token")
    assert r.status_code == 401
    # Viewer → 403
    _set_session(c, logged_in=True, role="viewer")
    r = c.post("/api/grow/units/1/rotate-token")
    assert r.status_code == 403
    # Controller → 403
    _set_session(c, logged_in=True, role="controller")
    r = c.post("/api/grow/units/1/rotate-token")
    assert r.status_code == 403
    # Admin → 201
    _set_session(c, logged_in=True, role="admin")
    r = c.post("/api/grow/units/1/rotate-token")
    assert r.status_code == 201


def test_rotate_token_invalidates_auth_cache_entries_for_that_unit(client):
    """Pre-populate a cache entry for unit 1, rotate, assert entry gone."""
    import time as _time
    from mlss_monitor.routes import api_grow_ws
    c, _, raw1, _ = client
    _set_session(c, role="admin")

    # Pre-populate the cache exactly as _validate_bearer would.
    with api_grow_ws._auth_cache_lock:
        api_grow_ws._auth_cache[(1, raw1)] = _time.monotonic()
    assert (1, raw1) in api_grow_ws._auth_cache

    r = c.post("/api/grow/units/1/rotate-token")
    assert r.status_code == 201

    assert (1, raw1) not in api_grow_ws._auth_cache, \
        "rotation must drop cached entries for the unit"


def test_rotate_token_does_not_affect_other_units_cache(client):
    """Pre-populate cache entries for unit 1 AND unit 2, rotate unit 1,
    assert unit 2's entry survived. Cache eviction is per-unit, not global."""
    import time as _time
    from mlss_monitor.routes import api_grow_ws
    c, _, raw1, raw2 = client
    _set_session(c, role="admin")

    now = _time.monotonic()
    with api_grow_ws._auth_cache_lock:
        api_grow_ws._auth_cache[(1, raw1)] = now
        api_grow_ws._auth_cache[(2, raw2)] = now

    c.post("/api/grow/units/1/rotate-token")

    assert (1, raw1) not in api_grow_ws._auth_cache
    assert (2, raw2) in api_grow_ws._auth_cache, \
        "unit 2's cache entry must not be touched by unit 1's rotation"


def test_rotate_token_404_does_not_invalidate_cache(client):
    """Sanity: a 404 rotation against a missing unit must not nuke real
    units' cache entries (defence against typo'd unit IDs)."""
    import time as _time
    from mlss_monitor.routes import api_grow_ws
    c, _, raw1, _ = client
    _set_session(c, role="admin")
    with api_grow_ws._auth_cache_lock:
        api_grow_ws._auth_cache[(1, raw1)] = _time.monotonic()
    r = c.post("/api/grow/units/99999/rotate-token")
    assert r.status_code == 404
    assert (1, raw1) in api_grow_ws._auth_cache


# ---------------------------------------------------------------------------
# GET /api/grow/units/<id>/token/peek-once
# ---------------------------------------------------------------------------


def test_peek_token_returns_stashed_value_and_deletes_it(client):
    """After rotate, peek returns the token; second peek is 410 Gone."""
    c, _, _, _ = client
    _set_session(c, role="admin")
    rotate = c.post("/api/grow/units/1/rotate-token")
    new_token = rotate.get_json()["token"]

    r1 = c.get("/api/grow/units/1/token/peek-once")
    assert r1.status_code == 200
    assert r1.get_json()["token"] == new_token

    # Second peek must be 410 — the stash is consumed
    r2 = c.get("/api/grow/units/1/token/peek-once")
    assert r2.status_code == 410


def test_peek_token_410_when_no_pending_reveal(client):
    """A peek with no prior rotation returns 410, not 200 with a stale
    token from some other workflow."""
    c, _, _, _ = client
    _set_session(c, role="admin")
    r = c.get("/api/grow/units/1/token/peek-once")
    assert r.status_code == 410
    assert r.get_json()["error"] == "already_revealed"


def test_peek_token_admin_only(client):
    c, _, _, _ = client
    # Pre-stash a value via rotate so the absence-of-stash case doesn't
    # mask a missing decorator (a bare 410 looks the same to the test
    # client whether the decorator ran or not).
    _set_session(c, role="admin")
    c.post("/api/grow/units/1/rotate-token")

    _set_session(c, logged_in=False, role="viewer")
    r = c.get("/api/grow/units/1/token/peek-once")
    assert r.status_code == 401

    _set_session(c, logged_in=True, role="viewer")
    r = c.get("/api/grow/units/1/token/peek-once")
    assert r.status_code == 403

    _set_session(c, logged_in=True, role="controller")
    r = c.get("/api/grow/units/1/token/peek-once")
    assert r.status_code == 403

    # Admin still picks up the stashed value (defence in depth: failed
    # peek attempts must NOT delete it)
    _set_session(c, logged_in=True, role="admin")
    r = c.get("/api/grow/units/1/token/peek-once")
    assert r.status_code == 200
    assert "token" in r.get_json()


def test_peek_token_isolated_per_unit(client):
    """Rotating unit 1 must not affect unit 2's peek state, and vice versa."""
    c, _, _, _ = client
    _set_session(c, role="admin")
    r1 = c.post("/api/grow/units/1/rotate-token")
    token1 = r1.get_json()["token"]

    # Unit 2 has nothing pending — peek returns 410
    r = c.get("/api/grow/units/2/token/peek-once")
    assert r.status_code == 410

    # Unit 1's stash is intact (unit 2's failed peek didn't consume it)
    r = c.get("/api/grow/units/1/token/peek-once")
    assert r.status_code == 200
    assert r.get_json()["token"] == token1
