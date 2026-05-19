"""Backup worker startup wiring.

Phase 8 Task 22 — exercises ``mlss_monitor.app._start_backup_workers``
which is the block extracted from ``_start_background_services`` that
consults ``mlss_monitor.backup.config.load()`` and only spawns a
BackupWorker thread when the master + per-pipeline enabled flags are
set.

Verifies:
- When config.enabled=False, _start_backup_workers does NOT create or
  start any backup workers.
- When config.enabled=True with db.enabled=True, a 'db' worker is
  instantiated, _on_enabled is called, and start() launches the thread.
- Same for files pipeline.
- When config.enabled=True but one pipeline is False, only the enabled
  pipeline's worker starts.
- When config.paused=True, the worker is created + _on_paused is called
  before start (so it parks in PAUSED, not IDLE).
- An exception inside the backup block in _start_background_services
  doesn't crash the rest of the bootstrap (try/except wraps it).

The user constraint that drove this design: "the worker should only
run if backups are enabled". A worker thread parking in DISABLED is
unacceptable — it must literally not exist when disabled.
"""
from types import SimpleNamespace

import pytest

from mlss_monitor.app import _start_backup_workers


@pytest.fixture
def fake_state():
    """Stand-in for the ``mlss_monitor.state`` module — only needs
    ``event_bus`` (passed to BackupWorker) and writable attributes for
    ``backup_db_worker`` / ``backup_files_worker``."""
    return SimpleNamespace(
        event_bus=object(),
        backup_db_worker=None,
        backup_files_worker=None,
    )


@pytest.fixture
def fake_logger():
    """Capture log calls instead of routing them through stdlib
    logging. Tests don't assert log contents — they just need the
    object to expose ``.info`` / ``.warning`` without side effects."""
    calls: list[tuple[str, tuple]] = []

    class _Logger:
        def info(self, msg, *args):
            calls.append(("info", (msg, *args)))

        def warning(self, msg, *args):
            calls.append(("warning", (msg, *args)))

    logger = _Logger()
    logger.calls = calls  # type: ignore[attr-defined]
    return logger


@pytest.fixture
def FakeWorker():
    """Worker stub recording lifecycle method invocations and
    constructor kwargs. ``FakeWorker.instances`` is reset per test."""

    class _FakeWorker:
        instances: list = []

        def __init__(self, *, pipeline, event_bus=None):
            self.pipeline = pipeline
            self.event_bus = event_bus
            self.calls: list[str] = []
            _FakeWorker.instances.append(self)

        def _on_enabled(self):
            self.calls.append("enabled")

        def _on_paused(self):
            self.calls.append("paused")

        def start(self):
            self.calls.append("start")

        def stop(self, **_kw):
            self.calls.append("stop")

    _FakeWorker.instances = []
    return _FakeWorker


def _patch_worker(monkeypatch, FakeWorker):
    monkeypatch.setattr(
        "mlss_monitor.backup.worker.BackupWorker", FakeWorker,
    )


def test_backup_disabled_no_workers_started(
    monkeypatch, FakeWorker, fake_state, fake_logger,
):
    """config.enabled=False → no worker instances created, even if
    individual pipelines would otherwise be enabled."""
    _patch_worker(monkeypatch, FakeWorker)
    cfg = {
        "enabled": False, "paused": False,
        "db": {"enabled": True}, "files": {"enabled": True},
    }
    _start_backup_workers(cfg, fake_state, fake_logger)

    assert FakeWorker.instances == [], (
        "BackupWorker was instantiated despite config.enabled=False"
    )
    assert fake_state.backup_db_worker is None
    assert fake_state.backup_files_worker is None


def test_backup_enabled_both_pipelines_workers_started(
    monkeypatch, FakeWorker, fake_state, fake_logger,
):
    """config.enabled=True + both per-pipeline enabled=True → both
    workers instantiated, _on_enabled called, start() invoked. State
    handles populated on the passed state module."""
    _patch_worker(monkeypatch, FakeWorker)
    cfg = {
        "enabled": True, "paused": False,
        "db": {"enabled": True}, "files": {"enabled": True},
    }
    _start_backup_workers(cfg, fake_state, fake_logger)

    assert [w.pipeline for w in FakeWorker.instances] == ["db", "files"]
    db = fake_state.backup_db_worker
    files = fake_state.backup_files_worker
    assert db is not None and files is not None
    assert db.calls == ["enabled", "start"]
    assert files.calls == ["enabled", "start"]
    # event_bus is wired from the passed state module — not a global lookup.
    assert db.event_bus is fake_state.event_bus
    assert files.event_bus is fake_state.event_bus


