"""Background worker that drains the outbox to Postgres + S3.

Phase 4 Task 12: state machine and backoff curve only — no I/O, no
threading lifecycle yet (Task 15 adds the run loop).

Two BackupWorker instances run in parallel — one with pipeline='db'
draining outbox_changes + outbox_delete_scope via PostgresClient, and
one with pipeline='files' draining outbox_blobs via S3Client. They have
independent state machines and backoff timers so a Postgres outage
doesn't block S3 shipping or vice versa.

State machine:
  DISABLED -> (admin enables) -> IDLE
  IDLE -> (work available) -> DRAINING
  DRAINING -> (batch shipped, more work) -> DRAINING
  DRAINING -> (queue empty) -> IDLE
  DRAINING -> (ship error) -> BACKOFF
  BACKOFF -> (timer expires OR reload event) -> DRAINING (retry)
  any state -> (admin pauses) -> PAUSED
  PAUSED -> (admin resumes) -> IDLE
  any state -> (admin disables) -> DISABLED

Backoff curve: 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 600 (cap), 600...
Resets to 1 on any successful ship.

Spec: docs/superpowers/specs/2026-05-18-mlss-backup-design.md
"""
from __future__ import annotations

import enum
import logging
import threading
from datetime import datetime

log = logging.getLogger(__name__)

BACKOFF_CAP_S = 600.0  # 10 minutes — caps the exponential climb so the
                       # worker still re-checks roughly every 10 min even
                       # if the remote has been down for hours.


class State(enum.Enum):
    DISABLED = "disabled"
    IDLE     = "idle"
    DRAINING = "draining"
    BACKOFF  = "backoff"
    PAUSED   = "paused"


class BackupWorker:
    """One pipeline's worker. Pure-logic state machine in this task —
    Tasks 13-15 add the drain loops and threading."""

    def __init__(self, *, pipeline: str) -> None:
        """`pipeline` is 'db' or 'files'. Determines which drain loop runs
        in the eventual _run method (Task 15). Kept as a plain attribute
        so the status emitter (Task 17) can tag emitted events."""
        self.pipeline = pipeline
        self.state = State.DISABLED
        self.backoff_delay = 1.0

        # Set by the run loop on each ship attempt (Task 15). Exposed for
        # the GET /status endpoint (Phase 6) via _publish_status (Task 17).
        self.last_attempt_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.last_error: str | None = None

        # Run-loop control flags (Task 15 reads these).
        self._stop_event = threading.Event()
        # Set by request_reload(). The run loop's BACKOFF sleep waits on
        # this event so admin-saving-config wakes the worker immediately
        # instead of after the (potentially 10-minute) backoff.
        self._reload_event = threading.Event()
        self._thread: threading.Thread | None = None

    # -- Backoff curve --------------------------------------------------

    def _increase_backoff(self) -> None:
        """Double the backoff, capped at BACKOFF_CAP_S."""
        self.backoff_delay = min(self.backoff_delay * 2, BACKOFF_CAP_S)

    def _reset_backoff(self) -> None:
        """Back to 1s — called on any successful ship or admin reload."""
        self.backoff_delay = 1.0

    # -- State transitions ----------------------------------------------

    def _on_enabled(self) -> None:
        """Admin flipped this pipeline's enabled flag to True. Only
        promotes from DISABLED — idempotent from any other state."""
        if self.state == State.DISABLED:
            self.state = State.IDLE

    def _on_disabled(self) -> None:
        """Admin flipped this pipeline's enabled flag to False, OR the
        whole backup feature was disabled. Hard reset from any state."""
        self.state = State.DISABLED

    def _on_paused(self) -> None:
        """Admin pressed the pause button. Override any non-DISABLED
        state — even mid-drain or mid-backoff."""
        if self.state != State.DISABLED:
            self.state = State.PAUSED

    def _on_resumed(self) -> None:
        """Admin pressed resume. Only meaningful from PAUSED — no-op
        from other states so resume-while-not-paused doesn't accidentally
        promote DISABLED to IDLE."""
        if self.state == State.PAUSED:
            self.state = State.IDLE

    def _on_ship_started(self) -> None:
        """Run loop is about to call drain. Recorded on the worker for
        the status panel; the run loop also sets last_attempt_at."""
        self.state = State.DRAINING

    def _on_ship_succeeded(self) -> None:
        """A batch shipped without error. Stay DRAINING — the queue-empty
        signal flips to IDLE. Reset backoff."""
        self._reset_backoff()
        self.state = State.DRAINING

    def _on_ship_failed(self, error: str = "") -> None:
        """A batch failed (Postgres connect refused, S3 5xx, etc.).
        Double the backoff and remember the error string for the status
        panel."""
        self._increase_backoff()
        self.state = State.BACKOFF
        self.last_error = error

    def _on_queue_empty(self) -> None:
        """Drain finished with no pending entries. Only flip if we were
        actively DRAINING — preserves PAUSED/BACKOFF semantics."""
        if self.state == State.DRAINING:
            self.state = State.IDLE

    # -- Hot reload (full wiring in Task 16) ----------------------------

    def request_reload(self) -> None:
        """Called by the event-bus subscriber when admin saves new
        config. Sets the reload event so the BACKOFF sleep in _run
        (Task 15) wakes immediately; also resets backoff so the new
        config gets a fresh chance without waiting out the old backoff."""
        self._reload_event.set()
        self._reset_backoff()
