"""Testcontainers fixtures for backup E2E tests.

Real Postgres (via testcontainers) + real MinIO (S3-compatible) +
real BackupWorker thread + real SQLite tempfile. The only thing
"mocked" is that the worker's PostgresClient is built with
``sslmode=disable`` (Pi-side containers don't have TLS) and the
S3Client with ``verify_tls=False`` — both are wrapped via monkeypatch
in ``configured_backup`` so the worker hits the local containers
without changing the production code path.

Containers are **session-scoped** because docker container startup
is slow (~5-10s each); function-scoped state cleanup
(``_clear_postgres`` / ``_clear_minio``) keeps each test isolated.

Without Docker, ``pytest_collection_modifyitems`` adds a ``skip``
marker to every ``@pytest.mark.e2e`` test so devs without Docker
can still run ``pytest tests/`` and see these tests cleanly skipped
rather than fail at fixture-setup time.

Spec ref: docs/superpowers/specs/2026-05-18-mlss-backup-design.md
Plan ref: docs/superpowers/plans/2026-05-18-mlss-backup.md (Phase 9)
"""
from __future__ import annotations

import functools
import gc
import os
import tempfile
import time
from pathlib import Path

import pytest


# Disable the Ryuk reaper container by default — it relies on a
# fixed-port (8080) container that Docker Desktop on Windows often
# fails to NAT cleanly, manifesting as
# ``ConnectionError: Port mapping for container ... and port 8080 is
# not available`` at container start. The reaper is a defensive
# cleanup-on-process-crash helper; without it, leaked containers must
# be cleaned manually if pytest itself segfaults — fine in CI (one-
# shot ephemeral runner) and acceptable locally (devs can `docker
# container prune`). Set ``TESTCONTAINERS_RYUK_DISABLED=false`` to
# re-enable when running on a Linux host where Ryuk works cleanly.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")


# ── Docker availability + auto-skip ─────────────────────────────────

def _docker_available() -> bool:
    """Return True if the local Docker daemon is reachable.

    Used at pytest collection time to skip ``e2e`` tests cleanly on
    machines without Docker. We import ``docker`` lazily so a dev
    without testcontainers installed can still load this module
    (the import error during collection would otherwise turn into a
    test-failure rather than a skip).
    """
    try:
        import docker  # pylint: disable=import-outside-toplevel
        docker.from_env().ping()
        return True
    except Exception:  # pylint: disable=broad-except
        return False


_DOCKER_OK = _docker_available()


def pytest_collection_modifyitems(config, items):
    """Auto-skip ``@pytest.mark.e2e`` tests when Docker is unavailable.

    The user principle: "Skip cleanly without Docker." A dev without
    Docker should see the e2e tests skipped, not failed; this hook
    inspects each collected item for the ``e2e`` keyword and adds a
    skip marker when the daemon isn't reachable.
    """
    if _DOCKER_OK:
        return
    skip_marker = pytest.mark.skip(
        reason="Docker not available — backup E2E tests require a "
               "running Docker daemon for Postgres + MinIO testcontainers."
    )
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_marker)


# ── Session-scoped containers ───────────────────────────────────────

@pytest.fixture(scope="session")
def postgres():
    """Session-scoped Postgres container.

    Returns a connection-params dict — host/port/database/user/password.
    Also exposes ``.container`` so tests that want to pause/unpause
    (e.g. ``test_overnight_outage``) can reach the underlying
    Docker container directly.
    """
    if not _DOCKER_OK:
        pytest.skip("Docker not available")
    from testcontainers.postgres import PostgresContainer
    pg = PostgresContainer("postgres:16-alpine")
    pg.start()
    try:
        params = {
            "host": pg.get_container_host_ip(),
            "port": int(pg.get_exposed_port(5432)),
            "database": pg.dbname,
            "user": pg.username,
            "password": pg.password,
            "container": pg,  # for pause/unpause in failure-mode tests
        }
        yield params
    finally:
        pg.stop()


