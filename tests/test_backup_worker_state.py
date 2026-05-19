"""BackupWorker state machine + exponential backoff curve.

Pure-logic tests — no threading, no I/O. Uses BackupWorker.__new__(...)
to bypass __init__ where needed (avoids the threading.Event allocation
when we only want to test the transitions).
"""
import pytest

from mlss_monitor.backup.worker import BackupWorker, State, BACKOFF_CAP_S


@pytest.fixture
def worker():
    """Fresh worker, no thread started. Pipeline label doesn't matter
    for state-machine tests."""
    return BackupWorker(pipeline="db")


def test_initial_state_is_disabled(worker):
    """A freshly-constructed worker starts DISABLED. Admin must
    explicitly enable via config + _on_enabled before it ships."""
    assert worker.state == State.DISABLED
    assert worker.backoff_delay == 1.0
    assert worker.last_attempt_at is None
    assert worker.last_success_at is None
    assert worker.last_error is None


def test_backoff_curve_doubles_to_cap():
    """1 → 2 → 4 → 8 → 16 → 32 → 64 → 128 → 256 → 512 → 600 (cap) → 600..."""
    w = BackupWorker.__new__(BackupWorker)
    w.backoff_delay = 1.0
    delays = [w.backoff_delay]
    for _ in range(15):
        w._increase_backoff()
        delays.append(w.backoff_delay)
    expected = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0, 512.0,
                BACKOFF_CAP_S, BACKOFF_CAP_S, BACKOFF_CAP_S,
                BACKOFF_CAP_S, BACKOFF_CAP_S, BACKOFF_CAP_S]
    assert delays == expected


def test_backoff_cap_constant_is_ten_minutes():
    """600s = 10 min. If this changes, the operator-visible status panel
    should be updated too."""
    assert BACKOFF_CAP_S == 600.0


def test_backoff_resets_to_one_on_success():
    w = BackupWorker.__new__(BackupWorker)
    w.backoff_delay = 256.0
    w._reset_backoff()
    assert w.backoff_delay == 1.0


def test_enabled_transitions_disabled_to_idle(worker):
    """The only legal exit from DISABLED is via _on_enabled."""
    assert worker.state == State.DISABLED
    worker._on_enabled()
    assert worker.state == State.IDLE


def test_enabled_is_noop_from_non_disabled_states(worker):
    """Idempotent: calling _on_enabled while already IDLE/DRAINING/etc.
    must not regress to IDLE."""
    worker._on_enabled()  # → IDLE
    worker._on_ship_started()  # → DRAINING
    worker._on_enabled()  # should NOT regress
    assert worker.state == State.DRAINING


def test_disabled_overrides_any_state(worker):
    """_on_disabled is the kill switch — from any state → DISABLED."""
    worker._on_enabled()  # IDLE
    worker._on_ship_started()  # DRAINING
    worker._on_disabled()
    assert worker.state == State.DISABLED


def test_paused_works_from_idle(worker):
    worker._on_enabled()  # IDLE
    worker._on_paused()
    assert worker.state == State.PAUSED


def test_paused_works_from_draining(worker):
    """An admin pause mid-drain should land us in PAUSED even though
    we're actively shipping a batch."""
    worker._on_enabled()
    worker._on_ship_started()  # DRAINING
    worker._on_paused()
    assert worker.state == State.PAUSED


def test_paused_works_from_backoff(worker):
    """An admin pause while in BACKOFF should override the backoff
    timer — we're now paused, not just waiting."""
    worker._on_enabled()
    worker._on_ship_failed(error="test")
    assert worker.state == State.BACKOFF
    worker._on_paused()
    assert worker.state == State.PAUSED


def test_paused_does_not_apply_to_disabled(worker):
    """A DISABLED worker shouldn't suddenly become PAUSED — DISABLED is
    a stronger state (config is off, not just temporarily paused)."""
    assert worker.state == State.DISABLED
    worker._on_paused()
    assert worker.state == State.DISABLED


def test_resumed_transitions_paused_to_idle(worker):
    worker._on_enabled()
    worker._on_paused()
    worker._on_resumed()
    assert worker.state == State.IDLE


def test_resumed_is_noop_from_non_paused(worker):
    """Calling resume from IDLE/DRAINING shouldn't break anything."""
    worker._on_enabled()
    worker._on_ship_started()  # DRAINING
    worker._on_resumed()
    assert worker.state == State.DRAINING  # unchanged


def test_ship_started_transitions_to_draining(worker):
    worker._on_enabled()
    worker._on_ship_started()
    assert worker.state == State.DRAINING


def test_ship_succeeded_stays_draining_resets_backoff(worker):
    """Success in the middle of a multi-batch drain stays DRAINING —
    the queue-empty signal is what flips us to IDLE."""
    worker._on_enabled()
    worker._on_ship_started()
    worker.backoff_delay = 64.0  # we were in backoff before
    worker._on_ship_succeeded()
    assert worker.state == State.DRAINING
    assert worker.backoff_delay == 1.0  # reset


def test_ship_failed_transitions_to_backoff_and_doubles(worker):
    worker._on_enabled()
    worker._on_ship_started()
    assert worker.backoff_delay == 1.0
    worker._on_ship_failed(error="psycopg2.OperationalError: ...")
    assert worker.state == State.BACKOFF
    assert worker.backoff_delay == 2.0
    assert "OperationalError" in worker.last_error


def test_ship_failed_chained_backoffs_cap(worker):
    """N consecutive failures: backoff doubles each time, then caps."""
    worker._on_enabled()
    worker._on_ship_started()
    for _ in range(20):
        worker._on_ship_failed(error="fail")
    assert worker.backoff_delay == BACKOFF_CAP_S


def test_queue_empty_transitions_draining_to_idle(worker):
    worker._on_enabled()
    worker._on_ship_started()
    worker._on_queue_empty()
    assert worker.state == State.IDLE


def test_queue_empty_is_noop_from_other_states(worker):
    """A queue-empty signal received while we're PAUSED or BACKOFF
    shouldn't promote us to IDLE — only the legitimate flow
    DRAINING → IDLE is allowed."""
    worker._on_enabled()
    worker._on_paused()
    worker._on_queue_empty()
    assert worker.state == State.PAUSED  # unchanged


def test_request_reload_sets_event_and_resets_backoff(worker):
    """When admin saves new config:
      (a) the reload event fires, so a BACKOFF sleep can wake immediately
      (b) the backoff resets, so we try ship-with-new-config without delay
    """
    worker._on_enabled()
    worker._on_ship_failed(error="x")
    assert worker.backoff_delay == 2.0
    assert not worker._reload_event.is_set()
    worker.request_reload()
    assert worker._reload_event.is_set()
    assert worker.backoff_delay == 1.0


def test_pipeline_label_is_preserved(worker):
    """Tasks 13-14 will dispatch on pipeline. Verify it round-trips."""
    assert worker.pipeline == "db"


def test_two_workers_have_independent_state():
    """A db worker failing should not put a files worker into BACKOFF."""
    db = BackupWorker(pipeline="db")
    files = BackupWorker(pipeline="files")
    db._on_enabled()
    files._on_enabled()
    db._on_ship_failed(error="db down")
    assert db.state == State.BACKOFF
    assert files.state == State.IDLE
