"""BackupWorker run loop + thread lifecycle.

Tests the daemon thread orchestration that ties Task 13's
``_drain.drain_db_batch`` and Task 14's ``_drain.drain_files_batch``
together: start/stop, state-dispatched sleeps, and per-tick client
construction.

Strategy: mock _drain_one_batch (so we don't need a real Postgres/S3)
and patch the sleep constants to milliseconds so tests run in <1s.

Spec: docs/superpowers/specs/2026-05-18-mlss-backup-design.md
"""
import threading
import time
from datetime import datetime
from unittest.mock import patch, MagicMock
import pytest

from mlss_monitor.backup.worker import BackupWorker, State


@pytest.fixture
def fast_intervals(monkeypatch):
    """Squash sleep constants to milliseconds so the run loop ticks
    fast enough for behavioural assertions in test time."""
    monkeypatch.setattr("mlss_monitor.backup.worker._IDLE_POLL_S", 0.01)
    monkeypatch.setattr("mlss_monitor.backup.worker._PAUSED_POLL_S", 0.005)
    monkeypatch.setattr("mlss_monitor.backup.worker._DRAINING_POLL_S", 0.005)


@pytest.fixture
def worker():
    """Fresh worker; always join the thread on teardown even if the
    test forgot to stop it."""
    w = BackupWorker(pipeline="db")
    yield w
    w.stop(timeout=2.0)


# -- start / stop ----------------------------------------------------

def test_start_launches_daemon_thread(worker, fast_intervals):
    """start() spins up a daemon thread; the worker exposes it as
    ._thread so the status panel (Task 17) can observe aliveness."""
    with patch.object(worker, "_drain_one_batch", return_value=False):
        worker._on_enabled()
        worker.start()
        time.sleep(0.05)
        assert worker._thread is not None
        assert worker._thread.is_alive()
        assert worker._thread.daemon is True


def test_thread_name_includes_pipeline(worker, fast_intervals):
    """Thread name is f'backup-{pipeline}' so it shows up clearly in
    py-spy / py-spy dump and gdb-py backtraces."""
    with patch.object(worker, "_drain_one_batch", return_value=False):
        worker._on_enabled()
        worker.start()
        time.sleep(0.05)
        assert worker._thread.name == "backup-db"


def test_thread_name_files_pipeline(fast_intervals):
    """Files pipeline gets a distinct thread name."""
    w = BackupWorker(pipeline="files")
    try:
        with patch.object(w, "_drain_one_batch", return_value=False):
            w._on_enabled()
            w.start()
            time.sleep(0.05)
            assert w._thread.name == "backup-files"
    finally:
        w.stop(timeout=2.0)


def test_start_is_idempotent(worker, fast_intervals):
    """Second start() call on an already-running worker is a no-op +
    warning, not a crash. Guards against admin double-clicking
    'start backup' in the UI."""
    with patch.object(worker, "_drain_one_batch", return_value=False):
        worker._on_enabled()
        worker.start()
        time.sleep(0.05)
        original_thread = worker._thread
        worker.start()  # second call
        assert worker._thread is original_thread


def test_stop_joins_thread_within_timeout(worker, fast_intervals):
    """stop() joins the thread and clears the ._thread reference so a
    subsequent start() can launch a fresh one."""
    with patch.object(worker, "_drain_one_batch", return_value=False):
        worker._on_enabled()
        worker.start()
        time.sleep(0.05)
        worker.stop(timeout=1.0)
        assert worker._thread is None


def test_stop_is_idempotent_when_never_started(worker):
    """stop() on a worker that was never started should log a warning
    rather than blow up (defensive — app shutdown might try to stop
    workers that failed to start)."""
    worker.stop(timeout=0.5)  # no thread running — no crash


def test_stop_wakes_from_backoff_wait(worker, fast_intervals):
    """A worker in BACKOFF is blocked on _reload_event.wait for up to
    backoff_delay seconds. stop() must set _reload_event so the wait
    returns immediately — otherwise app shutdown could hang for the
    full backoff (up to 10 min in production)."""
    worker.backoff_delay = 100.0  # very long backoff
    worker._on_enabled()
    worker.state = State.BACKOFF
    worker.start()
    time.sleep(0.05)  # let thread enter backoff wait
    start = time.monotonic()
    worker.stop(timeout=2.0)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"stop() took {elapsed}s (should be <<1s)"
    assert worker._thread is None


def test_restart_after_stop(worker, fast_intervals):
    """After stop(), a subsequent start() should spin up a fresh
    thread. Used during config reload sequences."""
    with patch.object(worker, "_drain_one_batch", return_value=False):
        worker._on_enabled()
        worker.start()
        time.sleep(0.02)
        worker.stop(timeout=1.0)
        assert worker._thread is None
        worker.start()
        time.sleep(0.02)
        assert worker._thread is not None
        assert worker._thread.is_alive()


# -- Run loop state dispatch ----------------------------------------