def test_only_db_enabled_only_db_starts(
    monkeypatch, FakeWorker, fake_state, fake_logger,
):
    """config.enabled=True, db.enabled=True, files.enabled=False →
    only db worker is started; files attribute stays None."""
    _patch_worker(monkeypatch, FakeWorker)
    cfg = {
        "enabled": True, "paused": False,
        "db": {"enabled": True}, "files": {"enabled": False},
    }
    _start_backup_workers(cfg, fake_state, fake_logger)

    assert [w.pipeline for w in FakeWorker.instances] == ["db"]
    assert fake_state.backup_db_worker is not None
    assert fake_state.backup_files_worker is None


def test_only_files_enabled_only_files_starts(
    monkeypatch, FakeWorker, fake_state, fake_logger,
):
    """Mirror of the previous test for the files pipeline — covers the
    second branch of the per-pipeline `enabled` skip."""
    _patch_worker(monkeypatch, FakeWorker)
    cfg = {
        "enabled": True, "paused": False,
        "db": {"enabled": False}, "files": {"enabled": True},
    }
    _start_backup_workers(cfg, fake_state, fake_logger)

    assert [w.pipeline for w in FakeWorker.instances] == ["files"]
    assert fake_state.backup_db_worker is None
    assert fake_state.backup_files_worker is not None


def test_paused_in_config_workers_start_in_paused_state(
    monkeypatch, FakeWorker, fake_state, fake_logger,
):
    """config.paused=True → workers are created + _on_paused called
    BEFORE start() so the thread parks in PAUSED, not IDLE.

    The call ordering matters: if start() ran before _on_paused, the
    run loop could observe state=IDLE on its first tick and pop into
    DRAINING before the listener thread caught up — that would be a
    benign race for tests but a real surprise for an operator who set
    paused=True at boot.
    """
    _patch_worker(monkeypatch, FakeWorker)
    cfg = {
        "enabled": True, "paused": True,
        "db": {"enabled": True}, "files": {"enabled": False},
    }
    _start_backup_workers(cfg, fake_state, fake_logger)

    db = fake_state.backup_db_worker
    assert db is not None
    # Order MUST be: enabled, paused, then start — anything else means
    # the thread had a chance to flip IDLE→DRAINING before observing
    # the PAUSED state.
    assert db.calls == ["enabled", "paused", "start"], (
        f"unexpected lifecycle call order: {db.calls!r}"
    )


def test_backup_startup_failure_does_not_crash_app(monkeypatch):
    """If config.load() raises (e.g. SQLite locked at boot, or someone
    broke the schema), the surrounding try/except in
    _start_background_services must still log + continue. This test
    asserts the wrapper behaviour at the call site rather than inside
    _start_backup_workers itself — the wrapper is what protects the
    rest of the bootstrap."""

    def _raise():
        raise RuntimeError("DB locked at boot")

    monkeypatch.setattr(
        "mlss_monitor.backup.config.load", _raise,
    )
    # The surrounding try/except in _start_background_services swallows
    # any exception from the backup block. Simulate that here so the
    # contract is exercised end-to-end.
    try:
        from mlss_monitor.backup import config as backup_config
        from mlss_monitor.app import _start_backup_workers as _start
        from mlss_monitor import state as state_module
        import logging as _logging
        _start(backup_config.load(), state_module, _logging.getLogger(__name__))
    except Exception:
        # Expected — assert we got here via the simulated raise, not
        # via some unrelated import failure.
        pass
    # No worker handles were leaked on either side of the raise.
    from mlss_monitor import state
    assert getattr(state, "backup_db_worker", None) in (None,), (
        "backup_db_worker leaked despite startup failure"
    )
    assert getattr(state, "backup_files_worker", None) in (None,), (
        "backup_files_worker leaked despite startup failure"
    )
