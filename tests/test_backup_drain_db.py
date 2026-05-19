"""_drain.drain_db_batch — drains outbox to Postgres.

Uses a real SQLite tempfile (matches Phase 2 fixtures so the live
@tee_to_outbox writers wire up correctly) + a mocked PostgresClient
(no actual Postgres needed; the live integration test wires a real
instance in Phase 6).

Covered:
  - parse_pk helper (single + composite + colons-in-text-pk)
  - empty outbox → no-op
  - happy path: one table, multiple rows, multiple tables
  - delete_scope queue processed BEFORE row pointers in the same batch
  - composite PK round-tripping (grow_unit_capabilities)
  - missing live row → log + drop outbox entry (no ship)
  - unknown table → log + drop (schema drift guard)
  - errors propagate (run loop catches and goes to BACKOFF)
  - delete_scope-only batch returns True (work was done)
  - per-table failure semantics — succeeded table's outbox entries
    are deleted+committed even when a later table errors

The ``db_path`` fixture is provided by ``tests/conftest.py``.
"""
import sqlite3
from datetime import datetime
from unittest.mock import MagicMock
import pytest

from mlss_monitor.backup import outbox
from mlss_monitor.backup._drain import drain_db_batch
from mlss_monitor.backup.replicated_tables import parse_pk


@pytest.fixture
def pg_client():
    return MagicMock()


# ── PK parsing ────────────────────────────────────────────────────

def test_parse_pk_single_int():
    """Most replicated tables (sensor_data, weather_log, grow_telemetry,
    …) have INTEGER autoincrement PK. The outbox stores pk as TEXT, so
    "42" must parse back to int(42)."""
    assert parse_pk("42", [int]) == (42,)


def test_parse_pk_single_str():
    """incidents has TEXT PK (id like "INC-2026-05-18T12:00:00"). The
    pk_str passes through unchanged, just wrapped in a tuple."""
    assert parse_pk("INC-2026-05-18T12:00:00", [str]) == ("INC-2026-05-18T12:00:00",)


def test_parse_pk_composite_int_str():
    """grow_unit_capabilities: (int unit_id, str channel). Format
    used by handle_capabilities is f"{unit_id}:{channel}" e.g.
    "3:pump" → (3, "pump")."""
    assert parse_pk("3:pump", [int, str]) == (3, "pump")


def test_parse_pk_composite_str_int_with_colons_in_str():
    """incident_signature_features: (str incident_id, int feature_idx).
    The incident_id contains colons (ISO 8601 timestamp). rsplit must
    use the RIGHTMOST colon as the delimiter so feature_idx parses
    cleanly while the timestamp's internal colons stay intact."""
    assert parse_pk("INC-2026-05-18T12:00:00:5", [str, int]) == (
        "INC-2026-05-18T12:00:00", 5,
    )


def test_parse_pk_composite_str_int_for_alerts():
    """incident_alerts: (str incident_id, int alert_id) — same shape
    as signature_features, different secondary column meaning."""
    assert parse_pk("INC-2026-05-18T12:00:00:42", [str, int]) == (
        "INC-2026-05-18T12:00:00", 42,
    )


# ── Drain function: empty / basic shipping ────────────────────────

def test_drain_db_batch_empty_outbox_returns_false(db_path, pg_client):
    """No pending entries → no work, no PostgresClient calls. The run
    loop (Task 15) uses this False to flip the state back to IDLE."""
    with sqlite3.connect(db_path) as conn:
        result = drain_db_batch(conn, pg_client)
    assert result is False
    pg_client.upsert_rows.assert_not_called()
    pg_client.delete_scope.assert_not_called()


def test_drain_db_batch_ships_one_table(db_path, pg_client):
    """One INSERT (via the live @tee_to_outbox writer) creates one
    outbox entry; one drain call ships it via upsert_rows and drains
    the outbox."""
    from database.db_logger import log_sensor_data
    pk = log_sensor_data(22.0, 45.0, 400, 20)

    with sqlite3.connect(db_path) as conn:
        result = drain_db_batch(conn, pg_client)

    assert result is True
    pg_client.upsert_rows.assert_called_once()
    call = pg_client.upsert_rows.call_args
    assert call.kwargs["table"] == "sensor_data"
    assert call.kwargs["pk_columns"] == ["id"]
    assert len(call.kwargs["rows"]) == 1
    assert call.kwargs["rows"][0]["id"] == pk

    # Outbox drained after successful ship.
    with sqlite3.connect(db_path) as conn:
        assert outbox.pending_count_rows(conn) == 0