def test_run_loop_drains_when_idle(worker, fast_intervals):
    """IDLE → drain attempted. Successful drain that returned True
    keeps us draining; eventually drain returns False and we'd flip
    back to IDLE. Just verify the loop calls drain multiple times."""
    drain = MagicMock(side_effect=[True, True, False, False])
    with patch.object(worker, "_drain_one_batch", drain):
        worker._on_enabled()
        worker.start()
        time.sleep(0.2)  # plenty of ticks
        worker.stop()
    assert drain.call_count >= 3, (
        f"Expected at least 3 drain calls, got {drain.call_count}"
    )


def test_run_loop_paused_does_not_drain(worker, fast_intervals):
    """PAUSED workers never call drain — admin pause must be respected
    even if the queue has work."""
    drain = MagicMock(return_value=False)
    with patch.object(worker, "_drain_one_batch", drain):
        worker._on_enabled()
        worker._on_paused()
        worker.start()
        time.sleep(0.1)
        worker.stop()
    assert drain.call_count == 0


def test_run_loop_disabled_does_not_drain(worker, fast_intervals):
    """DISABLED is the default state. start() does NOT auto-enable —
    that's the caller's responsibility (app wiring layer reads config
    and calls _on_enabled). Verify a never-enabled worker stays quiet."""
    drain = MagicMock(return_value=False)
    with patch.object(worker, "_drain_one_batch", drain):
        worker.start()
        time.sleep(0.1)
        worker.stop()
    assert drain.call_count == 0


def test_run_loop_failure_transitions_to_backoff(worker, fast_intervals):
    """A drain that raises → state becomes BACKOFF + last_error
    captured. The run loop catches the exception (so the thread
    doesn't die) and the state machine handles the rest."""
    drain = MagicMock(side_effect=Exception("Postgres connection refused"))
    with patch.object(worker, "_drain_one_batch", drain):
        worker._on_enabled()
        worker.backoff_delay = 0.01  # short backoff so loop ticks fast
        worker.start()
        time.sleep(0.1)
        worker.stop()
    assert worker.state == State.BACKOFF
    assert "Postgres" in (worker.last_error or "")


def test_run_loop_resume_via_reload_wakes_from_paused(worker, fast_intervals):
    """A paused worker that gets _on_resumed + request_reload should
    exit the paused-wait quickly and start draining. Models the admin
    pressing 'resume' in the UI (Task 16 wires this through the bus)."""
    drain = MagicMock(return_value=False)
    with patch.object(worker, "_drain_one_batch", drain):
        worker._on_enabled()
        worker._on_paused()
        worker.start()
        time.sleep(0.05)
        worker._on_resumed()
        worker.request_reload()  # wakes the paused wait
        time.sleep(0.1)
        worker.stop()
    assert drain.call_count >= 1, "Drain should have run after resume"


def test_run_loop_records_last_attempt_and_success(worker, fast_intervals):
    """The run loop sets last_attempt_at on every drain attempt and
    last_success_at on successful drains. Status panel (Task 17)
    surfaces these for operator visibility."""
    drain = MagicMock(return_value=False)
    with patch.object(worker, "_drain_one_batch", drain):
        worker._on_enabled()
        worker.start()
        time.sleep(0.1)
        worker.stop()
    assert worker.last_attempt_at is not None
    assert worker.last_success_at is not None
    assert isinstance(worker.last_attempt_at, datetime)
    assert isinstance(worker.last_success_at, datetime)


def test_run_loop_does_not_record_success_on_failure(worker, fast_intervals):
    """When drain fails, last_attempt_at advances but last_success_at
    stays None. Operator can see 'last attempt 5s ago but never
    succeeded' in the status panel."""
    drain = MagicMock(side_effect=Exception("bad"))
    with patch.object(worker, "_drain_one_batch", drain):
        worker._on_enabled()
        worker.backoff_delay = 0.01
        worker.start()
        time.sleep(0.1)
        worker.stop()
    assert worker.last_attempt_at is not None
    assert worker.last_success_at is None


def test_run_loop_exits_cleanly_on_stop(worker, fast_intervals):
    """The run loop respects _stop_event and exits the while-loop
    promptly when set. Tests timing rather than logs to avoid
    formatter coupling."""
    with patch.object(worker, "_drain_one_batch", return_value=False):
        worker._on_enabled()
        worker.start()
        time.sleep(0.05)
        thread = worker._thread
        worker.stop(timeout=1.0)
        assert not thread.is_alive(), "Thread should have exited after stop()"


# -- _drain_one_batch ------------------------------------------------

def test_drain_one_batch_dispatches_to_db_method():
    """Pipeline 'db' → _drain.drain_db_batch is called,
    _drain.drain_files_batch is not. Verifies the pipeline-string
    dispatch wiring."""
    w = BackupWorker(pipeline="db")
    with patch("mlss_monitor.backup._drain.drain_db_batch", return_value=True) as db_drain, \
         patch("mlss_monitor.backup._drain.drain_files_batch") as files_drain, \
         patch.object(w, "_build_client", return_value=MagicMock()):
        result = w._drain_one_batch()
    assert result is True
    db_drain.assert_called_once()
    files_drain.assert_not_called()


