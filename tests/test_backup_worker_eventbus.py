"""BackupWorker event-bus integration (Phase 4 Tasks 16 + 17).

Task 16 — Hot-reload:
  - Subscribes to the EventBus on start().
  - `backup_config_changed` events trigger request_reload(), which
    resets backoff and wakes the run loop from any sleep.
  - Unrelated events (sensor_update, anomaly_scores, etc.) are
    ignored — only backup_config_changed counts.
  - stop() unsubscribes cleanly and the listener thread exits
    promptly via the get-timeout.

Task 17 — Status emission:
  - _publish_status() snapshots worker state + outbox pending counts
    and broadcasts a `backup_status_changed` event.
  - The run loop publishes after every meaningful state transition:
    ship_started, ship_succeeded, queue_empty, ship_failed.
  - Best-effort: a snapshot error (e.g. DB locked) logs but never
    propagates — status publishing must not crash the worker.

DI: event_bus is constructor-injected and optional. Workers built
without an event bus run normally — subscribe + publish become
no-ops. This keeps Tasks 12-15's existing tests unchanged.

Spec: docs/superpowers/specs/2026-05-18-mlss-backup-design.md
"""
import sqlite3
import tempfile
import gc
import queue
import time
from pathlib import Path
from unittest.mock import patch
import pytest

from mlss_monitor.backup.worker import BackupWorker, State
from mlss_monitor.event_bus import EventBus


@pytest.fixture
def fast_intervals(monkeypatch):
    """Squash run-loop sleep constants to milliseconds so tests don't
    have to wait for 30-second IDLE polls."""
    monkeypatch.setattr("mlss_monitor.backup.worker._IDLE_POLL_S", 0.01)
    monkeypatch.setattr("mlss_monitor.backup.worker._PAUSED_POLL_S", 0.005)
    monkeypatch.setattr("mlss_monitor.backup.worker._DRAINING_POLL_S", 0.005)