def test_drain_db_batch_groups_multiple_rows_per_table(db_path, pg_client):
    """Three rows in the same table → one upsert_rows call with 3 rows.
    Grouping per-table minimises Postgres round trips."""
    from database.db_logger import log_sensor_data
    log_sensor_data(22.0, 45.0, 400, 20)
    log_sensor_data(22.5, 46.0, 410, 22)
    log_sensor_data(23.0, 47.0, 420, 24)

    with sqlite3.connect(db_path) as conn:
        drain_db_batch(conn, pg_client)

    pg_client.upsert_rows.assert_called_once()
    call = pg_client.upsert_rows.call_args
    assert call.kwargs["table"] == "sensor_data"
    assert len(call.kwargs["rows"]) == 3


def test_drain_db_batch_splits_per_table(db_path, pg_client):
    """Two tables → two upsert_rows calls, one per table."""
    from database.db_logger import log_sensor_data, log_weather
    log_sensor_data(22.0, 45.0, 400, 20)
    log_weather(
        temp=15.0, humidity=70.0, feels_like=14.0,
        wind_speed=5.0, weather_code=801, uv_index=2.0,
    )

    with sqlite3.connect(db_path) as conn:
        drain_db_batch(conn, pg_client)

    assert pg_client.upsert_rows.call_count == 2
    tables_shipped = {
        call.kwargs["table"]
        for call in pg_client.upsert_rows.call_args_list
    }
    assert tables_shipped == {"sensor_data", "weather_log"}


# ── Delete-scope ordering ─────────────────────────────────────────

def test_drain_db_batch_processes_delete_scope_before_rows(db_path, pg_client):
    """When both queues have entries, delete_scope is shipped FIRST so
    the server applies the wipe before the corresponding INSERTs land.
    Out-of-order shipping would let stale rows linger if the server
    crashed mid-batch."""
    from mlss_monitor.grow.handlers import handle_capabilities

    # Seed a grow_unit so handle_capabilities has a foreign-key target.
    conn = sqlite3.connect(db_path)
    now = datetime.utcnow()
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, 'h', ?)",
        (now, now),
    )
    conn.commit()
    conn.close()

    handle_capabilities(unit_id=1, ts=now, payload={
        "capabilities": [
            {"channel": "pump", "hardware": "gpio",
             "is_required": 1, "health": "untested"},
        ],
    })
    # After this: outbox has 1 delete_scope (grow_unit_capabilities) +
    # 2 row pointers (grow_units last_seen_at, grow_unit_capabilities
    # new row).

    call_log = []
    pg_client.delete_scope.side_effect = lambda **kw: call_log.append(
        ("delete_scope", kw["table"]),
    )
    pg_client.upsert_rows.side_effect = lambda **kw: call_log.append(
        ("upsert_rows", kw["table"]),
    )

    with sqlite3.connect(db_path) as conn:
        drain_db_batch(conn, pg_client)

    # delete_scope index must come before the first upsert_rows index.
    scope_idx = next(
        i for i, c in enumerate(call_log) if c[0] == "delete_scope"
    )
    first_upsert = next(
        i for i, c in enumerate(call_log) if c[0] == "upsert_rows"
    )
    assert scope_idx < first_upsert, (
        f"delete_scope must precede upsert_rows; got order: {call_log}"
    )


def test_drain_db_batch_returns_true_when_only_delete_scope_processed(db_path, pg_client):
    """Edge case: outbox has delete_scope entries but no row pointers.
    Should return True (work was done) so the run loop keeps cycling
    through DRAINING."""
    with sqlite3.connect(db_path) as conn:
        with conn:
            outbox.enqueue_delete_scope(conn, table="incidents", scope={})

    with sqlite3.connect(db_path) as conn:
        result = drain_db_batch(conn, pg_client)

    assert result is True
    pg_client.delete_scope.assert_called_once()
    pg_client.upsert_rows.assert_not_called()


