"""Backup worker startup wiring.

Phase 8 Task 22 — exercises the block inside
``mlss_monitor.app._start_background_services`` that consults
``mlss_monitor.backup.config.load()`` and only spawns a BackupWorker
thread when the master + per-pipeline enabled flags are set.

Verifies:
- When config.enabled=False, _start_background_services does NOT
  create or start any backup workers.
- When config.enabled=True with db.enabled=True, a 'db' worker is
  instantiated, _on_enabled is called, and start() launches the thread.
- Same for files pipeline.
- When config.enabled=True but one pipeline is False, only the
  enabled pipeline's worker starts.
- When config.paused=True, the worker is created + _on_paused is
  called before start (so it parks in PAUSED, not IDLE).
- An exception inside the backup block doesn't crash the rest of
  _start_background_services (try/except wraps it).

The user constraint that drove this design: "the worker should only
run if backups are enabled". A worker thread parking in DISABLED is
unacceptable — it must literally not exist when disabled.
"""
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def reset_services_guard():
    """The ``_services_started`` Event is module-state; reset it
    between tests so ``_start_background_services`` actually runs each
    time (otherwise the idempotency guard short-circuits subsequent
    invocations within the same test session)."""
    from mlss_monitor import app as _app
    _app._services_started.clear()
    yield
    _app._services_started.clear()


@pytest.fixture(autouse=True)
def reset_state_attrs():
    """Clear backup_*_worker attributes between tests so assertions
    about post-startup state are unambiguous."""
    from mlss_monitor import state
    for attr in ("backup_db_worker", "backup_files_worker"):
        if hasattr(state, attr):
            setattr(state, attr, None)
    yield
    for attr in ("backup_db_worker", "backup_files_worker"):
        if hasattr(state, attr):
            setattr(state, attr, None)


def _patch_all_background_threads(monkeypatch):
    """Stub out every Thread / Timer / WS listener / grouper / runner
    so ``_start_background_services`` doesn't actually do anything
    except the backup-worker block. Applies patches in place; returns
    nothing."""
    import mlss_monitor.app as app_mod
    # Threads + Timers from the standard threading module — these are
    # used by _start_background_services to spawn the sensor / log /
    # weather / startup-analysis daemon threads + the 20s bootstrap
    # Timer. MagicMock has .start() so the call sites don't AttributeError.
    monkeypatch.setattr("threading.Thread", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("threading.Timer", lambda *a, **kw: MagicMock())
    # The app module also pulls Thread in at the top: ``from threading
    # import Thread``. That's a separate binding from threading.Thread —
    # patch it directly so spawning daemon threads inside the function
    # picks up the stub.
    monkeypatch.setattr(app_mod, "Thread", lambda *a, **kw: MagicMock())
    # WS listener — declared inside _start_background_services via a
    # function import. start_ws_listener returns a handle stored at
    # state.grow_ws_handle; tests don't need the real listener.
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_ws.start_ws_listener",
        lambda *a, **kw: MagicMock(),
    )
    # Incident grouper — start_grouper returns the IncidentGrouper
    # instance stored at state.incident_grouper.
    monkeypatch.setattr(
        "mlss_monitor.incident_grouper.start_grouper",
        lambda *a, **kw: MagicMock(),
    )
    # Timelapse runner — start_runner_thread returns None and spawns a
    # daemon thread internally. No-op here.
    monkeypatch.setattr(
        "mlss_monitor.grow.timelapse_jobs.start_runner_thread",
        lambda: None,
    )


def test_backup_disabled_no_workers_started(monkeypatch):
    """config.enabled=False → no worker instances created, even if
    individual pipelines would otherwise be enabled."""
    _patch_all_background_threads(monkeypatch)
    monkeypatch.setattr(
        "mlss_monitor.backup.config.load",
        lambda: {
            "enabled": False, "paused": False,
            "db": {"enabled": True}, "files": {"enabled": True},
        },
    )
    # Use a sentinel FakeWorker so we can assert it was never instantiated.
    construct_count = []

    class FakeWorker:  # pragma: no cover — should never be invoked
        def __init__(self, *, pipeline, event_bus=None):
            construct_count.append(pipeline)
    monkeypatch.setattr(
        "mlss_monitor.backup.worker.BackupWorker", FakeWorker,
    )

    from mlss_monitor import app, state
    app._start_background_services()

    assert construct_count == [], (
        "BackupWorker was instantiated despite config.enabled=False"
    )
    assert getattr(state, "backup_db_worker", None) is None
    assert getattr(state, "backup_files_worker", None) is None