@pytest.fixture
def db_path(monkeypatch):
    """Real SQLite tempfile with the full live schema. The worker's
    _publish_status opens its own connection to DB_FILE, so we patch
    that import target plus init_db.DB_FILE so create_db hits the
    tempfile."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    original = init_db.DB_FILE
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.backup.worker.DB_FILE", tmp.name)
    init_db.create_db()
    yield tmp.name
    init_db.DB_FILE = original
    gc.collect()
    Path(tmp.name).unlink(missing_ok=True)


# ── Subscribe / hot-reload (Task 16) ─────────────────────────────────

def test_worker_subscribes_on_start_when_event_bus_given(fast_intervals):
    """start() with an event_bus → bus.subscribe() is called and the
    subscriber count grows. stop() must unsubscribe so we don't leak
    queues across worker lifetimes."""
    bus = EventBus()
    w = BackupWorker(pipeline="db", event_bus=bus)
    with patch.object(w, "_drain_one_batch", return_value=False):
        w._on_enabled()
        w.start()
        time.sleep(0.05)
        assert bus.subscriber_count() == 1
        w.stop()
        assert bus.subscriber_count() == 0


def test_worker_with_no_event_bus_does_not_crash(fast_intervals):
    """A worker built without an event bus (e.g. Tasks 12-15 unit
    tests) must still run normally — subscribe + publish are no-ops.
    Guards against the DI-required regression."""
    w = BackupWorker(pipeline="db", event_bus=None)
    with patch.object(w, "_drain_one_batch", return_value=False):
        w._on_enabled()
        w.start()
        time.sleep(0.05)
        w.stop()
    # No exception raised → pass.


def test_backup_config_changed_event_triggers_request_reload(fast_intervals):
    """Publishing `backup_config_changed` should fire request_reload()
    which (a) resets backoff to 1.0 and (b) sets the reload event so
    the BACKOFF wait wakes immediately."""
    bus = EventBus()
    w = BackupWorker(pipeline="db", event_bus=bus)
    with patch.object(w, "_drain_one_batch", return_value=False):
        w._on_enabled()
        # Park the worker in BACKOFF with a long delay so we can
        # observe the reload waking it back to a 1s backoff.
        w._on_ship_failed(error="x")
        w.backoff_delay = 100.0
        w.start()
        time.sleep(0.05)
        bus.publish("backup_config_changed", {})
        time.sleep(0.1)
        # request_reload() resets backoff to 1.0
        assert w.backoff_delay == 1.0
        w.stop()


def test_unrelated_events_do_not_trigger_reload(fast_intervals):
    """Events other than backup_config_changed (sensor_update,
    anomaly_scores, etc.) must NOT trigger request_reload — otherwise
    every sensor tick would reset the backoff curve and we'd hammer
    a broken Postgres instance instead of backing off."""
    bus = EventBus()
    w = BackupWorker(pipeline="db", event_bus=bus)
    with patch.object(w, "_drain_one_batch", return_value=False):
        w._on_enabled()
        w._on_ship_failed(error="x")
        w.backoff_delay = 100.0
        w.start()
        time.sleep(0.05)
        bus.publish("sensor_update", {"temperature": 22})
        bus.publish("anomaly_scores", {"score": 0.7})
        time.sleep(0.05)
        # Backoff untouched → reload did not fire
        assert w.backoff_delay == 100.0
        w.stop()


def test_stop_unsubscribes_from_event_bus(fast_intervals):
    """Symmetric to subscribe-on-start: stop() releases the
    subscription. Important for the future scenario where the app
    reloads config and rebuilds workers — old subscriptions would
    otherwise pile up."""
    bus = EventBus()
    w = BackupWorker(pipeline="db", event_bus=bus)
    with patch.object(w, "_drain_one_batch", return_value=False):
        w._on_enabled()
        w.start()
        time.sleep(0.05)
        assert bus.subscriber_count() == 1
        w.stop()
        assert bus.subscriber_count() == 0


def test_listener_thread_exits_promptly_on_stop(fast_intervals):
    """The listener thread blocks on q.get(timeout=1.0). stop() must
    return within ~1s, otherwise app shutdown could hang for the full
    get-timeout (or longer if the timeout were larger)."""
    bus = EventBus()
    w = BackupWorker(pipeline="db", event_bus=bus)
    with patch.object(w, "_drain_one_batch", return_value=False):
        w._on_enabled()
        w.start()
        time.sleep(0.05)
        start = time.monotonic()
        w.stop(timeout=2.0)
        elapsed = time.monotonic() - start
    assert elapsed < 1.5, f"stop() took {elapsed}s — listener didn't exit"


def test_listener_thread_is_daemon_and_named(fast_intervals):
    """py-spy / threading dump readability — the listener thread must
    be daemon (so the app can exit) and clearly named per pipeline."""
    bus = EventBus()
    w = BackupWorker(pipeline="files", event_bus=bus)
    with patch.object(w, "_drain_one_batch", return_value=False):
        w._on_enabled()
        w.start()
        time.sleep(0.05)
        assert w._listener_thread is not None
        assert w._listener_thread.daemon is True
        assert w._listener_thread.name == "backup-files-listener"
        w.stop()


# ── Status emission (Task 17) ────────────────────────────────────────

def test_publish_status_is_noop_with_no_event_bus():
    """Smoke: no bus → no crash, no publish. _publish_status must
    early-return rather than try to call .publish on None."""
    w = BackupWorker(pipeline="db", event_bus=None)
    w._publish_status()  # should not raise


def test_publish_status_emits_event_with_full_payload(db_path):
    """The status payload must include every field the UI panel needs:
    pipeline, state, backoff_delay, last_attempt_at, last_success_at,
    last_error, pending_rows, pending_blobs, pending_delete_scope."""
    bus = EventBus()
    w = BackupWorker(pipeline="db", event_bus=bus)
    w._on_enabled()
    w._on_ship_started()
    sub = bus.subscribe()
    w._publish_status()
    msg = sub.get(timeout=1.0)
    assert msg["event"] == "backup_status_changed"
    data = msg["data"]
    assert data["pipeline"] == "db"
    assert data["state"] == "draining"
    assert "backoff_delay_s" in data
    assert "last_attempt_at" in data
    assert "last_success_at" in data
    assert "last_error" in data
    assert "pending_rows" in data
    assert "pending_blobs" in data
    assert "pending_delete_scope" in data


def test_publish_status_pending_counts_reflect_outbox(db_path):
    """Plant outbox entries and verify the status payload sees them.
    The counts are read at publish-time (fresh SQLite query) so the
    UI panel always reflects the current backlog."""
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

    bus = EventBus()
    w = BackupWorker(pipeline="db", event_bus=bus)
    w._on_enabled()
    sub = bus.subscribe()
    w._publish_status()
    msg = sub.get(timeout=1.0)
    data = msg["data"]
    assert data["pending_rows"] == 1
    assert data["pending_blobs"] == 1
    assert data["pending_delete_scope"] == 1


def test_publish_status_swallows_exceptions(monkeypatch, db_path):
    """If a pending_count_* call raises (DB locked, schema drift,
    whatever), _publish_status must log + continue rather than
    propagate — status publishing is best-effort and never allowed
    to crash the worker."""
    bus = EventBus()
    w = BackupWorker(pipeline="db", event_bus=bus)
    w._on_enabled()
    monkeypatch.setattr(
        "mlss_monitor.backup.worker.outbox.pending_count_rows",
        lambda c: (_ for _ in ()).throw(Exception("DB locked")),
    )
    w._publish_status()  # must not raise


def test_publish_status_iso_format_for_timestamps(db_path):
    """last_attempt_at + last_success_at are emitted as ISO strings
    so the SSE consumer can JSON-decode them. None when unset."""
    from datetime import datetime
    bus = EventBus()
    w = BackupWorker(pipeline="db", event_bus=bus)
    w._on_enabled()
    w.last_attempt_at = datetime(2026, 5, 18, 12, 0, 0)
    sub = bus.subscribe()
    w._publish_status()
    msg = sub.get(timeout=1.0)
    data = msg["data"]
    assert data["last_attempt_at"] == "2026-05-18T12:00:00"
    assert data["last_success_at"] is None


def test_run_loop_publishes_after_state_transitions(fast_intervals, db_path):
    """The full run loop produces at least one status event after a
    drain cycle — covers ship_started (→ DRAINING) and
    queue_empty (→ IDLE) state transitions firing _publish_status."""
    bus = EventBus()
    w = BackupWorker(pipeline="db", event_bus=bus)
    sub = bus.subscribe()
    with patch.object(w, "_drain_one_batch", return_value=False):
        w._on_enabled()
        w.start()
        time.sleep(0.1)
        w.stop()
    # Collect every status event left on the subscriber queue.
    events = []
    while True:
        try:
            events.append(sub.get_nowait())
        except queue.Empty:
            break
    status_events = [e for e in events if e["event"] == "backup_status_changed"]
    assert len(status_events) >= 1, (
        "Run loop should publish at least one status event"
    )
    states_seen = {e["data"]["state"] for e in status_events}
    # Either draining or idle should appear (depending on whether the
    # first drain returned and flipped to IDLE before stop).
    assert states_seen & {"draining", "idle"}, (
        f"Expected draining or idle in {states_seen}"
    )


def test_run_loop_publishes_on_failure(fast_intervals, db_path):
    """Drain failure → _on_ship_failed → BACKOFF status published
    with last_error populated. The SSE-driven UI panel uses this to
    flag a red status badge + show the error string."""
    bus = EventBus()
    w = BackupWorker(pipeline="db", event_bus=bus)
    sub = bus.subscribe()
    with patch.object(w, "_drain_one_batch", side_effect=Exception("boom")):
        w._on_enabled()
        w.backoff_delay = 0.01
        w.start()
        time.sleep(0.1)
        w.stop()
    events = []
    while True:
        try:
            events.append(sub.get_nowait())
        except queue.Empty:
            break
    status_events = [e for e in events if e["event"] == "backup_status_changed"]
    backoff_events = [e for e in status_events if e["data"]["state"] == "backoff"]
    assert backoff_events, "Failure must publish at least one backoff status"
    assert backoff_events[0]["data"]["last_error"] == "boom"
