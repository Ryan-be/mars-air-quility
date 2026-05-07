"""Phase 3 Task 4 — Diagnostics tab "Danger Zone" actions.

Three admin-only endpoints:

  * DELETE /api/grow/units/<id>          — soft-delete (is_active=0).
                                           Telemetry history + grow_photos
                                           are preserved.
  * POST   /api/grow/units/<id>/clear-buffer — synchronous WS push of a
                                           {"name": "clear_buffer"} command.
  * DELETE /api/grow/units/<id>/photos   — wipe every photo (DB rows +
                                           JPEG files on disk).

Tests cover:
  * RBAC (admin-only — viewer + controller get 403)
  * 404 handling
  * Soft-delete preserves grow_telemetry rows (no cascade)
  * clear-buffer pushes the right WS payload + returns 503 when the unit
    is disconnected
  * clear-photos deletes both DB rows and JPEG files; tolerates missing
    files; returns 0 for empty units; preserves telemetry / watering rows
"""
import asyncio
import json
import os
import sqlite3
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import pytest


def _set_session(c, *, logged_in=True, role="admin"):
    with c.session_transaction() as sess:
        sess["logged_in"] = logged_in
        sess["user_role"] = role


@pytest.fixture
def client(monkeypatch):
    """Mount the api_grow_units blueprint against a fresh DB seeded with
    one active grow_unit (id=1). For clear-buffer tests the fixture also
    spins up a fake WS registry + listener loop so the synchronous push
    path has somewhere to send to.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_units.DB_FILE", tmp.name
    )
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    monkeypatch.setattr("mlss_monitor.grow.health_watchdog.DB_FILE", tmp.name)
    init_db.create_db()

    now = datetime.utcnow()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, is_active) "
        "VALUES (1, 'hw-1', 'Tom 1', ?, 'h', ?, 1)",
        (now, now),
    )
    conn.commit()
    conn.close()

    # Fake WS registry + listener loop so the clear-buffer push path has
    # somewhere to land — same plumbing as test_api_grow_config.client.
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

    from flask import Flask
    from mlss_monitor.routes.api_grow_units import api_grow_units_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_units_bp)
    test_client = app.test_client()
    _set_session(test_client, role="admin")

    yield test_client, fake_ws, tmp.name

    captured["loop"].call_soon_threadsafe(captured["loop"].stop)
    state.grow_ws_loop = None
    state.grow_ws_registry = None


# ---------------------------------------------------------------------------
# DELETE /api/grow/units/<id> — soft-delete
# ---------------------------------------------------------------------------


def test_delete_unit_soft_deletes_sets_is_active_zero(client):
    """admin DELETE → 200 + grow_units.is_active flips to 0.

    No cascade — telemetry + photo rows must NOT be deleted (proven by
    test_delete_unit_preserves_telemetry_history below).
    """
    c, _, db_path = client
    r = c.delete("/api/grow/units/1")
    assert r.status_code == 200, r.data
    assert r.get_json() == {"ok": True}
    conn = sqlite3.connect(db_path)
    is_active = conn.execute(
        "SELECT is_active FROM grow_units WHERE id=1"
    ).fetchone()[0]
    conn.close()
    assert is_active == 0


def test_delete_unit_admin_only_viewer_gets_403(client):
    c, _, _ = client
    _set_session(c, role="viewer")
    r = c.delete("/api/grow/units/1")
    assert r.status_code == 403


def test_delete_unit_admin_only_controller_gets_403(client):
    c, _, _ = client
    _set_session(c, role="controller")
    r = c.delete("/api/grow/units/1")
    assert r.status_code == 403


def test_delete_unit_404_for_unknown(client):
    c, _, _ = client
    r = c.delete("/api/grow/units/9999")
    assert r.status_code == 404
    assert r.get_json()["error"] == "unit_not_found"


def test_delete_unit_404_for_already_deleted(client):
    """Idempotency check: a unit already soft-deleted (is_active=0) is
    indistinguishable from a never-existed unit — both return 404.

    This means a retried DELETE doesn't accidentally touch a different
    row that happens to have the same id (impossible in SQLite, but the
    is_active=1 guard makes the contract explicit)."""
    c, _, _ = client
    # Soft-delete first, then try again.
    r1 = c.delete("/api/grow/units/1")
    assert r1.status_code == 200
    r2 = c.delete("/api/grow/units/1")
    assert r2.status_code == 404


def test_delete_unit_preserves_telemetry_history(client):
    """Soft-delete must NOT cascade-delete grow_telemetry rows.

    Audit + forensics: even after a unit is decommissioned, the telemetry
    it produced while live remains queryable. This is the rationale for
    soft-delete vs hard-DELETE — the operator can revive the unit later
    via a manual UPDATE without losing context.
    """
    c, _, db_path = client
    # Seed one telemetry row + one watering event for unit 1
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_telemetry "
        "(unit_id, timestamp_utc, soil_moisture_raw, soil_moisture_pct, "
        " light_state, pump_state) "
        "VALUES (1, ?, 612, 58, 1, 0)",
        (now,),
    )
    conn.execute(
        "INSERT INTO grow_watering_events "
        "(unit_id, timestamp_utc, trigger, duration_s, triggered_by) "
        "VALUES (1, ?, 'manual', 5, 'user')",
        (now,),
    )
    conn.commit()
    conn.close()

    r = c.delete("/api/grow/units/1")
    assert r.status_code == 200

    # Telemetry + watering history rows still present
    conn = sqlite3.connect(db_path)
    tel_count = conn.execute(
        "SELECT COUNT(*) FROM grow_telemetry WHERE unit_id=1"
    ).fetchone()[0]
    we_count = conn.execute(
        "SELECT COUNT(*) FROM grow_watering_events WHERE unit_id=1"
    ).fetchone()[0]
    conn.close()
    assert tel_count == 1, "telemetry must survive soft-delete"
    assert we_count == 1, "watering events must survive soft-delete"


def test_delete_unit_drops_unit_from_list_endpoint(client):
    """End-to-end: after DELETE, the unit no longer appears in
    GET /api/grow/units (which filters on is_active=1)."""
    c, _, _ = client
    # Confirm baseline visibility
    r0 = c.get("/api/grow/units")
    assert any(u["id"] == 1 for u in r0.get_json()["units"])
    # Soft-delete + re-list
    c.delete("/api/grow/units/1")
    r1 = c.get("/api/grow/units")
    assert all(u["id"] != 1 for u in r1.get_json()["units"]), \
        "soft-deleted unit must disappear from fleet view"


# ---------------------------------------------------------------------------
# POST /api/grow/units/<id>/clear-buffer — synchronous WS push
# ---------------------------------------------------------------------------


def test_clear_buffer_pushes_command_via_ws(client):
    """admin POST → fake registry's send received {"name":"clear_buffer"}.

    Mirrors the safety_override push contract: 202 + {"queued": true} on
    confirmed delivery; the payload uses the legacy `name`-keyed shape
    (same as identify / water_now) since the firmware dispatcher routes
    on `name=="clear_buffer"`.
    """
    c, fake_ws, _ = client
    r = c.post("/api/grow/units/1/clear-buffer")
    assert r.status_code == 202, r.data
    assert r.get_json() == {"queued": True}
    assert len(fake_ws.sent) == 1
    cmd = json.loads(fake_ws.sent[0])
    assert cmd["type"] == "command"
    assert cmd["payload"] == {"name": "clear_buffer"}


def test_clear_buffer_returns_503_when_unit_disconnected(monkeypatch):
    """Unit not in registry → 503 unit_not_connected.

    Same contract as safety_override: clear-buffer is intent-to-act-now,
    so a 503 surfaces clearly rather than silently being best-effort.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_units.DB_FILE", tmp.name
    )
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    init_db.create_db()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, is_active) "
        "VALUES (1, 'hw-1', 'Tom 1', ?, 'h', ?, 1)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.commit()
    conn.close()

    from mlss_monitor import state
    state.grow_ws_registry = None
    state.grow_ws_loop = None

    from flask import Flask
    from mlss_monitor.routes.api_grow_units import api_grow_units_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_units_bp)
    tc = app.test_client()
    with tc.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_role"] = "admin"

    r = tc.post("/api/grow/units/1/clear-buffer")
    assert r.status_code == 503
    assert r.get_json()["error"] == "unit_not_connected"


