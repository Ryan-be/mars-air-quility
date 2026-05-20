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

import sqlite3
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


@pytest.fixture(autouse=True)
def _suppress_bootstrap_thread(request, monkeypatch):
    """Stub ``_kick_off_bootstrap`` for every test by default.

    Without this, PUT /config tests would spawn a real
    bootstrap-oneshot daemon thread that opens a long-lived sqlite3
    connection on the tempfile DB — on Windows that connection
    blocks the conftest teardown's ``unlink``, causing every PUT
    test to leak a temp file.

    Tests that want to exercise the bootstrap kickoff opt in by
    requesting the ``real_bootstrap`` fixture (or the dedicated
    ``mock_kick_off`` fixture for fine-grained assertions). The
    request-fixture lookup below ensures opt-in tests skip the
    autouse stub.
    """
    if "real_bootstrap" in request.fixturenames:
        return

    def _noop_kick_off(*, force_reset):  # pylint: disable=unused-argument
        return None

    monkeypatch.setattr(
        "mlss_monitor.routes.api_backup._kick_off_bootstrap",
        _noop_kick_off,
    )


@pytest.fixture
def real_bootstrap():
    """Opt-in marker fixture — tests that request this disable the
    autouse ``_suppress_bootstrap_thread`` stub so the real
    ``_kick_off_bootstrap`` runs. The fixture body is intentionally
    empty; the autouse fixture checks for its name in fixturenames."""
    return None


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
        # pylint mis-infers worker_instance.start as a <lambda> from the
        # autouse _kick_off_bootstrap monkeypatch above — MagicMock does
        # have assert_called, so suppress the false positive.
        worker_instance.start.assert_called()  # pylint: disable=no-member
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


def test_post_init_db_applies_generated_ddl(client, db_path, event_bus):  # noqa: ARG001
    """DB init derives the server DDL from the live SQLite schema +
    applies it via PostgresClient.run_ddl. Both the generator and the
    Postgres client are exercised here — generator runs for real
    against the tempfile, the client is mocked because the test
    environment has no Postgres."""
    from mlss_monitor.backup.replicated_tables import REPLICATED_TABLES

    _login(client, role="admin")
    with patch(
        "mlss_monitor.routes.api_backup.PostgresClient"
    ) as mock_cls:
        mock_client = mock_cls.return_value
        r = client.post("/api/admin/backup/init?pipeline=db")

    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    # tables_created enumerates every replicated table.
    assert set(body["tables_created"]) == set(REPLICATED_TABLES.keys())

    # run_ddl was called exactly once with a multi-statement DDL string
    # produced by the generator.
    mock_client.run_ddl.assert_called_once()
    ddl_arg = mock_client.run_ddl.call_args.args[0]
    # Every replicated table appears in the emitted DDL.
    for table in REPLICATED_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl_arg
    # And the backup-specific columns are present.
    assert "source_pi_id TEXT NOT NULL" in ddl_arg
    assert "ingested_at" in ddl_arg


def test_post_init_db_returns_500_when_run_ddl_raises(client, db_path, event_bus):  # noqa: ARG001
    """If the Postgres client fails (auth, network, bad config) the
    endpoint surfaces 500 + the error message rather than letting the
    exception bubble out as a Flask 500 HTML page."""
    _login(client, role="admin")
    with patch(
        "mlss_monitor.routes.api_backup.PostgresClient"
    ) as mock_cls:
        mock_cls.return_value.run_ddl.side_effect = RuntimeError(
            "auth failure: password is wrong"
        )
        r = client.post("/api/admin/backup/init?pipeline=db")

    assert r.status_code == 500
    body = r.get_json()
    assert body["ok"] is False
    assert "auth failure" in body["error"]


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
    client, db_path, event_bus, real_bootstrap,
):  # noqa: ARG001
    """force_rebootstrap deletes every bootstrap_progress row + kicks
    off a fresh scan in a background thread. We only assert the
    reset side here — the thread is fire-and-forget.

    Uses ``real_bootstrap`` to bypass the autouse stub so the real
    ``_kick_off_bootstrap`` runs and exercises the reset code path.
    The BootstrapScanner's ``start_*_bootstrap`` methods are patched
    to no-ops so we don't actually walk the world during the test.
    """
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
    # Patch BootstrapScanner so the thread's start_* methods don't
    # actually walk the world; ``reset`` keeps its real implementation
    # so we can assert the post-condition.
    with patch(
        "mlss_monitor.routes.api_backup.BootstrapScanner"
    ) as mock_cls:
        scanner = mock_cls.return_value
        from mlss_monitor.backup.bootstrap import BootstrapScanner as Real
        scanner.reset.side_effect = Real(db_path).reset
        # start_* are MagicMocks by default (no-op) — the spawned
        # thread will call them and exit immediately without holding
        # any sqlite connection on the test DB.
        r = client.post(
            "/api/admin/backup/maintenance",
            json={"action": "force_rebootstrap", "confirm": True},
        )
        assert r.status_code == 200
        # Wait briefly for the daemon thread to finish its no-op
        # start_* calls so it doesn't hold any reference past
        # teardown.
        import time
        for _ in range(20):
            if scanner.start_db_bootstrap.called and scanner.start_files_bootstrap.called:
                break
            time.sleep(0.05)

    # After the request returns, both pipelines should have empty
    # bootstrap_progress (the spawn thread may still be running but
    # reset happened synchronously in the route).
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM bootstrap_progress"
        ).fetchone()[0]
        assert count == 0


