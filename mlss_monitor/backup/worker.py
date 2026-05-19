"""Background worker that drains the outbox to Postgres + S3.

Phase 4 Task 12: state machine and backoff curve.
Phase 4 Task 13: DB sub-worker drain loop (delegates to
    ``_drain.drain_db_batch``) + per-table PK schema + outbox-pk
    parser (both now live in ``replicated_tables`` / ``_drain``).
Phase 4 Task 14: Files sub-worker drain loop (delegates to
    ``_drain.drain_files_batch``).
Phase 4 Task 15: Threading lifecycle / run loop (`start`/`stop`/`_run`/
    `_drain_one_batch`/`_build_client`) tying the state machine and
    drain functions together into a long-running daemon thread.
Phase 4 Task 16: Hot-reload via event bus — listener thread reads
    `backup_config_changed` events and calls request_reload() so the
    admin saving new config wakes the worker without restart.
Phase 4 Task 17: Status emission — _publish_status() broadcasts a
    `backup_status_changed` event after every meaningful state change
    for the SSE-driven admin status panel.

The actual shipping logic lives in ``mlss_monitor.backup._drain`` —
this module owns the state machine + lifecycle + event-bus
integration so a reader focused on either concern can stay in one
file.

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
import queue
import sqlite3
import threading
from contextlib import closing
from datetime import datetime
from typing import TYPE_CHECKING

from database.init_db import DB_FILE
from mlss_monitor.backup import _drain, config, outbox
from mlss_monitor.backup.postgres_client import PostgresClient
from mlss_monitor.backup.s3_client import S3Client

if TYPE_CHECKING:  # pragma: no cover — typing-only to avoid circular import
    from mlss_monitor.event_bus import EventBus

log = logging.getLogger(__name__)

BACKOFF_CAP_S = 600.0  # 10 minutes — caps the exponential climb so the
                       # worker still re-checks roughly every 10 min even
                       # if the remote has been down for hours.

# Run-loop sleep intervals (module-level so tests can monkeypatch to
# milliseconds and assert behaviour without 30-second waits).
_IDLE_POLL_S = 30.0       # interval between drains when queue is empty
_PAUSED_POLL_S = 1.0      # interval between resume-checks when paused
_DRAINING_POLL_S = 0.1    # tight loop during active drain


class State(enum.Enum):
    DISABLED = "disabled"
    IDLE     = "idle"
    DRAINING = "draining"
    BACKOFF  = "backoff"
    PAUSED   = "paused"


class BackupWorker:
    """One pipeline's worker. State machine + drain loops; Task 15
    will add the run-loop threading that ties them together."""

    def __init__(
        self,
        *,
        pipeline: str,
        event_bus: "EventBus | None" = None,
    ) -> None:
        """`pipeline` is 'db' or 'files'. Determines which drain loop runs
        in `_run` (Task 15) and tags emitted status events (Task 17).

        `event_bus` is optional — if provided, the worker subscribes on
        start() and a dedicated listener thread (Task 16) dispatches
        ``backup_config_changed`` events to `request_reload()`. The same
        bus is used for outbound `backup_status_changed` events (Task
        17). When None (typical for unit tests built before Tasks 16-17),
        both subscribe and publish become no-ops so existing tests
        constructed without an event bus continue to work unchanged."""
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

        # Event-bus wiring (Task 16 + 17). The listener thread reads
        # `_sub_queue` and dispatches `backup_config_changed` to
        # request_reload(). All three default to None so a worker built
        # without an event_bus skips the listener entirely.
        self._event_bus = event_bus
        self._sub_queue: "queue.Queue | None" = None
        self._listener_thread: threading.Thread | None = None

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

    # -- Hot reload (Task 16) -------------------------------------------

    def request_reload(self) -> None:
        """Called by the event-bus subscriber when admin saves new
        config. Sets the reload event so the BACKOFF sleep in _run
        wakes immediately; also resets backoff so the new config gets
        a fresh chance without waiting out the old backoff."""
        self._reload_event.set()
        self._reset_backoff()

    def _listen_loop(self) -> None:
        """Listener thread main loop. Reads the event-bus subscription
        and dispatches `backup_config_changed` events to
        request_reload(). All other events are ignored — we only react
        to the one event type that means "config has changed".

        Blocks on q.get with a 1s timeout so the thread sees
        `_stop_event` and exits promptly without needing a sentinel
        message. The 1s timeout sets the upper bound on how long
        stop() can take to tear down the listener.

        Only runs when an event_bus was injected — when None this
        method is never called because start() skips the listener
        thread entirely."""
        log.info(
            "backup worker %r listener loop starting", self.pipeline,
        )
        assert self._sub_queue is not None  # invariant: start() set it
        while not self._stop_event.is_set():
            try:
                msg = self._sub_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if msg.get("event") == "backup_config_changed":
                self.request_reload()
                # Republish status so the UI sees the post-reload state
                # snapshot promptly (backoff reset, etc.).
                self._publish_status()
        log.info(
            "backup worker %r listener loop exiting", self.pipeline,
        )

    # -- Status emission (Task 17) --------------------------------------

    def _publish_status(self) -> None:
        """Snapshot current worker state + outbox pending counts and
        publish a `backup_status_changed` event for the SSE-driven
        admin status panel.

        No-op if no event_bus was injected (so unit tests that built
        workers without a bus continue to work).

        Best-effort: exceptions during snapshot (DB locked, transient
        SQLite error, etc.) are logged but never raised. Status
        publishing is an observation channel — it must never crash the
        worker that produced the status.

        Thread safety note: the worker state machine isn't currently
        thread-locked. The listener thread can fire request_reload()
        while the run loop is mid-transition, so a published payload
        may capture a transient state (e.g. BACKOFF with the backoff
        already reset to 1s). This is acceptable for a best-effort UI
        panel and doesn't merit adding locks — the next state change
        will publish a fresh snapshot."""
        if self._event_bus is None:
            return
        try:
            with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
                pending_rows = outbox.pending_count_rows(conn)
                pending_blobs = outbox.pending_count_blobs(conn)
                pending_delete_scope = outbox.pending_count_delete_scope(conn)
            self._event_bus.publish("backup_status_changed", {
                "pipeline": self.pipeline,
                "state": self.state.value,
                "backoff_delay_s": self.backoff_delay,
                "last_attempt_at": (
                    self.last_attempt_at.isoformat()
                    if self.last_attempt_at else None
                ),
                "last_success_at": (
                    self.last_success_at.isoformat()
                    if self.last_success_at else None
                ),
                "last_error": self.last_error,
                "pending_rows": pending_rows,
                "pending_blobs": pending_blobs,
                "pending_delete_scope": pending_delete_scope,
            })
        except Exception as exc:  # pylint: disable=broad-except
            log.warning(
                "backup worker %r: _publish_status failed: %s",
                self.pipeline, exc,
            )

    # -- Thread lifecycle (Task 15) -------------------------------------

    def start(self) -> None:
        """Launch the daemon background thread(s).

        Idempotent — calling twice when already running is a no-op
        (logs a warning). The caller should already have set state to
        IDLE via ``_on_enabled()`` if config has this pipeline enabled;
        ``start()`` does NOT consult config (separation of concerns —
        config interpretation happens at app-wiring time, not in the
        worker).

        If an event_bus was injected (Task 16) a second daemon thread
        is spawned to read the subscription queue and dispatch
        ``backup_config_changed`` events to request_reload(). The
        listener thread is named ``backup-{pipeline}-listener`` for
        py-spy readability."""
        if self._thread is not None and self._thread.is_alive():
            log.warning(
                "backup worker %r start() called but thread already alive",
                self.pipeline,
            )
            return
        self._stop_event.clear()

        # Subscribe + launch the listener BEFORE the main run thread so
        # any startup-time `backup_config_changed` events (e.g. from a
        # synchronous config reload during enabling) land on the queue
        # rather than getting dropped on the floor.
        if self._event_bus is not None:
            self._sub_queue = self._event_bus.subscribe()
            self._listener_thread = threading.Thread(
                target=self._listen_loop,
                daemon=True,
                name=f"backup-{self.pipeline}-listener",
            )
            self._listener_thread.start()

        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"backup-{self.pipeline}",
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Signal the run loop to exit and join the thread(s).

        Idempotent — calling on an already-stopped worker logs a
        warning and returns. The join timeout defaults to 2s; if the
        thread is mid-drain when stop is signalled it may take up to
        that long to notice (drain functions don't poll _stop_event
        mid-iteration — they finish their current ship first).

        ``_reload_event`` is also set so a worker blocked on a
        BACKOFF / PAUSED wait wakes up promptly instead of waiting
        out the full delay (up to 10 min in production).

        The listener thread (Task 16) blocks on q.get(timeout=1.0) so
        it exits on its own within ~1s once `_stop_event` is set —
        no extra signal needed. We still join + unsubscribe so the
        event bus doesn't leak a queue reference past the worker's
        lifetime."""
        if self._thread is None or not self._thread.is_alive():
            log.warning(
                "backup worker %r stop() called but no thread running",
                self.pipeline,
            )
            return
        self._stop_event.set()
        self._reload_event.set()  # wake from any wait() so we exit promptly
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            log.warning(
                "backup worker %r did not exit within %ss",
                self.pipeline, timeout,
            )
        self._thread = None

        # Tear down the event-bus subscription (Task 16). The listener
        # thread sees _stop_event.is_set() on its next loop iteration
        # (within the 1s get-timeout) and exits.
        if self._listener_thread is not None:
            self._listener_thread.join(timeout=timeout)
            if self._listener_thread.is_alive():
                log.warning(
                    "backup worker %r listener did not exit within %ss",
                    self.pipeline, timeout,
                )
            self._listener_thread = None
        if self._event_bus is not None and self._sub_queue is not None:
            self._event_bus.unsubscribe(self._sub_queue)
            self._sub_queue = None

    def _run(self) -> None:
        """The thread's main loop. State-dispatched: each iteration
        looks at ``self.state`` and either drains, waits for a config
        change, waits out a backoff, or polls for resume."""
        log.info("backup worker %r run loop starting", self.pipeline)
        while not self._stop_event.is_set():
            if self.state == State.DISABLED:
                # Wait for a reload event (admin enabling the pipeline
                # fires request_reload) OR for stop.
                self._reload_event.wait(timeout=_IDLE_POLL_S)
                self._reload_event.clear()
                continue

            if self.state == State.PAUSED:
                # Wake on reload (admin resume calls request_reload too).
                self._reload_event.wait(timeout=_PAUSED_POLL_S)
                self._reload_event.clear()
                continue

            if self.state == State.BACKOFF:
                # Wait `backoff_delay` seconds OR until reload fires.
                # request_reload() resets backoff to 1s and sets the
                # event so the wait wakes immediately.
                if self._reload_event.wait(timeout=self.backoff_delay):
                    self._reload_event.clear()
                # Whether we slept the full backoff or were woken early,
                # try again on next iteration (state stays BACKOFF until
                # the next drain succeeds or fails).
                continue

            # IDLE or DRAINING: attempt a drain.
            #
            # We intentionally do NOT publish a status event BEFORE
            # the drain. On an empty queue the resulting transition
            # would be IDLE → DRAINING → IDLE in microseconds, and
            # the UI would see a useless DRAINING flicker on every
            # tick. Status is published only at the END of the tick
            # (success / queue-empty / failure) so subscribers see
            # the durable state, not the transient one.
            #
            # ``last_attempt_at`` is still set on every tick so the
            # status panel can report "last drain attempted at …"
            # without needing the intermediate publish.
            self._on_ship_started()
            self.last_attempt_at = datetime.utcnow()
            try:
                shipped_any = self._drain_one_batch()
                self.last_success_at = datetime.utcnow()
                self._on_ship_succeeded()
                if not shipped_any:
                    self._on_queue_empty()
                # Single publish at the END of the tick captures the
                # durable state (DRAINING if more work shipped this
                # tick, IDLE if queue empty). The UI cares about the
                # settled state, not the transient mid-tick DRAINING.
                self._publish_status()
            except Exception as exc:  # pylint: disable=broad-except
                log.warning(
                    "backup ship failed (%s): %s", self.pipeline, exc,
                )
                self._on_ship_failed(error=str(exc))
                self._publish_status()  # transition into BACKOFF
                # Falls through to the sleep below — the BACKOFF state
                # means the NEXT iteration takes the backoff branch
                # above.

            # Sleep between drains.
            # IDLE     → long sleep (~30s) so we don't poll SQLite hot.
            # DRAINING → tight loop (0.1s) so a backlog drains quickly.
            # BACKOFF (just-set) → 0s here; next iteration handles the
            #                      backoff wait.
            if self.state == State.IDLE:
                self._stop_event.wait(timeout=_IDLE_POLL_S)
            elif self.state == State.DRAINING:
                self._stop_event.wait(timeout=_DRAINING_POLL_S)

        log.info("backup worker %r run loop exiting", self.pipeline)

    def _drain_one_batch(self) -> bool:
        """Open a fresh SQLite connection + build a fresh client +
        dispatch to the appropriate drain function in ``_drain``.

        The connection is short-lived (one drain). Re-opening it every
        tick keeps WAL locks from piling up under concurrent writers
        (sensor loop, grow handlers, route POSTs all want their own
        write locks).

        The client is also fresh each tick so a hot-reload (Task 16)
        that changed endpoint/credentials takes effect on the next
        drain without bouncing the thread.

        The actual shipping logic lives in ``_drain.drain_db_batch`` /
        ``_drain.drain_files_batch`` — this method just dispatches.

        Returns the drain function's bool ``did_work`` value (True if
        any rows/blobs were shipped, False if the outbox was empty).
        """
        client = self._build_client()
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            if self.pipeline == "db":
                return _drain.drain_db_batch(conn, client)
            if self.pipeline == "files":
                return _drain.drain_files_batch(conn, client)
            raise ValueError(
                f"Unknown pipeline {self.pipeline!r} — "
                "expected 'db' or 'files'"
            )

    def _build_client(self):
        """Read current config and return a pipeline-appropriate client.

        Per-drain instantiation lets a hot-reload (Task 16) change
        credentials without restarting the thread — the next drain
        picks up the new config automatically.

        Raises ``ValueError`` if pipeline is anything other than
        'db' / 'files'.
        """
        cfg = config.load()
        if self.pipeline == "db":
            return PostgresClient(
                host=cfg["db"]["host"],
                port=cfg["db"]["port"],
                database=cfg["db"]["database"],
                user=cfg["db"]["user"],
                password=config.get_secret("db", "password") or "",
                source_pi_id=self._source_pi_id(),
                timeout=cfg["advanced"]["connection_timeout_s"],
            )
        if self.pipeline == "files":
            return S3Client(
                endpoint=cfg["files"]["endpoint"],
                region=cfg["files"]["region"],
                access_key=cfg["files"]["access_key_id"],
                secret_key=config.get_secret("files", "secret_key") or "",
                bucket_prefix=cfg["files"]["bucket_prefix"],
                timeout=cfg["advanced"]["connection_timeout_s"],
            )
        raise ValueError(
            f"Unknown pipeline {self.pipeline!r} — expected 'db' or 'files'"
        )

    def _source_pi_id(self) -> str:
        """source_pi_id tags this Pi's data on the server. For Phase 4
        we default to ``'pi-1'``; Phase 6 will expose this as a config
        field so multi-Pi deployments can distinguish their data.

        Returns a non-empty string. The PostgresClient constructor
        sentinel will reject empty / whitespace input — this assertion
        is a belt-and-braces for callers that bypass the client (e.g.
        future log helpers)."""
        pi_id = "pi-1"
        assert pi_id and pi_id.strip(), "_source_pi_id() must return non-empty"
        return pi_id