def test_clear_buffer_admin_only_viewer_gets_403(client):
    c, _, _ = client
    _set_session(c, role="viewer")
    r = c.post("/api/grow/units/1/clear-buffer")
    assert r.status_code == 403


def test_clear_buffer_admin_only_controller_gets_403(client):
    c, _, _ = client
    _set_session(c, role="controller")
    r = c.post("/api/grow/units/1/clear-buffer")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/grow/units/<id>/photos — clear-all-photos
# ---------------------------------------------------------------------------


@pytest.fixture
def photos_client(client, tmp_path, monkeypatch):
    """Extends the danger fixture with grow_images_dir wired to tmp_path
    + three photos seeded for unit 1 (DB rows + matching JPEGs on disk).

    Yields (test_client, db_path, images_root, photo_paths) so individual
    tests can verify both the DB and filesystem post-conditions.
    """
    c, _ws, db_path = client
    images_root = tmp_path / "imgs"
    images_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", str(images_root)
    )
    monkeypatch.setattr("mlss_monitor.grow.photo_storage.DB_FILE", db_path)

    photo_dir = images_root / "unit_001" / "2026-05-07"
    photo_dir.mkdir(parents=True)
    photo_paths = []
    for i in range(3):
        rel = f"unit_001/2026-05-07/13000{i}.jpg"
        abs_path = images_root / rel
        abs_path.write_bytes(b"\xff\xd8FAKEJPEG" + bytes([i]))
        photo_paths.append((rel, abs_path))

    conn = sqlite3.connect(db_path)
    base_ts = datetime.utcnow()
    for i, (rel, _abs) in enumerate(photo_paths):
        conn.execute(
            "INSERT INTO grow_photos (unit_id, taken_at, file_path, "
            "width_px, height_px, size_bytes) VALUES (1, ?, ?, 100, 100, 9)",
            (datetime(base_ts.year, base_ts.month, base_ts.day,
                      13, 0, i), rel),
        )
    conn.commit()
    conn.close()

    return c, db_path, images_root, photo_paths