@pytest.fixture(scope="session")
def minio():
    """Session-scoped MinIO container.

    Returns S3-compatible connection params + the underlying
    container for tests that want to assert object presence directly
    via ``boto3``.

    Start is wrapped in a retry loop because the testcontainers HTTP
    wait strategy races Docker's port-mapping bookkeeping on Windows:
    ``container.get_exposed_port(9000)`` is called immediately after
    the container shows up in ``docker ps``, but the port hasn't
    been bound yet, so the lookup raises ``ConnectionError``. Three
    retries with a small back-off resolves it reliably.  This same
    pattern doesn't seem to bite the Postgres container, presumably
    because postgres-alpine's slower entrypoint gives the port
    mapping time to settle.
    """
    if not _DOCKER_OK:
        pytest.skip("Docker not available")
    from testcontainers.minio import MinioContainer
    mc = None
    last_exc: Exception | None = None
    for attempt in range(3):
        if attempt:
            time.sleep(2.0)  # back-off between retries
        try:
            mc = MinioContainer("minio/minio:RELEASE.2025-04-22T22-12-26Z")
            mc.start()
            break
        except ConnectionError as exc:
            last_exc = exc
            # Clean up the partially-started container so the next
            # attempt doesn't leak it.
            try:
                if mc is not None:
                    mc.stop()
            except Exception:  # pylint: disable=broad-except
                pass
            mc = None
    if mc is None:
        raise RuntimeError(
            f"MinIO container failed to start after 3 attempts: {last_exc}"
        )
    try:
        host = mc.get_container_host_ip()
        port = int(mc.get_exposed_port(9000))
        yield {
            "endpoint": f"http://{host}:{port}",
            "access_key": mc.access_key,
            "secret_key": mc.secret_key,
            "container": mc,
        }
    finally:
        mc.stop()


# ── Function-scoped fresh SQLite + monkeypatch ──────────────────────

# Modules that snapshot ``DB_FILE`` at import time. Listed here so
# adding a new module that does the same only needs one update.
_DB_FILE_MODULES = (
    "database.db_logger",
    "mlss_monitor.grow.handlers",
    "mlss_monitor.grow.photo_storage",
    "mlss_monitor.backup.config",
    "mlss_monitor.backup.worker",
)


@pytest.fixture
def fresh_sqlite_db(monkeypatch):
    """Fresh tempfile SQLite primed with the full live schema.

    Every backup-pipeline module that snapshots ``DB_FILE`` at import
    time is monkeypatched so live writers + worker + outbox all hit
    the tempfile.  Mirror of the ``db_path`` fixture in
    ``tests/conftest.py`` but renamed to make the test intent
    obvious at the call site (E2E tests work with a "fresh" DB by
    design, not a shared one).
    """
    import database.init_db as init_db  # pylint: disable=import-outside-toplevel
    # NamedTemporaryFile must outlive this fixture; pytest setup/teardown
    # cycles around the yield need the path to remain valid.
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=consider-using-with
    tmp.close()
    original = init_db.DB_FILE
    init_db.DB_FILE = tmp.name
    for mod in _DB_FILE_MODULES:
        monkeypatch.setattr(f"{mod}.DB_FILE", tmp.name, raising=False)
    # api_backup also snapshots — patched defensively (won't import
    # in all test scenarios, raising=False prevents collection failure).
    monkeypatch.setattr(
        "mlss_monitor.routes.api_backup.DB_FILE", tmp.name, raising=False,
    )
    init_db.create_db()
    try:
        yield tmp.name
    finally:
        init_db.DB_FILE = original
        gc.collect()
        Path(tmp.name).unlink(missing_ok=True)


# ── Configured-backup wiring ────────────────────────────────────────

# Bucket suffixes that the file pipeline routes to. Mirrors
# ``_bucket_suffix_for_key`` in ``mlss_monitor/backup/_drain.py``.
_BUCKET_SUFFIXES = ("photos", "anomaly", "multivar-anomaly", "attribution")


