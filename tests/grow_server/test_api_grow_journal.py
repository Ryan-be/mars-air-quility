"""Phase 4 #7 — operator journal entries (notes pinned to a timestamp).

CRUD + RBAC + author-or-admin gate + range filter.

Endpoints under test:
  GET    /api/grow/units/<id>/journal?range=24h
  POST   /api/grow/units/<id>/journal
  PATCH  /api/grow/units/<id>/journal/<entry_id>
  DELETE /api/grow/units/<id>/journal/<entry_id>
"""
import sqlite3
import tempfile
from datetime import datetime, timedelta

import pytest


def _set_session(c, *, role="admin", user="alice"):
    with c.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_role"] = role
        sess["user"] = user


@pytest.fixture
def client(monkeypatch):
    """Fresh DB with two active units. Each test gets a clean slate."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_journal.DB_FILE", tmp.name
    )
    init_db.create_db()

    now = datetime.utcnow()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, is_active) "
        "VALUES (1, 'hw-1', 'Tom 1', ?, 'h', ?, 1)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, is_active) "
        "VALUES (2, 'hw-2', 'Basil 2', ?, 'h', ?, 1)",
        (now, now),
    )
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_journal import api_grow_journal_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_journal_bp)
    c = app.test_client()
    _set_session(c, role="admin", user="alice")
    yield c, tmp.name


def _seed_entry(db_path, *, unit_id=1, author="alice", body="seed",
                ts=None, created_at=None):
    """Insert a row directly so list/edit/delete tests have something to
    work against. Returns the new id."""
    if ts is None:
        ts = datetime.utcnow()
    if created_at is None:
        created_at = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO grow_journal_entries "
        "(unit_id, timestamp_utc, author, body, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (unit_id, ts, author, body, created_at),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


# ---------------------------------------------------------------------------
# POST — create
# ---------------------------------------------------------------------------


def test_post_creates_entry_and_returns_201(client):
    c, _db = client
    ts = datetime.utcnow().isoformat() + "Z"
    r = c.post("/api/grow/units/1/journal", json={
        "timestamp_utc": ts, "body": "Started bloom nutrients"
    })
    assert r.status_code == 201
    body = r.get_json()
    assert body["unit_id"] == 1
    assert body["author"] == "alice"
    assert body["body"] == "Started bloom nutrients"
    assert body["created_at"] is not None
    assert body["updated_at"] is None


def test_post_404_for_unknown_unit(client):
    c, _db = client
    r = c.post("/api/grow/units/9999/journal", json={
        "timestamp_utc": datetime.utcnow().isoformat(),
        "body": "x",
    })
    assert r.status_code == 404


def test_post_404_for_soft_deleted_unit(client):
    c, db = client
    conn = sqlite3.connect(db)
    conn.execute("UPDATE grow_units SET is_active=0 WHERE id=1")
    conn.commit()
    conn.close()
    r = c.post("/api/grow/units/1/journal", json={
        "timestamp_utc": datetime.utcnow().isoformat(),
        "body": "x",
    })
    assert r.status_code == 404


def test_post_400_on_empty_body(client):
    c, _db = client
    r = c.post("/api/grow/units/1/journal", json={
        "timestamp_utc": datetime.utcnow().isoformat(), "body": "   "
    })
    assert r.status_code == 400


def test_post_400_on_missing_timestamp(client):
    c, _db = client
    r = c.post("/api/grow/units/1/journal", json={"body": "x"})
    assert r.status_code == 400


def test_post_400_on_invalid_timestamp(client):
    c, _db = client
    r = c.post("/api/grow/units/1/journal", json={
        "timestamp_utc": "not-a-date", "body": "x",
    })
    assert r.status_code == 400


def test_post_403_for_viewer(client):
    c, _db = client
    _set_session(c, role="viewer", user="alice")
    r = c.post("/api/grow/units/1/journal", json={
        "timestamp_utc": datetime.utcnow().isoformat(), "body": "x",
    })
    assert r.status_code == 403


def test_post_400_on_oversized_body(client):
    c, _db = client
    huge = "x" * 5000
    r = c.post("/api/grow/units/1/journal", json={
        "timestamp_utc": datetime.utcnow().isoformat(), "body": huge,
    })
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET — list
# ---------------------------------------------------------------------------


def test_get_returns_entries_for_unit_only(client):
    c, db = client
    _seed_entry(db, unit_id=1, body="for unit 1")
    _seed_entry(db, unit_id=2, body="for unit 2")
    r = c.get("/api/grow/units/1/journal?range=24h")
    assert r.status_code == 200
    bodies = [e["body"] for e in r.get_json()]
    assert bodies == ["for unit 1"]


def test_get_filters_by_range(client):
    c, db = client
    now = datetime.utcnow()
    # In-range: -2h
    _seed_entry(db, unit_id=1, body="recent", ts=now - timedelta(hours=2))
    # Out of range: -2d
    _seed_entry(db, unit_id=1, body="old", ts=now - timedelta(days=2))
    r = c.get("/api/grow/units/1/journal?range=24h")
    bodies = [e["body"] for e in r.get_json()]
    assert bodies == ["recent"]
    # 7d range picks up both
    r2 = c.get("/api/grow/units/1/journal?range=7d")
    bodies2 = [e["body"] for e in r2.get_json()]
    assert set(bodies2) == {"recent", "old"}


def test_get_returns_descending_by_timestamp(client):
    c, db = client
    now = datetime.utcnow()
    _seed_entry(db, body="oldest", ts=now - timedelta(hours=10))
    _seed_entry(db, body="middle", ts=now - timedelta(hours=5))
    _seed_entry(db, body="newest", ts=now - timedelta(hours=1))
    r = c.get("/api/grow/units/1/journal?range=24h")
    bodies = [e["body"] for e in r.get_json()]
    assert bodies == ["newest", "middle", "oldest"]


def test_get_400_invalid_range(client):
    c, _db = client
    r = c.get("/api/grow/units/1/journal?range=bogus")
    assert r.status_code == 400


def test_get_returns_empty_array_for_unit_with_no_entries(client):
    c, _db = client
    r = c.get("/api/grow/units/1/journal?range=24h")
    assert r.status_code == 200
    assert r.get_json() == []


def test_get_works_for_viewer_role(client):
    c, db = client
    _seed_entry(db, body="viewer-readable")
    _set_session(c, role="viewer")
    r = c.get("/api/grow/units/1/journal")
    assert r.status_code == 200
    assert len(r.get_json()) == 1


# ---------------------------------------------------------------------------
# PATCH — edit
# ---------------------------------------------------------------------------


def test_patch_author_can_edit_their_own_entry(client):
    c, db = client
    eid = _seed_entry(db, author="alice", body="orig")
    _set_session(c, role="controller", user="alice")
    r = c.patch(f"/api/grow/units/1/journal/{eid}",
                json={"body": "edited"})
    assert r.status_code == 200
    assert r.get_json()["body"] == "edited"
    assert r.get_json()["updated_at"] is not None


def test_patch_admin_can_edit_someone_elses_entry(client):
    c, db = client
    eid = _seed_entry(db, author="bob", body="bobs note")
    # Session is admin (alice) by default
    r = c.patch(f"/api/grow/units/1/journal/{eid}",
                json={"body": "admin edit"})
    assert r.status_code == 200
    assert r.get_json()["body"] == "admin edit"


def test_patch_controller_cannot_edit_someone_elses_entry(client):
    c, db = client
    eid = _seed_entry(db, author="bob", body="bobs note")
    _set_session(c, role="controller", user="alice")
    r = c.patch(f"/api/grow/units/1/journal/{eid}",
                json={"body": "alice attempt"})
    assert r.status_code == 403


def test_patch_404_for_unknown_id(client):
    c, _db = client
    r = c.patch("/api/grow/units/1/journal/99999",
                json={"body": "x"})
    assert r.status_code == 404


def test_patch_404_for_id_under_wrong_unit(client):
    """Cross-unit security: editing entry-1 via /units/2/journal/1 must 404."""
    c, db = client
    eid = _seed_entry(db, unit_id=1, body="x")
    r = c.patch(f"/api/grow/units/2/journal/{eid}",
                json={"body": "y"})
    assert r.status_code == 404


def test_patch_400_on_empty_body(client):
    c, db = client
    eid = _seed_entry(db, body="orig")
    r = c.patch(f"/api/grow/units/1/journal/{eid}", json={"body": ""})
    assert r.status_code == 400


def test_patch_403_for_viewer(client):
    c, db = client
    eid = _seed_entry(db, body="orig")
    _set_session(c, role="viewer")
    r = c.patch(f"/api/grow/units/1/journal/{eid}", json={"body": "x"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


def test_delete_author_can_delete_own_entry(client):
    c, db = client
    eid = _seed_entry(db, author="alice")
    _set_session(c, role="controller", user="alice")
    r = c.delete(f"/api/grow/units/1/journal/{eid}")
    assert r.status_code == 200
    # Confirm gone
    r2 = c.get("/api/grow/units/1/journal")
    assert len(r2.get_json()) == 0


def test_delete_admin_can_delete_someone_elses(client):
    c, db = client
    eid = _seed_entry(db, author="bob")
    # Default session is admin (alice)
    r = c.delete(f"/api/grow/units/1/journal/{eid}")
    assert r.status_code == 200


def test_delete_controller_cannot_delete_someone_elses(client):
    c, db = client
    eid = _seed_entry(db, author="bob")
    _set_session(c, role="controller", user="alice")
    r = c.delete(f"/api/grow/units/1/journal/{eid}")
    assert r.status_code == 403


def test_delete_404_for_unknown(client):
    c, _db = client
    r = c.delete("/api/grow/units/1/journal/99999")
    assert r.status_code == 404


def test_delete_404_for_cross_unit(client):
    c, db = client
    eid = _seed_entry(db, unit_id=1)
    r = c.delete(f"/api/grow/units/2/journal/{eid}")
    assert r.status_code == 404


def test_delete_403_for_viewer(client):
    c, db = client
    eid = _seed_entry(db, author="alice")
    _set_session(c, role="viewer")
    r = c.delete(f"/api/grow/units/1/journal/{eid}")
    assert r.status_code == 403