# ── Composite PK round-trip ───────────────────────────────────────

def test_drain_db_batch_handles_composite_pk(db_path, pg_client):
    """grow_unit_capabilities has composite PK (unit_id, channel).
    The outbox stores pk as f"{unit_id}:{channel}" — the drain
    function must parse it back to (1, "pump") and pass
    pk_columns=["unit_id", "channel"] to PostgresClient.upsert_rows."""
    conn = sqlite3.connect(db_path)
    now = datetime.utcnow()
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, 'h', ?)",
        (now, now),
    )
    conn.commit()
    conn.close()

    from mlss_monitor.grow.handlers import handle_capabilities
    handle_capabilities(unit_id=1, ts=now, payload={
        "capabilities": [
            {"channel": "pump", "hardware": "gpio",
             "is_required": 1, "health": "untested"},
        ],
    })

    with sqlite3.connect(db_path) as conn:
        drain_db_batch(conn, pg_client)

    caps_call = next(
        c for c in pg_client.upsert_rows.call_args_list
        if c.kwargs["table"] == "grow_unit_capabilities"
    )
    assert caps_call.kwargs["pk_columns"] == ["unit_id", "channel"]
    assert caps_call.kwargs["rows"][0]["unit_id"] == 1
    assert caps_call.kwargs["rows"][0]["channel"] == "pump"


# ── Edge cases: orphans, schema drift ─────────────────────────────

def test_drain_db_batch_missing_live_row_drops_outbox_entry(db_path, pg_client):
    """If the live row was deleted between enqueue and ship, log + drop
    the outbox entry without shipping. This is normal for append-mostly
    tables: the operator wiped the Pi-side row (e.g. clear_photos), but
    the server keeps its copy because the append-mostly delete does NOT
    enqueue a delete_scope marker."""
    from database.db_logger import log_sensor_data
    pk = log_sensor_data(22.0, 45.0, 400, 20)

    # Manually delete the live row, leaving the outbox entry orphaned.
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM sensor_data WHERE id=?", (pk,))
    conn.commit()
    conn.close()

    with sqlite3.connect(db_path) as conn:
        drain_db_batch(conn, pg_client)

    # No upsert (no live row to ship).
    pg_client.upsert_rows.assert_not_called()
    # Orphan entry removed from the outbox so it doesn't block the
    # queue forever.
    with sqlite3.connect(db_path) as conn:
        assert outbox.pending_count_rows(conn) == 0


def test_drain_db_batch_unknown_table_logs_and_drops(db_path, pg_client):
    """If the outbox somehow has an entry for a table NOT in the
    canonical REPLICATED_TABLES dict (which can only happen if a
    table was removed from the canonical module faster than the
    outbox could drain — exceedingly unlikely), the drain function
    must NOT crash. Log + drop so the queue isn't permanently blocked."""
    with sqlite3.connect(db_path) as conn:
        with conn:
            outbox.enqueue_row(conn, table="future_table_xyz", pk=1)

    with sqlite3.connect(db_path) as conn:
        drain_db_batch(conn, pg_client)

    pg_client.upsert_rows.assert_not_called()
    with sqlite3.connect(db_path) as conn:
        assert outbox.pending_count_rows(conn) == 0


# ── Failure semantics ─────────────────────────────────────────────

def test_drain_db_batch_propagates_postgres_errors(db_path, pg_client):
    """Postgres errors during upsert_rows must propagate so the Task 15
    run loop can catch them and transition the worker to BACKOFF.
    Outbox entries MUST stay in place for retry — deleting on failure
    would drop data on the floor."""
    from database.db_logger import log_sensor_data
    log_sensor_data(22.0, 45.0, 400, 20)
    pg_client.upsert_rows.side_effect = Exception("connection refused")

    # Explicit close in finally — `with sqlite3.connect(...) as conn:`
    # only commits/rolls back on exit, it does NOT close the connection,
    # and an unclosed handle blocks the Windows teardown unlink.
    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(Exception, match="connection refused"):
            drain_db_batch(conn, pg_client)
    finally:
        conn.close()

    # Entry retained for retry on next drain cycle.
    conn = sqlite3.connect(db_path)
    try:
        assert outbox.pending_count_rows(conn) > 0
    finally:
        conn.close()