def test_clear_photos_deletes_all_db_rows_and_files(photos_client):
    """admin DELETE /photos → 200 + {deleted_count: 3}; both rows + files gone."""
    c, db_path, _images_root, photo_paths = photos_client
    r = c.delete("/api/grow/units/1/photos")
    assert r.status_code == 200, r.data
    assert r.get_json() == {"deleted_count": 3}

    # DB rows gone
    conn = sqlite3.connect(db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM grow_photos WHERE unit_id=1"
    ).fetchone()[0]
    conn.close()
    assert n == 0, "all grow_photos rows for unit 1 must be deleted"

    # JPEGs gone from disk
    for _rel, abs_path in photo_paths:
        assert not abs_path.exists(), \
            f"JPEG {abs_path} should have been unlinked"


def test_clear_photos_returns_zero_for_unit_with_no_photos(client):
    """Empty unit: 200 + {deleted_count: 0}, NOT 404."""
    c, _ws, _db_path = client
    r = c.delete("/api/grow/units/1/photos")
    assert r.status_code == 200
    assert r.get_json() == {"deleted_count": 0}


def test_clear_photos_404_for_unknown_unit(client):
    """Bogus unit id → 404, like the rest of the danger-zone endpoints."""
    c, _ws, _db_path = client
    r = c.delete("/api/grow/units/9999/photos")
    assert r.status_code == 404
    assert r.get_json()["error"] == "unit_not_found"


def test_clear_photos_404_for_soft_deleted_unit(client):
    """Soft-deleted units must refuse photo wipes — same is_active=1
    guard as the soft-delete endpoint itself. Audit trail clarity:
    the unit is gone from the UI, so attempting to wipe its photos
    via the URL would silently succeed and confuse forensics later.
    """
    c, _ws, db_path = client
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE grow_units SET is_active=0 WHERE id=1")
    conn.commit()
    conn.close()
    r = c.delete("/api/grow/units/1/photos")
    assert r.status_code == 404