# ─────────────────────────────────────────────────────────────────────
# source_pi_id config-driven (Task 1)
# ─────────────────────────────────────────────────────────────────────


def test_put_config_persists_source_pi_id(client, db_path, event_bus):  # noqa: ARG001
    """source_pi_id is a top-level field. PUT /config persists it
    alongside enabled / paused, and GET /config exposes it."""
    _login(client, role="admin")
    r = client.put("/api/admin/backup/config", json={"source_pi_id": "pi-9"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["source_pi_id"] == "pi-9"
    # And it's readable via config.load() — single source of truth.
    assert config.load()["source_pi_id"] == "pi-9"


def test_put_config_rejects_empty_source_pi_id_as_400(client, db_path, event_bus):  # noqa: ARG001
    """Empty source_pi_id should fail fast at write time. The route
    converts the underlying ValueError to a 400 so the UI sees the
    failure instead of a Flask 500 HTML page."""
    _login(client, role="admin")
    r = client.put("/api/admin/backup/config", json={"source_pi_id": ""})
    assert r.status_code == 400
    body = r.get_json()
    assert body["ok"] is False
    assert "source_pi_id" in body["error"]


def test_post_test_uses_config_source_pi_id(client, db_path, event_bus):  # noqa: ARG001
    """POST /test (db variant) must build PostgresClient with the
    config-stored source_pi_id, NOT a hardcoded literal. Regression-
    guards against a future revert to the deleted _source_pi_id()
    helper that always returned 'pi-1'."""
    config.save({
        "source_pi_id": "pi-3",
        "db": {"host": "h", "port": 5432, "database": "mlss",
               "user": "u", "password": "p"},
    })
    _login(client, role="admin")
    with patch(
        "mlss_monitor.routes.api_backup.PostgresClient"
    ) as mock_cls:
        mock_cls.return_value.test_connection.return_value = {"ok": True}
        r = client.post("/api/admin/backup/test?pipeline=db")
        assert r.status_code == 200
        # PostgresClient was constructed with the config-stored value.
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["source_pi_id"] == "pi-3"


def test_post_init_uses_config_source_pi_id(client, db_path, event_bus):  # noqa: ARG001
    """POST /init (db variant) must also read source_pi_id from config."""
    config.save({"source_pi_id": "pi-5"})
    _login(client, role="admin")
    with patch(
        "mlss_monitor.routes.api_backup.PostgresClient"
    ) as mock_cls:
        client.post("/api/admin/backup/init?pipeline=db")
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["source_pi_id"] == "pi-5"


# ─────────────────────────────────────────────────────────────────────
# Bootstrap auto-run on first enable (Task 2)
# ─────────────────────────────────────────────────────────────────────


def test_put_config_auto_runs_bootstrap_on_first_enable(
    client, db_path, event_bus,
):  # noqa: ARG001
    """When bootstrap_progress is empty, PUT /config kicks off a
    bootstrap scan in a daemon thread. Operator workflow:
    "configure → save → enable" — the scan starts behind the save
    without a separate force_rebootstrap click."""
    _login(client, role="admin")
    with patch(
        "mlss_monitor.routes.api_backup._kick_off_bootstrap"
    ) as mock_kickoff:
        r = client.put("/api/admin/backup/config", json={
            "enabled": True,
            "db": {"enabled": True, "host": "server.local", "password": "x"},
        })
        assert r.status_code == 200
        # Auto-run path uses force_reset=False so it gates on
        # bootstrap_progress being empty.
        mock_kickoff.assert_called_once_with(force_reset=False)


def test_put_config_calls_kickoff_on_every_save(
    client, db_path, event_bus,
):  # noqa: ARG001
    """The auto-run path is called on EVERY PUT /config, not just
    enable transitions — the helper itself decides whether to actually
    start a thread (by checking bootstrap_progress). This keeps the
    PUT handler trivial and the deduplication logic in one place."""
    config.save({
        "enabled": True,
        "db": {"enabled": True, "host": "h"},
    })
    _login(client, role="admin")
    with patch(
        "mlss_monitor.routes.api_backup._kick_off_bootstrap"
    ) as mock_kickoff:
        # Saving an unrelated field still calls the kickoff.
        r = client.put("/api/admin/backup/config", json={
            "db": {"port": 5433},
        })
        assert r.status_code == 200
        mock_kickoff.assert_called_once_with(force_reset=False)


def test_kick_off_bootstrap_no_op_when_progress_exists(
    db_path, event_bus, real_bootstrap,  # noqa: ARG001
):
    """Calling _kick_off_bootstrap(force_reset=False) on a DB whose
    bootstrap_progress already has rows is a no-op — no thread
    spawned. Drives the idempotency guarantee that auto-run can be
    called on every PUT /config without re-bootstrapping after the
    first enable."""
    from mlss_monitor.routes.api_backup import _kick_off_bootstrap
    # Pre-seed bootstrap_progress with one row.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO bootstrap_progress "
            "(pipeline, scope, last_pk, total_rows, started_at) "
            "VALUES ('db', 'sensor_data', '0', 0, ?)",
            ("2026-05-01",),
        )
        conn.commit()

    with patch("mlss_monitor.routes.api_backup.threading.Thread") as mock_thread:
        _kick_off_bootstrap(force_reset=False)
        mock_thread.assert_not_called()


def test_kick_off_bootstrap_spawns_thread_when_progress_empty(
    db_path, event_bus, real_bootstrap,  # noqa: ARG001
):
    """force_reset=False on an empty bootstrap_progress DOES spawn
    the daemon thread."""
    from mlss_monitor.routes.api_backup import _kick_off_bootstrap
    with patch("mlss_monitor.routes.api_backup.threading.Thread") as mock_thread:
        _kick_off_bootstrap(force_reset=False)
        mock_thread.assert_called_once()
        # The thread must be a daemon so app shutdown doesn't hang
        # on an in-progress scan.
        kwargs = mock_thread.call_args.kwargs
        assert kwargs["daemon"] is True
        assert kwargs["name"] == "backup-bootstrap-oneshot"


def test_kick_off_bootstrap_force_reset_wipes_progress_first(
    db_path, event_bus, real_bootstrap,  # noqa: ARG001
):
    """force_reset=True clears bootstrap_progress synchronously
    BEFORE spawning the thread, so a re-bootstrap re-walks
    everything from zero."""
    from mlss_monitor.routes.api_backup import _kick_off_bootstrap
    # Seed both pipelines.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO bootstrap_progress "
            "(pipeline, scope, last_pk, total_rows, started_at) "
            "VALUES ('db', 'sensor_data', '99', 100, ?)",
            ("2026-05-01",),
        )
        conn.execute(
            "INSERT INTO bootstrap_progress "
            "(pipeline, scope, last_pk, total_rows, started_at) "
            "VALUES ('files', '/x', NULL, NULL, ?)",
            ("2026-05-01",),
        )
        conn.commit()

    with patch(
        "mlss_monitor.routes.api_backup.BootstrapScanner"
    ) as mock_cls:
        scanner = mock_cls.return_value
        from mlss_monitor.backup.bootstrap import BootstrapScanner as Real
        scanner.reset.side_effect = Real(db_path).reset
        with patch("mlss_monitor.routes.api_backup.threading.Thread"):
            _kick_off_bootstrap(force_reset=True)

    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM bootstrap_progress"
        ).fetchone()[0]
    assert count == 0