def _clear_postgres(pg_client) -> None:
    """Drop every replicated table in the test Postgres so the next
    test starts clean.

    DROP CASCADE is required because composite-PK tables
    (``incident_alerts``, ``grow_unit_capabilities``, etc.) may have
    FK-like relationships in some schemas — DROP TABLE alone would
    error on the dependent.
    """
    from mlss_monitor.backup.replicated_tables import (  # pylint: disable=import-outside-toplevel
        REPLICATED_TABLES,
    )
    with pg_client._connect() as conn:  # pylint: disable=protected-access
        with conn.cursor() as cur:
            for table in REPLICATED_TABLES:
                cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


def _clear_minio(s3_client) -> None:
    """Empty + delete every backup bucket so the next test starts clean.

    boto3 doesn't have a single-call "delete bucket + everything in
    it", so we list + delete objects first, then delete the bucket.
    """
    for suffix in _BUCKET_SUFFIXES:
        bucket = s3_client._bucket(suffix)  # pylint: disable=protected-access
        try:
            objs = s3_client._client.list_objects_v2(  # pylint: disable=protected-access
                Bucket=bucket,
            ).get("Contents", [])
            for obj in objs:
                s3_client._client.delete_object(  # pylint: disable=protected-access
                    Bucket=bucket, Key=obj["Key"],
                )
            s3_client._client.delete_bucket(Bucket=bucket)  # pylint: disable=protected-access
        except Exception:  # pylint: disable=broad-except
            # Bucket may not exist (a test failed before make_bucket
            # ran); we still want teardown to succeed so the next test
            # gets a clean slate.
            pass


@pytest.fixture
def configured_backup(fresh_sqlite_db, postgres, minio, monkeypatch):
    """Wire the testcontainer credentials into the backup config +
    apply the server-side DDL + create the S3 buckets.

    Returns a dict with the live ``PostgresClient`` + ``S3Client``
    instances (for assertions in test bodies that need to SELECT or
    HEAD directly against the server) plus the loaded config.

    Also monkeypatches ``PostgresClient`` / ``S3Client`` in the
    ``mlss_monitor.backup.worker`` module so the worker's
    ``_build_client`` injects ``sslmode='disable'`` (no TLS in local
    containers) and ``verify_tls=False`` (MinIO uses self-signed
    certs at best).  This monkeypatch is what lets us run the full
    production code path against local containers without forking
    the worker's client-construction logic.

    Also shrinks the worker's run-loop sleep intervals from
    production values (30s IDLE poll) to sub-second so a test
    assertion doesn't have to wait 30s for the next drain tick.
    The worker module explicitly documents this knob:
    "module-level so tests can monkeypatch to milliseconds and
    assert behaviour without 30-second waits".
    """
    monkeypatch.setattr("mlss_monitor.backup.worker._IDLE_POLL_S", 0.3)
    monkeypatch.setattr("mlss_monitor.backup.worker._PAUSED_POLL_S", 0.1)
    monkeypatch.setattr("mlss_monitor.backup.worker._DRAINING_POLL_S", 0.05)

    from mlss_monitor.backup import config  # pylint: disable=import-outside-toplevel
    from mlss_monitor.backup import server_schema  # pylint: disable=import-outside-toplevel
    from mlss_monitor.backup.postgres_client import (  # pylint: disable=import-outside-toplevel
        PostgresClient,
    )
    from mlss_monitor.backup.s3_client import (  # pylint: disable=import-outside-toplevel
        S3Client,
    )

    config.save({
        "enabled": True,
        "paused": False,
        "source_pi_id": "test-pi",
        "db": {
            "enabled": True,
            "host": postgres["host"],
            "port": postgres["port"],
            "database": postgres["database"],
            "user": postgres["user"],
            "password": postgres["password"],
        },
        "files": {
            "enabled": True,
            "endpoint": minio["endpoint"],
            "region": "auto",
            "access_key_id": minio["access_key"],
            "secret_key": minio["secret_key"],
            "bucket_prefix": "test-",
        },
        # Aggressive connect-timeout (default is 10s) so the
        # overnight-outage / hot-reload / two-pipelines tests that
        # exercise the BACKOFF transition don't spend most of their
        # wall-clock budget waiting for psycopg2's TCP timeout.
        # 3 seconds is enough for a real connection in CI; way faster
        # than the default 10 when the host is unreachable.
        "advanced": {"connection_timeout_s": 3},
    })

    # Patch the worker module's client classes to inject dev-friendly
    # defaults that production never uses (no TLS, no cert
    # verification). Wrapping (rather than replacing) keeps any
    # caller-supplied kwargs intact.
    _OrigPg = PostgresClient
    _OrigS3 = S3Client

    @functools.wraps(_OrigPg)
    def _PgWithSslDisable(*args, **kwargs):
        kwargs.setdefault("sslmode", "disable")
        return _OrigPg(*args, **kwargs)

    @functools.wraps(_OrigS3)
    def _S3WithoutTls(*args, **kwargs):
        kwargs.setdefault("verify_tls", False)
        return _OrigS3(*args, **kwargs)

    monkeypatch.setattr(
        "mlss_monitor.backup.worker.PostgresClient", _PgWithSslDisable,
    )
    monkeypatch.setattr(
        "mlss_monitor.backup.worker.S3Client", _S3WithoutTls,
    )

    pg_client = _PgWithSslDisable(
        host=postgres["host"], port=postgres["port"],
        database=postgres["database"], user=postgres["user"],
        password=postgres["password"], source_pi_id="test-pi",
    )
    pg_client.run_ddl(server_schema.generate_ddl(fresh_sqlite_db))

    s3_client = _S3WithoutTls(
        endpoint=minio["endpoint"], region="auto",
        access_key=minio["access_key"], secret_key=minio["secret_key"],
        bucket_prefix="test-",
    )
    for suffix in _BUCKET_SUFFIXES:
        s3_client.make_bucket(suffix)

    try:
        yield {
            "pg": pg_client,
            "s3": s3_client,
            "config": config.load(),
            "postgres_params": postgres,
            "minio_params": minio,
        }
    finally:
        _clear_postgres(pg_client)
        _clear_minio(s3_client)