def test_clear_photos_admin_only_viewer_gets_403(photos_client):
    c, _db_path, _images_root, _photos = photos_client
    _set_session(c, role="viewer")
    r = c.delete("/api/grow/units/1/photos")
    assert r.status_code == 403


def test_clear_photos_admin_only_controller_gets_403(photos_client):
    c, _db_path, _images_root, _photos = photos_client
    _set_session(c, role="controller")
    r = c.delete("/api/grow/units/1/photos")
    assert r.status_code == 403


def test_clear_photos_tolerates_missing_jpeg_on_disk(photos_client):
    """If a JPEG has been hand-removed from disk (e.g. ops cleanup) the
    DB row must still be deleted. The end-state contract is "no rows,
    no files for this unit"; a row pointing at a non-existent file is
    a half-orphan that's harder to recover from than just deleting it.
    """
    c, db_path, _images_root, photo_paths = photos_client
    # Yank one JPEG out from under the row before the wipe
    photo_paths[0][1].unlink()

    r = c.delete("/api/grow/units/1/photos")
    assert r.status_code == 200
    # All three rows deleted (pre-removed file doesn't break the wipe)
    assert r.get_json() == {"deleted_count": 3}

    conn = sqlite3.connect(db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM grow_photos WHERE unit_id=1"
    ).fetchone()[0]
    conn.close()
    assert n == 0


def test_clear_photos_preserves_telemetry_and_watering_history(photos_client):
    """Photo wipe is scoped to grow_photos. Telemetry + watering history
    rows for the same unit must remain intact."""
    c, db_path, _images_root, _photos = photos_client
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_telemetry (unit_id, timestamp_utc, "
        "soil_moisture_raw, soil_moisture_pct, light_state, pump_state) "
        "VALUES (1, ?, 612, 58, 1, 0)", (now,),
    )
    conn.execute(
        "INSERT INTO grow_watering_events (unit_id, timestamp_utc, "
        "trigger, duration_s, triggered_by) "
        "VALUES (1, ?, 'manual', 5, 'user')", (now,),
    )
    conn.commit()
    conn.close()

    r = c.delete("/api/grow/units/1/photos")
    assert r.status_code == 200

    conn = sqlite3.connect(db_path)
    tel = conn.execute(
        "SELECT COUNT(*) FROM grow_telemetry WHERE unit_id=1"
    ).fetchone()[0]
    we = conn.execute(
        "SELECT COUNT(*) FROM grow_watering_events WHERE unit_id=1"
    ).fetchone()[0]
    conn.close()
    assert tel == 1, "photo wipe must not cascade-delete telemetry"
    assert we == 1, "photo wipe must not cascade-delete watering events"


def test_clear_photos_does_not_touch_other_units_photos(photos_client):
    """Wipe is scoped per unit_id. Photos belonging to a different unit
    must remain on disk and in the DB."""
    c, db_path, images_root, _photos = photos_client
    # Seed a second unit + one photo
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, is_active) "
        "VALUES (2, 'hw-2', 'Tom 2', ?, 'h', ?, 1)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    other_dir = images_root / "unit_002" / "2026-05-07"
    other_dir.mkdir(parents=True)
    other_rel = "unit_002/2026-05-07/120000.jpg"
    other_abs = images_root / other_rel
    other_abs.write_bytes(b"\xff\xd8KEEPME")
    conn.execute(
        "INSERT INTO grow_photos (unit_id, taken_at, file_path, "
        "width_px, height_px, size_bytes) VALUES (2, ?, ?, 100, 100, 6)",
        (datetime(2026, 5, 7, 12, 0, 0), other_rel),
    )
    conn.commit()
    conn.close()

    r = c.delete("/api/grow/units/1/photos")
    assert r.status_code == 200
    # Unit 2's row + file untouched
    conn = sqlite3.connect(db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM grow_photos WHERE unit_id=2"
    ).fetchone()[0]
    conn.close()
    assert n == 1
    assert other_abs.exists(), "unit 2's photo file must be left alone"
