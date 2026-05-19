"""Tests for /api/admin/backup/* admin endpoints (Phase 6 Tasks 19-20).

Five endpoints under /api/admin/backup/:
  GET    /config     — masked config (password_set, no cleartext)
  PUT    /config     — save + reconcile worker state + publish event
  GET    /status     — pipeline status + thread liveness + last snapshot
  POST   /test       — exercise connection with current credentials
  POST   /init       — apply server schema / create buckets
  POST   /maintenance — clear_outbox / pause / resume / force_rebootstrap

All endpoints require admin role. The shared ``db_path`` fixture from
``tests/conftest.py`` provides a tempfile SQLite primed with the full
live schema; we mount the blueprint on a minimal Flask app so tests
don't need to spin up the production sensor loop.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import closing
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from mlss_monitor import state
from mlss_monitor.backup import config
from mlss_monitor.event_bus import EventBus
from mlss_monitor.routes.api_backup import api_backup_bp


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def app(db_path):  # noqa: ARG001  (db_path patches DB_FILE module-wide)
    """Flask test client with the api_backup blueprint mounted.

    ``db_path`` from conftest.py primes the tempfile with the full
    schema (app_settings + outbox + bootstrap_progress) and patches
    every module-level DB_FILE that backup code paths read, so
    ``config.load/save``, ``state.event_bus`` access, and the worker
    reconcile helper all hit the tempfile.
    """
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_backup_bp)
    return app


@pytest.fixture
def client(app):
    """Bare test client — no session set. Tests that need an admin
    session call ``_login_admin(client)``."""
    return app.test_client()


@pytest.fixture
def event_bus():
    """Fresh event bus + reset state.backup_*_worker around each test
    so worker reconciliation can be observed cleanly."""
    bus = EventBus()
    state.event_bus = bus
    state.backup_db_worker = None
    state.backup_files_worker = None
    yield bus
    # Stop any worker that a test may have left behind.
    for attr in ("backup_db_worker", "backup_files_worker"):
        worker = getattr(state, attr, None)
        if worker is not None and getattr(worker, "_thread", None):
            try:
                worker.stop(timeout=1.0)
            except Exception:  # pylint: disable=broad-except
                pass
        setattr(state, attr, None)
    state.event_bus = None


def _login(client, *, role="admin"):
    """Open a Flask session with the requested role."""
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user"] = "test-user"
        sess["user_role"] = role
        sess["user_id"] = 1


# ─────────────────────────────────────────────────────────────────────
# RBAC matrix — every /api/admin/backup/* endpoint requires admin role
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("method,path,role,expected_status", [
    # Anonymous → 401 (require_role rejects unauthenticated sessions
    # before checking the role).
    ("GET",  "/api/admin/backup/config",            None,         401),
    ("PUT",  "/api/admin/backup/config",            None,         401),
    ("GET",  "/api/admin/backup/status",            None,         401),
    ("POST", "/api/admin/backup/test?pipeline=db",  None,         401),
    ("POST", "/api/admin/backup/init?pipeline=db",  None,         401),
    ("POST", "/api/admin/backup/maintenance",       None,         401),
    # Authenticated but non-admin → 403 (require_role enforces the
    # admin role discriminator after auth succeeds).
    ("GET",  "/api/admin/backup/config",            "viewer",     403),
    ("GET",  "/api/admin/backup/config",            "controller", 403),
    ("PUT",  "/api/admin/backup/config",            "viewer",     403),
    ("POST", "/api/admin/backup/maintenance",       "controller", 403),
])
def test_admin_backup_rbac_matrix(
    client, db_path, event_bus,  # noqa: ARG001
    method, path, role, expected_status,
):
    """Every /api/admin/backup/* endpoint must reject anonymous (401)
    and non-admin (403) callers. Happy-path admin behaviour is asserted
    by the per-endpoint tests below — those tests assume the access
    gate works."""
    if role:
        _login(client, role=role)
    if method == "GET":
        resp = client.get(path)
    elif method == "PUT":
        resp = client.put(path, json={})
    else:
        resp = client.post(path, json={})
    assert resp.status_code == expected_status


# ─────────────────────────────────────────────────────────────────────
# GET /config — response shape
# ─────────────────────────────────────────────────────────────────────


def test_get_config_admin_returns_masked(client, db_path, event_bus):  # noqa: ARG001
    """Admin should get the masked config — password_set boolean,
    no cleartext password / secret_key keys."""
    config.save({
        "db": {"host": "h", "password": "secret123"},
        "files": {"access_key_id": "AK", "secret_key": "SK"},
    })
    _login(client, role="admin")
    r = client.get("/api/admin/backup/config")
    assert r.status_code == 200
    body = r.get_json()
    assert body["db"]["password_set"] is True
    assert "password" not in body["db"]
    assert body["files"]["secret_key_set"] is True
    assert "secret_key" not in body["files"]


# ─────────────────────────────────────────────────────────────────────
# PUT /config — persistence + reconcile + hot-reload event
# ─────────────────────────────────────────────────────────────────────


def test_put_config_admin_persists(client, db_path, event_bus):  # noqa: ARG001
    _login(client, role="admin")
    r = client.put("/api/admin/backup/config", json={
        "db": {"host": "newhost", "password": "newpw"},
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["db"]["host"] == "newhost"
    # Response is masked
    assert "password" not in body["db"]
    assert body["db"]["password_set"] is True
    # Cleartext readable via the secret accessor
    assert config.get_secret("db", "password") == "newpw"


def test_put_config_empty_password_preserves_existing(client, db_path, event_bus):  # noqa: ARG001
    """Submitting an empty string for the password field is a UI
    gesture meaning 'preserve existing' — the stored secret must
    survive untouched."""
    config.save({"db": {"password": "original"}})
    _login(client, role="admin")
    r = client.put("/api/admin/backup/config", json={
        "db": {"host": "h", "password": ""},
    })
    assert r.status_code == 200
    assert config.get_secret("db", "password") == "original"


def test_put_config_publishes_backup_config_changed(client, db_path, event_bus):
    """After save + reconcile, the route must publish
    ``backup_config_changed`` so any still-running workers hot-reload."""
    sub = event_bus.subscribe()
    _login(client, role="admin")
    r = client.put("/api/admin/backup/config", json={
        "db": {"host": "h"},
    })
    assert r.status_code == 200
    # Drain the queue and look for our event
    events: list[str] = []
    while not sub.empty():
        msg = sub.get_nowait()
        events.append(msg["event"])
    assert "backup_config_changed" in events


def test_put_config_enable_starts_worker(client, db_path, event_bus):
    """Flipping enabled False → True must create + start the worker
    thread (per the user's 'worker only runs when enabled' rule)."""
    assert state.backup_db_worker is None
    _login(client, role="admin")
    # Patch BackupWorker.start to avoid the live drain loop; we still
    # want the real instance so attribute access (_thread, .stop) works.
    with patch(
        "mlss_monitor.routes.api_backup.BackupWorker"
    ) as mock_cls:
        worker_instance = MagicMock()
        mock_cls.return_value = worker_instance
        r = client.put("/api/admin/backup/config", json={
            "enabled": True,
            "db": {"enabled": True},
        })
        assert r.status_code == 200
        # Worker was constructed for the db pipeline + started
        mock_cls.assert_any_call(pipeline="db", event_bus=event_bus)
        worker_instance.start.assert_called()
    # The (mock) worker is now held in state
    assert state.backup_db_worker is not None


def test_put_config_disable_stops_worker(client, db_path, event_bus):
    """Flipping enabled True → False must stop the worker thread."""
    # Seed: backups enabled, both pipelines on, mock worker installed.
    config.save({
        "enabled": True,
        "db": {"enabled": True},
        "files": {"enabled": True},
    })
    mock_worker = MagicMock()
    mock_worker._thread = MagicMock(is_alive=lambda: True)
    state.backup_db_worker = mock_worker
    _login(client, role="admin")

    r = client.put("/api/admin/backup/config", json={"enabled": False})
    assert r.status_code == 200
    mock_worker.stop.assert_called()
    assert state.backup_db_worker is None


def test_put_config_unchanged_enabled_does_not_restart(client, db_path, event_bus):
    """When the pipeline was enabled and stays enabled, the existing
    worker should NOT be stopped/restarted — the published event drives
    hot-reload instead."""
    config.save({
        "enabled": True, "db": {"enabled": True},
    })
    mock_worker = MagicMock()
    mock_worker._thread = MagicMock(is_alive=lambda: True)
    state.backup_db_worker = mock_worker
    _login(client, role="admin")

    r = client.put("/api/admin/backup/config", json={
        "db": {"host": "different.local"},
    })
    assert r.status_code == 200
    mock_worker.stop.assert_not_called()
    # Worker preserved
    assert state.backup_db_worker is mock_worker


# ─────────────────────────────────────────────────────────────────────
# GET /status — payload shape
# ─────────────────────────────────────────────────────────────────────


def test_get_status_admin_returns_payload(client, db_path, event_bus):
    """Status payload should describe both pipelines + the latest
    snapshot from the event-bus history (or None when no snapshot
    has been published yet)."""
    # Publish a status event so the route's snapshot lookup finds something.
    event_bus.publish("backup_status_changed", {
        "pipeline": "db", "state": "idle", "pending_rows": 0,
    })
    _login(client, role="admin")
    r = client.get("/api/admin/backup/status")
    assert r.status_code == 200
    body = r.get_json()
    assert "enabled" in body
    assert "paused" in body
    assert "pipelines" in body
    assert "db" in body["pipelines"]
    assert "files" in body["pipelines"]
    db = body["pipelines"]["db"]
    assert "enabled" in db
    assert "thread_alive" in db
    assert "snapshot" in db
    # We published a db snapshot above — it should round-trip
    assert db["snapshot"]["state"] == "idle"
    # No files snapshot was published, so that key is None
    assert body["pipelines"]["files"]["snapshot"] is None


def test_get_status_thread_alive_reflects_worker(client, db_path, event_bus):
    """``thread_alive`` must reflect ``worker._thread.is_alive()``. A
    mock worker with a live thread shows True; absent worker shows
    False."""
    mock_worker = MagicMock()
    mock_worker._thread = MagicMock(is_alive=lambda: True)
    state.backup_db_worker = mock_worker
    _login(client, role="admin")
    r = client.get("/api/admin/backup/status")
    body = r.get_json()
    assert body["pipelines"]["db"]["thread_alive"] is True
    assert body["pipelines"]["files"]["thread_alive"] is False


# ─────────────────────────────────────────────────────────────────────
# POST /test — connection probe
# ─────────────────────────────────────────────────────────────────────


def test_post_test_invalid_pipeline_returns_400(client, db_path, event_bus):  # noqa: ARG001
    _login(client, role="admin")
    r = client.post("/api/admin/backup/test?pipeline=bogus")
    assert r.status_code == 400


def test_post_test_db_calls_postgres_test_connection(client, db_path, event_bus):  # noqa: ARG001
    """The db variant must build a PostgresClient with current
    config + call its test_connection method."""
    config.save({
        "db": {"host": "h", "port": 5432, "database": "mlss",
               "user": "u", "password": "p"},
    })
    _login(client, role="admin")
    with patch(
        "mlss_monitor.routes.api_backup.PostgresClient"
    ) as mock_cls:
        mock_cls.return_value.test_connection.return_value = {
            "ok": True, "version": "PG 15"
        }
        r = client.post("/api/admin/backup/test?pipeline=db")
        assert r.status_code == 200
        assert r.get_json() == {"ok": True, "version": "PG 15"}
        mock_cls.assert_called_once()


def test_post_test_files_calls_s3_test_connection(client, db_path, event_bus):  # noqa: ARG001
    """The files variant builds an S3Client + calls test_connection."""
    config.save({
        "files": {"endpoint": "https://x", "access_key_id": "AK",
                  "secret_key": "SK"},
    })
    _login(client, role="admin")
    with patch(
        "mlss_monitor.routes.api_backup.S3Client"
    ) as mock_cls:
        mock_cls.return_value.test_connection.return_value = {"ok": True}
        r = client.post("/api/admin/backup/test?pipeline=files")
        assert r.status_code == 200
        assert r.get_json() == {"ok": True}


# ─────────────────────────────────────────────────────────────────────
# POST /init — server-side schema / bucket creation
# ─────────────────────────────────────────────────────────────────────


def test_post_init_invalid_pipeline_returns_400(client, db_path, event_bus):  # noqa: ARG001
    _login(client, role="admin")
    r = client.post("/api/admin/backup/init?pipeline=bogus")
    assert r.status_code == 400


def test_post_init_db_returns_stub(client, db_path, event_bus):  # noqa: ARG001
    """DB init is a Phase 9 placeholder — should return 200 with a
    message rather than crash."""
    _login(client, role="admin")
    r = client.post("/api/admin/backup/init?pipeline=db")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert "not yet implemented" in body["message"].lower()


def test_post_init_files_creates_all_buckets(client, db_path, event_bus):  # noqa: ARG001
    """Files init iterates the four known bucket suffixes and calls
    make_bucket(suffix) for each."""
    config.save({
        "files": {"endpoint": "https://x", "access_key_id": "AK",
                  "secret_key": "SK", "bucket_prefix": "mlss-"},
    })
    _login(client, role="admin")
    with patch(
        "mlss_monitor.routes.api_backup.S3Client"
    ) as mock_cls:
        mock_client = mock_cls.return_value
        r = client.post("/api/admin/backup/init?pipeline=files")
        assert r.status_code == 200
        # Exactly four make_bucket calls, one per known suffix
        suffixes_called = [call.args[0] for call in mock_client.make_bucket.call_args_list]
        assert set(suffixes_called) == {
            "photos", "anomaly", "multivar-anomaly", "attribution",
        }
        body = r.get_json()
        assert body["ok"] is True
        assert "mlss-photos" in body["buckets_created"]


# ─────────────────────────────────────────────────────────────────────
# POST /maintenance — admin actions
# ─────────────────────────────────────────────────────────────────────


def test_post_maintenance_missing_confirm_returns_400(client, db_path, event_bus):  # noqa: ARG001
    """All destructive actions gate on ``confirm: true``."""
    _login(client, role="admin")
    r = client.post(
        "/api/admin/backup/maintenance",
        json={"action": "clear_outbox"},
    )
    assert r.status_code == 400


def test_post_maintenance_unknown_action_returns_400(client, db_path, event_bus):  # noqa: ARG001
    _login(client, role="admin")
    r = client.post(
        "/api/admin/backup/maintenance",
        json={"action": "wat", "confirm": True},
    )
    assert r.status_code == 400


def test_post_maintenance_clear_outbox_empties_tables(client, db_path, event_bus):  # noqa: ARG001
    """clear_outbox wipes outbox_changes + outbox_blobs +
    outbox_delete_scope. Plant some entries first, then prove they're
    gone."""
    from mlss_monitor.backup import outbox
    with sqlite3.connect(db_path) as conn:
        with conn:
            outbox.enqueue_row(conn, table="sensor_data", pk=1)
            outbox.enqueue_blob(
                conn, kind="photo", source_path="/x.jpg",
                target_key="unit_001/x.jpg", sha256="a",
            )
            outbox.enqueue_delete_scope(
                conn, table="incidents", scope={},
            )

    _login(client, role="admin")
    r = client.post(
        "/api/admin/backup/maintenance",
        json={"action": "clear_outbox", "confirm": True},
    )
    assert r.status_code == 200

    with sqlite3.connect(db_path) as conn:
        assert outbox.pending_count_rows(conn) == 0
        assert outbox.pending_count_blobs(conn) == 0
        assert outbox.pending_count_delete_scope(conn) == 0


def test_post_maintenance_pause_flips_config_and_notifies_workers(
    client, db_path, event_bus,
):  # noqa: ARG001
    """pause must set ``paused=True`` in config + call ``_on_paused``
    on any live workers."""
    mock_worker = MagicMock()
    state.backup_db_worker = mock_worker
    _login(client, role="admin")

    r = client.post(
        "/api/admin/backup/maintenance",
        json={"action": "pause", "confirm": True},
    )
    assert r.status_code == 200
    assert config.load()["paused"] is True
    mock_worker._on_paused.assert_called()


def test_post_maintenance_resume_clears_pause_and_notifies(
    client, db_path, event_bus,
):  # noqa: ARG001
    """resume sets ``paused=False`` and notifies workers via
    _on_resumed."""
    config.save({"paused": True})
    mock_worker = MagicMock()
    state.backup_db_worker = mock_worker
    _login(client, role="admin")

    r = client.post(
        "/api/admin/backup/maintenance",
        json={"action": "resume", "confirm": True},
    )
    assert r.status_code == 200
    assert config.load()["paused"] is False
    mock_worker._on_resumed.assert_called()


def test_post_maintenance_force_rebootstrap_resets_progress(
    client, db_path, event_bus,
):  # noqa: ARG001
    """force_rebootstrap deletes every bootstrap_progress row + kicks
    off a fresh scan in a background thread. We only assert the
    reset side here — the thread is fire-and-forget."""
    # Seed bootstrap_progress with some progress markers.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO bootstrap_progress "
            "(pipeline, scope, last_pk, total_rows, started_at, completed_at) "
            "VALUES ('db', 'sensor_data', '42', 100, ?, ?)",
            ("2026-05-01", "2026-05-01"),
        )
        conn.execute(
            "INSERT INTO bootstrap_progress "
            "(pipeline, scope, last_pk, total_rows, started_at) "
            "VALUES ('files', '/foo', NULL, NULL, ?)",
            ("2026-05-01",),
        )
        conn.commit()

    _login(client, role="admin")
    # Patch the BootstrapScanner methods that the route spawns in a thread
    # so we don't actually walk the world during the test.
    with patch(
        "mlss_monitor.routes.api_backup.BootstrapScanner"
    ) as mock_cls:
        scanner = mock_cls.return_value
        # ``reset`` we want to behave like the real one (clears
        # bootstrap_progress rows) so the post-condition holds.
        from mlss_monitor.backup.bootstrap import BootstrapScanner as Real
        scanner.reset.side_effect = Real(db_path).reset
        r = client.post(
            "/api/admin/backup/maintenance",
            json={"action": "force_rebootstrap", "confirm": True},
        )
        assert r.status_code == 200

    # After the request returns, both pipelines should have empty
    # bootstrap_progress (the spawn thread may still be running but
    # reset happened synchronously in the route).
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM bootstrap_progress"
        ).fetchone()[0]
        assert count == 0