# ─────────────────────────────────────────────────────────────────────
# _default_file_roots — model artefact paths (Task 3)
# ─────────────────────────────────────────────────────────────────────


def test_default_file_roots_includes_photos_dir():
    """Photos tree is always walked — populated by the live
    photo_storage.handle_photo_frame writer."""
    from mlss_monitor.routes.api_backup import _default_file_roots
    roots = _default_file_roots()
    photo_entries = [(kind, p) for kind, p in roots if kind == "photo"]
    assert len(photo_entries) >= 1
    # Path ends with data/grow_images
    assert photo_entries[0][1].as_posix().endswith("data/grow_images")


def test_default_file_roots_excludes_models_intentionally():
    """Models are NOT bootstrapped. Documented in _BOOTSTRAP_FILE_ROOTS:
    bootstrap walks a tree and produces target_key from the relative
    path, but the live writers (AnomalyDetector._save_models etc.)
    enqueue blobs with bucket-routed prefixes (anomaly/<channel>/
    <iso>.pkl). Walking data/anomaly_models would produce target_keys
    like "tvoc_ppb.pkl" that _drain._bucket_suffix_for_key would
    log-drop. Models get re-enqueued on every save cycle (~3 min),
    so the next training cycle after backups are enabled re-ships
    every model with the correct shape.

    This test locks in that design choice — a future contributor
    adding model dirs back to bootstrap without also fixing the
    target_key shape would resurrect the silent-drop bug.
    """
    from mlss_monitor.routes.api_backup import _default_file_roots
    roots = _default_file_roots()
    model_entries = [(kind, p) for kind, p in roots if kind == "model"]
    assert model_entries == [], (
        f"Bootstrap should not walk model directories — see the "
        f"_BOOTSTRAP_FILE_ROOTS comment in api_backup.py for why. "
        f"Found unexpected model entries: {model_entries!r}"
    )


def test_default_file_roots_paths_are_absolute():
    """Bootstrap is invoked from a daemon thread whose cwd may not be
    the project root. All paths must be absolute so rglob walks the
    correct tree regardless of where gunicorn was launched."""
    from mlss_monitor.routes.api_backup import _default_file_roots
    for kind, path in _default_file_roots():
        assert path.is_absolute(), (
            f"Path for kind={kind!r} is not absolute: {path!r}"
        )