# ── Worker fixtures ─────────────────────────────────────────────────

@pytest.fixture
def db_worker(configured_backup):  # pylint: disable=redefined-outer-name
    """A running ``db``-pipeline BackupWorker. Auto-stops in teardown.

    The ``event_bus`` arg is intentionally ``None`` — E2E tests don't
    need status emission and a bus would just add noise. Test bodies
    that need hot-reload events (``test_hot_reload``) build a
    second worker with a real bus inline.
    """
    from mlss_monitor.backup.worker import BackupWorker  # pylint: disable=import-outside-toplevel
    w = BackupWorker(pipeline="db", event_bus=None)
    w._on_enabled()  # pylint: disable=protected-access
    w.start()
    try:
        yield w
    finally:
        w.stop(timeout=5.0)


@pytest.fixture
def files_worker(configured_backup):  # pylint: disable=redefined-outer-name
    """A running ``files``-pipeline BackupWorker. Auto-stops in teardown."""
    from mlss_monitor.backup.worker import BackupWorker  # pylint: disable=import-outside-toplevel
    w = BackupWorker(pipeline="files", event_bus=None)
    w._on_enabled()  # pylint: disable=protected-access
    w.start()
    try:
        yield w
    finally:
        w.stop(timeout=5.0)


# ── Test-side helpers (exported via conftest, not a fixture) ────────

def wait_until(predicate, *, timeout: float = 15.0, interval: float = 0.2,
               message: str = "") -> None:
    """Poll ``predicate`` until it returns truthy, or raise on timeout.

    Returns ``None`` on success. The error message is bespoke — the
    user principle is "no magic timeouts": a hung test should fail
    with a sentence describing what it was waiting for, not just
    ``False``.

    Example::

        wait_until(
            lambda: pg.fetchone() is not None,
            timeout=15.0,
            message="sensor row shipped to Postgres",
        )
    """
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # pylint: disable=broad-except
            # Retry on transient errors (e.g. Postgres still
            # rejecting connections during pause/unpause); remember
            # the last one for the timeout message.
            last_exc = exc
        time.sleep(interval)
    suffix = f" (last error: {last_exc})" if last_exc else ""
    raise AssertionError(
        f"wait_until timed out after {timeout}s: "
        f"{message or 'predicate did not become true'}{suffix}"
    )