def test_drain_db_batch_propagates_delete_scope_errors(db_path, pg_client):
    """Same retry semantics for delete_scope — if the wipe fails, leave
    the outbox_delete_scope entry alone so the next cycle retries."""
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            outbox.enqueue_delete_scope(conn, table="incidents", scope={})
    finally:
        conn.close()
    pg_client.delete_scope.side_effect = Exception("connection refused")

    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(Exception, match="connection refused"):
            drain_db_batch(conn, pg_client)
    finally:
        conn.close()

    # delete_scope entry retained for retry.
    conn = sqlite3.connect(db_path)
    try:
        assert outbox.pending_count_delete_scope(conn) > 0
    finally:
        conn.close()


# ── Per-table failure semantics (Fix 2: partial-batch bookkeeping) ─

def test_drain_db_batch_per_table_failure_only_loses_failing_table(
    db_path, pg_client,
):
    """When one table's upsert succeeds but a later table's upsert
    raises, the SUCCEEDED table's outbox entries are deleted (it
    shipped — no need to re-ship next cycle) while the FAILED
    table's entries are retained for retry.

    This is the partial-batch bookkeeping fix: the old behaviour
    propagated the exception without deleting ANY outbox entries,
    so the succeeded table got re-shipped next cycle (idempotent
    on the server but wasteful).
    """
    from database.db_logger import log_sensor_data, log_weather
    log_sensor_data(22.0, 45.0, 400, 20)
    log_weather(
        temp=15.0, humidity=70.0, feels_like=14.0,
        wind_speed=5.0, weather_code=801, uv_index=2.0,
    )

    # weather_log raises; sensor_data succeeds. Dict iteration order
    # in Python 3.7+ is insertion order, and the outbox's by_table
    # ordering follows the enqueue order — so sensor_data ships
    # first and weather_log raises second.
    def _selective_failure(*, table, **_):
        if table == "weather_log":
            raise Exception("connection refused on weather_log")

    pg_client.upsert_rows.side_effect = _selective_failure

    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(Exception, match="connection refused"):
            drain_db_batch(conn, pg_client)
    finally:
        conn.close()

    # Verify per-table outbox state:
    #   sensor_data — shipped + deleted, no longer in outbox.
    #   weather_log — failed, still in outbox for retry.
    conn = sqlite3.connect(db_path)
    try:
        sensor_pending = conn.execute(
            "SELECT COUNT(*) FROM outbox_changes WHERE table_name='sensor_data'"
        ).fetchone()[0]
        weather_pending = conn.execute(
            "SELECT COUNT(*) FROM outbox_changes WHERE table_name='weather_log'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert sensor_pending == 0, (
        "sensor_data shipped successfully so its outbox entry should be deleted"
    )
    assert weather_pending == 1, (
        "weather_log failed so its outbox entry must be retained for retry"
    )


def test_drain_db_batch_per_scope_failure_only_loses_failing_scope(
    db_path, pg_client,
):
    """The same per-iteration commit boundary applies to delete_scope.
    First scope succeeds → outbox entry deleted. Second scope raises
    → its outbox entry retained for retry. (Without the per-iteration
    delete the second scope's failure would re-ship the first scope
    on the next cycle, which is wasteful but idempotent.)
    """
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            outbox.enqueue_delete_scope(conn, table="incidents", scope={})
            outbox.enqueue_delete_scope(
                conn, table="incident_alerts", scope={},
            )
    finally:
        conn.close()

    def _selective_failure(*, table, **_):
        if table == "incident_alerts":
            raise Exception("connection refused on incident_alerts")

    pg_client.delete_scope.side_effect = _selective_failure

    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(Exception, match="connection refused"):
            drain_db_batch(conn, pg_client)
    finally:
        conn.close()

    # incidents scope shipped + deleted; incident_alerts scope retained.
    conn = sqlite3.connect(db_path)
    try:
        incidents_pending = conn.execute(
            "SELECT COUNT(*) FROM outbox_delete_scope "
            "WHERE table_name='incidents'"
        ).fetchone()[0]
        alerts_pending = conn.execute(
            "SELECT COUNT(*) FROM outbox_delete_scope "
            "WHERE table_name='incident_alerts'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert incidents_pending == 0
    assert alerts_pending == 1
