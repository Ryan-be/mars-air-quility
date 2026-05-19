"""Background worker that drains the outbox to Postgres + S3.

Phase 4 Task 12: state machine and backoff curve.
Phase 4 Task 13: DB sub-worker drain loop (`_drain_db_batch`) +
    per-table PK schema + outbox-pk parser.
Phase 4 Task 14: Files sub-worker drain loop (`_drain_files_batch`) +
    target_key → bucket_suffix routing helper.
Phase 4 Task 15: Threading lifecycle / run loop (`start`/`stop`/`_run`/
    `_drain_one_batch`/`_build_client`) tying the state machine and
    drain loops together into a long-running daemon thread.
Phase 4 Task 16: Hot-reload via event bus — listener thread reads
    `backup_config_changed` events and calls request_reload() so the
    admin saving new config wakes the worker without restart.
Phase 4 Task 17: Status emission — _publish_status() broadcasts a
    `backup_status_changed` event after every meaningful state change
    for the SSE-driven admin status panel.

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
import json
import logging
import os
import queue
import sqlite3
import threading
from contextlib import closing
from datetime import datetime
from typing import TYPE_CHECKING

from database.init_db import DB_FILE
from mlss_monitor.backup import config, outbox
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


# ── Replicated-table PK schema ─────────────────────────────────────
#
# Per-table PK metadata, mirrors the REPLICATED_TABLES list in
# tests/test_no_direct_writes_to_replicated_tables.py. Used by
# _drain_db_batch to:
#   * parse the outbox `pk` string back into typed value(s)
#   * build the SELECT … WHERE pk_col = ? query that reads live state
#   * pass pk_columns into PostgresClient.upsert_rows
#
# pk_columns: ordered column names of the SQLite PK. The conflict target
# on Postgres is (*pk_columns, source_pi_id).
#
# pk_types: matching Python types — outbox.pk is always TEXT, so "1"
# must be parsed to int(1) for INTEGER-PK tables before the WHERE
# binding lines up.
#
# Verified against database/init_db.py + database/grow_schema.py.
# Most tables have INTEGER autoincrement PK; the exceptions are
# `incidents` (TEXT id like "INC-2026-05-18T12:00:00") and the
# composite-PK tables (incident_alerts, incident_signature_features,
# grow_unit_capabilities).
_REPLICATED_TABLES: dict[str, dict] = {
    "sensor_data":                 {"pk_columns": ["id"],                         "pk_types": [int]},
    "weather_log":                 {"pk_columns": ["id"],                         "pk_types": [int]},
    "inferences":                  {"pk_columns": ["id"],                         "pk_types": [int]},
    "event_tags":                  {"pk_columns": ["id"],                         "pk_types": [int]},
    "incidents":                   {"pk_columns": ["id"],                         "pk_types": [str]},   # TEXT PK
    "incident_alerts":             {"pk_columns": ["incident_id", "alert_id"],    "pk_types": [str, int]},
    "incident_signature_features": {"pk_columns": ["incident_id", "feature_idx"], "pk_types": [str, int]},
    "grow_units":                  {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_telemetry":              {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_unit_capabilities":      {"pk_columns": ["unit_id", "channel"],         "pk_types": [int, str]},
    "grow_watering_events":        {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_errors":                 {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_photos":                 {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_journal_entries":        {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_plant_profiles":         {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_light_windows":          {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_timelapse_jobs":         {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_medium_defaults":        {"pk_columns": ["medium_type"],                "pk_types": [str]},   # TEXT PK
}


def _parse_pk(pk_str: str, pk_types: list[type]) -> tuple:
    """Convert outbox.pk (always TEXT) into a tuple of typed values.

    Single-PK tables: pk_str is just the value, e.g. "42" → (42,) for
    int PK or "INC-…" → ("INC-…",) for str PK.

    Composite-PK tables: pk_str is f"{a}:{b}" — for example "1:pump"
    for grow_unit_capabilities(unit_id, channel). The "incidents:alerts"
    case is trickier because the incident_id itself contains colons
    (ISO 8601 timestamp like "INC-2026-05-18T12:00:00"), so we always
    split from the RIGHT len(pk_types)-1 times. That way the rightmost
    colon delimits the trailing integer (alert_id / feature_idx) and
    the timestamp's internal colons stay intact.
    """
    if len(pk_types) == 1:
        parts = [pk_str]
    else:
        # Composite. rsplit from the right N-1 times so any colons
        # inside an early-position string PK are preserved.
        parts = pk_str.rsplit(":", len(pk_types) - 1)
    return tuple(t(p) for t, p in zip(pk_types, parts))


def _read_live_row(
    conn: sqlite3.Connection,
    table: str,
    pk_columns: list[str],
    pk_values: tuple,
) -> dict | None:
    """SELECT * FROM {table} WHERE pk match. Returns a dict of
    column→value, or None if the row no longer exists.

    None means the row was deleted between enqueue and ship; the drain
    loop logs it and drops the outbox entry without shipping. The PG
    side keeps its previously-shipped copy (append-mostly delete
    semantics — operator-cleared rows on the Pi do NOT propagate)."""
    conn.row_factory = sqlite3.Row
    where = " AND ".join(f"{c} = ?" for c in pk_columns)
    row = conn.execute(
        f"SELECT * FROM {table} WHERE {where}",
        pk_values,
    ).fetchone()
    return dict(row) if row else None


# ── Files pipeline: target_key → bucket suffix ─────────────────────

def _bucket_suffix_for_key(target_key: str) -> str:
    """Derive the S3 bucket suffix from the target_key prefix.

    target_key shapes (from Phase 2 file pipeline writers):
      - 'unit_NNN/YYYY-MM-DD/...jpg'         → 'photos'
      - 'anomaly/<channel>/<iso>.pkl'        → 'anomaly'
      - 'multivar_anomaly/<model>/<iso>.pkl' → 'multivar-anomaly'
      - 'attribution/classifier/<iso>.pkl'   → 'attribution'

    Note the underscore→hyphen normalisation for ``multivar_anomaly``:
    S3 bucket naming uses hyphens by convention (DNS-compatible names),
    but the on-disk filesystem layout uses underscores to match the
    Python module names (``mlss_monitor.multivar_anomaly``). Don't
    "fix" this — both forms are intentional.

    Raises ``ValueError`` on unknown prefix. The drain loop catches
    this and logs + drops the outbox entry so a schema-drift prefix
    can't permanently block the queue.

    Order note: ``multivar_anomaly/`` check goes before ``anomaly/``
    for clarity, though prefix matching wouldn't actually collide
    ("multivar_anomaly" does not start with "anomaly").
    """
    if target_key.startswith("unit_"):
        return "photos"
    if target_key.startswith("multivar_anomaly/"):
        return "multivar-anomaly"
    if target_key.startswith("anomaly/"):
        return "anomaly"
    if target_key.startswith("attribution/"):
        return "attribution"
    raise ValueError(f"Cannot derive S3 bucket for target_key {target_key!r}")


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

    # -- DB pipeline drain (Task 13) ------------------------------------

    def _drain_db_batch(
        self,
        sqlite_conn: sqlite3.Connection,
        pg_client,
    ) -> bool:
        """Drain one batch of outbox entries to Postgres.

        Order of operations:

          1. ``outbox_delete_scope`` FIRST — strict-mirror wipes must
             land on the server BEFORE the corresponding INSERTs so a
             DELETE+INSERT replace arrives atomically. If we shipped
             INSERTs first, the server would briefly have old + new
             versions overlapping; a mid-batch crash would leave stale
             rows behind.

          2. ``outbox_changes`` second — group entries by table_name,
             fetch the CURRENT row state from live SQLite for each pk,
             upsert per-table via PostgresClient.

        Returns True if any work was shipped (rows or scopes), False
        if both queues were empty. The Task 15 run loop uses False to
        flip the worker state back to IDLE.

        Edge cases:

        - Missing live row (deleted between enqueue and ship): log +
          drop the outbox entry without shipping. Normal for
          append-mostly tables — operator cleared the Pi-side row but
          the server keeps its copy (append-mostly deletes don't
          enqueue a delete_scope).

        - Unknown table in outbox (schema drift between the lint
          allowlist and ``_REPLICATED_TABLES``): log + drop so the
          queue doesn't permanently block.

        - PostgresClient errors propagate. The run loop catches them
          and transitions to BACKOFF via ``_on_ship_failed``. Outbox
          entries are NOT deleted on failure — they retry on the next
          drain cycle.

        Connections + client are passed in so this is unit-testable in
        isolation; the run loop (Task 15) will own the live SQLite
        connection and the PostgresClient instance and pass them in.
        """
        # 1. Delete-scope queue first.
        scope_entries = outbox.peek_delete_scope(sqlite_conn, limit=100)
        for entry in scope_entries:
            scope = json.loads(entry["scope_json"])
            pg_client.delete_scope(
                table=entry["table_name"], scope=scope,
            )
        if scope_entries:
            # Delete only AFTER every ship succeeded — if any
            # delete_scope call above raised, the exception propagated
            # and we never get here; the entries stay for retry.
            outbox.delete_delete_scope(
                sqlite_conn, ids=[e["id"] for e in scope_entries],
            )

        # 2. Row pointers.
        row_entries = outbox.peek_rows(sqlite_conn, limit=1000)
        if not row_entries:
            # Only-delete-scope batch (or empty batch). Return True
            # only when scopes shipped (work was done).
            return bool(scope_entries)

        # Group by table_name. For each table we'll fetch live state
        # and ship as a single batch.
        by_table: dict[str, list[dict]] = {}
        ids_to_delete: list[int] = []
        for entry in row_entries:
            table = entry["table_name"]
            schema = _REPLICATED_TABLES.get(table)
            if schema is None:
                # Schema drift — unknown table. Log + orphan the
                # entry so a future schema change doesn't permanently
                # block the queue. (If this fires in production, the
                # lint allowlist or _REPLICATED_TABLES is out of date.)
                log.warning(
                    "backup db: unknown replicated table %r — "
                    "dropping outbox entry id=%s",
                    table, entry["id"],
                )
                ids_to_delete.append(entry["id"])
                continue
            pk_values = _parse_pk(entry["pk"], schema["pk_types"])
            live_row = _read_live_row(
                sqlite_conn, table, schema["pk_columns"], pk_values,
            )
            if live_row is None:
                # Row was deleted between enqueue and ship. Drop the
                # outbox entry; the server keeps its previously-shipped
                # copy (append-mostly delete doesn't propagate).
                log.info(
                    "backup db: live row %r:%r missing — "
                    "dropping outbox entry id=%s",
                    table, entry["pk"], entry["id"],
                )
                ids_to_delete.append(entry["id"])
                continue
            by_table.setdefault(table, []).append(live_row)
            ids_to_delete.append(entry["id"])

        # Ship per-table. Errors propagate — outbox entries stay queued
        # for retry because delete_rows is only called after all
        # upserts succeed.
        for table, rows in by_table.items():
            schema = _REPLICATED_TABLES[table]
            pg_client.upsert_rows(
                table=table,
                pk_columns=schema["pk_columns"],
                rows=rows,
            )

        if ids_to_delete:
            outbox.delete_rows(sqlite_conn, ids=ids_to_delete)

        return True

    # -- Files pipeline drain (Task 14) ---------------------------------

    def _drain_files_batch(
        self,
        sqlite_conn: sqlite3.Connection,
        s3_client,
    ) -> bool:
        """Drain one batch of outbox_blobs entries to S3.

        Per-entry flow:

          1. If ``source_path`` no longer exists on disk: log + drop
             the outbox entry without any network round-trip. Normal
             for append-mostly artefacts where the operator cleared
             the Pi-side file (e.g. clear_photos route unlinks JPEGs)
             but the server keeps its previously-shipped copy.

          2. Derive the S3 bucket suffix from ``target_key`` via
             ``_bucket_suffix_for_key``. Unknown prefix → log + drop
             so schema drift doesn't permanently block the queue.

          3. HEAD-check the destination. If the object is already
             there (idempotency — previous ship succeeded but the
             worker crashed before the outbox delete committed),
             skip the upload but still drop the outbox entry.

          4. PUT if missing.

          5. Drop the outbox entry after a successful ship (or skip).

        Returns True if any entries were processed (shipped, skipped,
        or dropped), False if the outbox was empty. The Task 15 run
        loop uses False to flip the worker state back to IDLE.

        Batch size respects ``outbox.peek_blobs`` default (10) —
        blobs are multi-MB uploads, so we ship slower than the DB
        pipeline by design.

        Errors from ``s3_client.head`` or ``s3_client.put`` propagate.
        The run loop catches them and transitions to BACKOFF via
        ``_on_ship_failed``. The failing entry stays in the outbox for
        retry; earlier entries in the same batch that already shipped
        successfully are not rolled back (their outbox entries were
        already deleted per-iteration).

        Connection + client are passed in so this is unit-testable in
        isolation; the run loop (Task 15) will own the live SQLite
        connection and the S3Client instance and pass them in.
        """
        entries = outbox.peek_blobs(sqlite_conn, limit=10)
        if not entries:
            return False

        for entry in entries:
            # Cheap dead-source check first — saves a network round-trip
            # if the operator cleared the file between enqueue and ship.
            if not os.path.exists(entry["source_path"]):
                log.info(
                    "backup files: source %r missing — "
                    "dropping outbox entry id=%s",
                    entry["source_path"], entry["id"],
                )
                outbox.delete_blobs(sqlite_conn, ids=[entry["id"]])
                continue

            # Route to the right bucket. Unknown prefix is schema drift
            # — log + drop so the queue isn't permanently blocked.
            try:
                bucket_suffix = _bucket_suffix_for_key(entry["target_key"])
            except ValueError as exc:
                log.warning(
                    "backup files: %s — dropping outbox entry id=%s",
                    exc, entry["id"],
                )
                outbox.delete_blobs(sqlite_conn, ids=[entry["id"]])
                continue

            # Idempotency: if the blob is already on S3 (crash-resume
            # mid-batch), skip the upload but still drop the outbox
            # entry. HEAD errors (auth, network) propagate — caller
            # decides retry strategy.
            if s3_client.head(
                bucket_suffix=bucket_suffix, key=entry["target_key"],
            ):
                outbox.delete_blobs(sqlite_conn, ids=[entry["id"]])
                continue

            # Ship. Errors propagate — outbox entry stays in place for
            # retry on the next drain cycle.
            s3_client.put(
                bucket_suffix=bucket_suffix,
                key=entry["target_key"],
                source_path=entry["source_path"],
                sha256=entry["sha256"],
            )
            outbox.delete_blobs(sqlite_conn, ids=[entry["id"]])

        return True

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
            self._on_ship_started()
            self.last_attempt_at = datetime.utcnow()
            self._publish_status()  # transition into DRAINING
            try:
                shipped_any = self._drain_one_batch()
                self.last_success_at = datetime.utcnow()
                self._on_ship_succeeded()
                if not shipped_any:
                    self._on_queue_empty()
                # One publish covers ship_succeeded + (optional)
                # queue_empty — the UI cares about the final state
                # of this tick, not the intermediate DRAINING flicker.
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
        dispatch by pipeline.

        The connection is short-lived (one drain). Re-opening it every
        tick keeps WAL locks from piling up under concurrent writers
        (sensor loop, grow handlers, route POSTs all want their own
        write locks).

        The client is also fresh each tick so a hot-reload (Task 16)
        that changed endpoint/credentials takes effect on the next
        drain without bouncing the thread.

        Returns the drain function's bool ``did_work`` value (True if
        any rows/blobs were shipped, False if the outbox was empty).
        """
        client = self._build_client()
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            if self.pipeline == "db":
                return self._drain_db_batch(conn, client)
            if self.pipeline == "files":
                return self._drain_files_batch(conn, client)
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
        field so multi-Pi deployments can distinguish their data."""
        return "pi-1"