def test_drain_one_batch_dispatches_to_files_method():
    """Pipeline 'files' → _drain.drain_files_batch is called,
    _drain.drain_db_batch is not."""
    w = BackupWorker(pipeline="files")
    with patch("mlss_monitor.backup._drain.drain_db_batch") as db_drain, \
         patch("mlss_monitor.backup._drain.drain_files_batch", return_value=False) as files_drain, \
         patch.object(w, "_build_client", return_value=MagicMock()):
        result = w._drain_one_batch()
    assert result is False
    files_drain.assert_called_once()
    db_drain.assert_not_called()


def test_drain_one_batch_unknown_pipeline_raises():
    """Schema-drift guard — an unknown pipeline string raises ValueError
    rather than silently no-op'ing."""
    w = BackupWorker(pipeline="bogus")
    with patch.object(w, "_build_client", return_value=MagicMock()):
        with pytest.raises(ValueError, match="bogus"):
            w._drain_one_batch()


# -- _build_client ---------------------------------------------------

def test_build_client_returns_postgres_for_db_pipeline(monkeypatch):
    """_build_client reads current config and constructs a fresh
    PostgresClient. Per-drain instantiation lets a hot-reload (Task 16)
    change credentials without restarting the thread."""
    w = BackupWorker(pipeline="db")
    fake_cfg = {
        "db": {"host": "x", "port": 5432, "database": "d", "user": "u"},
        "advanced": {"connection_timeout_s": 10},
    }
    monkeypatch.setattr("mlss_monitor.backup.worker.config.load",
                        lambda: fake_cfg)
    monkeypatch.setattr("mlss_monitor.backup.worker.config.get_secret",
                        lambda pipeline, key: "secret")
    with patch("mlss_monitor.backup.worker.PostgresClient") as mock_pg:
        w._build_client()
    mock_pg.assert_called_once()
    kwargs = mock_pg.call_args.kwargs
    assert kwargs["host"] == "x"
    assert kwargs["port"] == 5432
    assert kwargs["database"] == "d"
    assert kwargs["user"] == "u"
    assert kwargs["password"] == "secret"
    assert kwargs["source_pi_id"] == "pi-1"
    assert kwargs["timeout"] == 10


def test_build_client_returns_s3_for_files_pipeline(monkeypatch):
    """_build_client reads current config and constructs a fresh
    S3Client for the files pipeline."""
    w = BackupWorker(pipeline="files")
    fake_cfg = {
        "files": {
            "endpoint": "https://s3.local",
            "region": "auto",
            "access_key_id": "AK",
            "bucket_prefix": "mlss-",
        },
        "advanced": {"connection_timeout_s": 10},
    }
    monkeypatch.setattr("mlss_monitor.backup.worker.config.load",
                        lambda: fake_cfg)
    monkeypatch.setattr("mlss_monitor.backup.worker.config.get_secret",
                        lambda pipeline, key: "SK")
    with patch("mlss_monitor.backup.worker.S3Client") as mock_s3:
        w._build_client()
    mock_s3.assert_called_once()
    kwargs = mock_s3.call_args.kwargs
    assert kwargs["endpoint"] == "https://s3.local"
    assert kwargs["region"] == "auto"
    assert kwargs["access_key"] == "AK"
    assert kwargs["secret_key"] == "SK"
    assert kwargs["bucket_prefix"] == "mlss-"
    assert kwargs["timeout"] == 10


def test_build_client_handles_missing_secret(monkeypatch):
    """get_secret returns None when the secret row is absent. The
    client should still be built — the (empty) password will fail at
    connect time, which the run loop catches into BACKOFF rather than
    crashing the build_client call."""
    w = BackupWorker(pipeline="db")
    fake_cfg = {
        "db": {"host": "x", "port": 5432, "database": "d", "user": "u"},
        "advanced": {"connection_timeout_s": 10},
    }
    monkeypatch.setattr("mlss_monitor.backup.worker.config.load",
                        lambda: fake_cfg)
    monkeypatch.setattr("mlss_monitor.backup.worker.config.get_secret",
                        lambda pipeline, key: None)
    with patch("mlss_monitor.backup.worker.PostgresClient") as mock_pg:
        w._build_client()
    kwargs = mock_pg.call_args.kwargs
    assert kwargs["password"] == ""


def test_build_client_unknown_pipeline_raises(monkeypatch):
    """Schema-drift guard symmetric with _drain_one_batch.

    Stub config.load + get_secret so we don't hit the live DB — the
    test is asserting the dispatch ValueError, not the config layer.
    """
    monkeypatch.setattr("mlss_monitor.backup.worker.config.load",
                        lambda: {"db": {}, "files": {}, "advanced": {}})
    monkeypatch.setattr("mlss_monitor.backup.worker.config.get_secret",
                        lambda pipeline, key: None)
    w = BackupWorker(pipeline="bogus")
    with pytest.raises(ValueError, match="bogus"):
        w._build_client()