def test_backup_enabled_both_pipelines_workers_started(monkeypatch):
    """config.enabled=True + both per-pipeline enabled=True → both
    workers instantiated, _on_enabled called, start() invoked. State
    handles populated on ``mlss_monitor.state``."""
    _patch_all_background_threads(monkeypatch)
    monkeypatch.setattr(
        "mlss_monitor.backup.config.load",
        lambda: {
            "enabled": True, "paused": False,
            "db": {"enabled": True}, "files": {"enabled": True},
        },
    )

    class FakeWorker:
        def __init__(self, *, pipeline, event_bus=None):
            self.pipeline = pipeline
            self.enabled = False
            self.paused = False
            self.started = False

        def _on_enabled(self):
            self.enabled = True

        def _on_paused(self):
            self.paused = True

        def start(self):
            self.started = True

        def stop(self, **_kw):
            self.started = False

    monkeypatch.setattr(
        "mlss_monitor.backup.worker.BackupWorker", FakeWorker,
    )

    from mlss_monitor import app, state
    app._start_background_services()

    db = getattr(state, "backup_db_worker", None)
    files = getattr(state, "backup_files_worker", None)
    assert db is not None
    assert files is not None
    assert db.pipeline == "db"
    assert files.pipeline == "files"
    assert db.enabled and files.enabled, "_on_enabled was not called"
    assert db.started and files.started, "start() was not called"
    assert not db.paused and not files.paused, (
        "_on_paused was called despite config.paused=False"
    )


def test_only_db_enabled_only_db_starts(monkeypatch):
    """config.enabled=True, db.enabled=True, files.enabled=False →
    only db worker is started; files attribute stays None."""
    _patch_all_background_threads(monkeypatch)
    monkeypatch.setattr(
        "mlss_monitor.backup.config.load",
        lambda: {
            "enabled": True, "paused": False,
            "db": {"enabled": True}, "files": {"enabled": False},
        },
    )
    workers_created: list[str] = []

    class FakeWorker:
        def __init__(self, *, pipeline, event_bus=None):
            self.pipeline = pipeline
            workers_created.append(pipeline)

        def _on_enabled(self):
            pass

        def _on_paused(self):
            pass

        def start(self):
            pass

    monkeypatch.setattr(
        "mlss_monitor.backup.worker.BackupWorker", FakeWorker,
    )

    from mlss_monitor import app, state
    app._start_background_services()

    assert workers_created == ["db"], (
        f"Expected only 'db' worker; got {workers_created!r}"
    )
    assert getattr(state, "backup_db_worker", None) is not None
    assert getattr(state, "backup_files_worker", None) is None


def test_only_files_enabled_only_files_starts(monkeypatch):
    """Mirror of the previous test for the files pipeline — covers the
    second branch of the per-pipeline `enabled` skip."""
    _patch_all_background_threads(monkeypatch)
    monkeypatch.setattr(
        "mlss_monitor.backup.config.load",
        lambda: {
            "enabled": True, "paused": False,
            "db": {"enabled": False}, "files": {"enabled": True},
        },
    )
    workers_created: list[str] = []

    class FakeWorker:
        def __init__(self, *, pipeline, event_bus=None):
            self.pipeline = pipeline
            workers_created.append(pipeline)

        def _on_enabled(self):
            pass

        def _on_paused(self):
            pass

        def start(self):
            pass

    monkeypatch.setattr(
        "mlss_monitor.backup.worker.BackupWorker", FakeWorker,
    )

    from mlss_monitor import app, state
    app._start_background_services()

    assert workers_created == ["files"]
    assert getattr(state, "backup_db_worker", None) is None
    assert getattr(state, "backup_files_worker", None) is not None


def test_paused_in_config_workers_start_in_paused_state(monkeypatch):
    """config.paused=True → workers are created + _on_paused called
    BEFORE start() so the thread parks in PAUSED, not IDLE.

    The call ordering matters: if start() ran before _on_paused, the
    run loop could observe state=IDLE on its first tick and pop into
    DRAINING before the listener thread caught up — that would be a
    benign race for tests but a real surprise for an operator who set
    paused=True at boot.
    """
    _patch_all_background_threads(monkeypatch)
    monkeypatch.setattr(
        "mlss_monitor.backup.config.load",
        lambda: {
            "enabled": True, "paused": True,
            "db": {"enabled": True}, "files": {"enabled": False},
        },
    )
    calls: list[tuple[str, str]] = []

    class FakeWorker:
        def __init__(self, *, pipeline, event_bus=None):
            self.pipeline = pipeline

        def _on_enabled(self):
            calls.append((self.pipeline, "enabled"))

        def _on_paused(self):
            calls.append((self.pipeline, "paused"))

        def start(self):
            calls.append((self.pipeline, "start"))

    monkeypatch.setattr(
        "mlss_monitor.backup.worker.BackupWorker", FakeWorker,
    )

    from mlss_monitor import app
    app._start_background_services()

    # Order MUST be: enabled, paused, then start — anything else means
    # the thread had a chance to flip IDLE→DRAINING before observing
    # the PAUSED state.
    assert calls == [
        ("db", "enabled"),
        ("db", "paused"),
        ("db", "start"),
    ], f"unexpected lifecycle call order: {calls!r}"


def test_backup_startup_failure_does_not_crash_app(monkeypatch):
    """If config.load() raises (e.g. SQLite locked at boot, or someone
    broke the schema), the rest of _start_background_services must
    still complete. The block wraps everything in try/except and
    log.warning's the failure."""
    _patch_all_background_threads(monkeypatch)

    def _raise():
        raise RuntimeError("DB locked at boot")

    monkeypatch.setattr(
        "mlss_monitor.backup.config.load", _raise,
    )

    from mlss_monitor import app, state
    # Must not raise — assertion is the absence of an exception.
    app._start_background_services()
    # And no worker handles were leaked.
    assert getattr(state, "backup_db_worker", None) is None
    assert getattr(state, "backup_files_worker", None) is None
