"""grow handlers enqueue outbox entries for every replicated-table write."""
import sqlite3
import tempfile
import gc
import json
from pathlib import Path
from datetime import datetime, timezone
import pytest


@pytest.fixture
def db_path(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    original = init_db.DB_FILE
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.handlers.DB_FILE", tmp.name)
    init_db.create_db()
    now = datetime.utcnow()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, 'h', ?)",
        (now, now))
    conn.commit()
    conn.close()
    yield tmp.name
    init_db.DB_FILE = original
    gc.collect()
    Path(tmp.name).unlink(missing_ok=True)


def _outbox_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return list(conn.execute(
            "SELECT table_name, pk FROM outbox_changes ORDER BY id"))
    finally:
        conn.close()


def _delete_scope_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return list(conn.execute(
            "SELECT table_name, scope_json FROM outbox_delete_scope ORDER BY id"))
    finally:
        conn.close()


def test_handle_telemetry_enqueues_telemetry_and_unit(db_path):
    from mlss_monitor.grow.handlers import handle_telemetry
    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    telemetry_id = handle_telemetry(unit_id=1, ts=ts, payload={
        "soil_moisture_raw": 500, "light_state": 0, "pump_state": 0,
    })
    rows = _outbox_rows(db_path)
    assert ("grow_telemetry", str(telemetry_id)) in rows
    assert ("grow_units", "1") in rows


def test_handle_telemetry_with_optional_fields_does_not_double_enqueue_unit(db_path):
    """Repeated UPDATE on grow_units within one transaction should coalesce
    via ON CONFLICT — outbox stores exactly one entry per (table, pk)."""
    from mlss_monitor.grow.handlers import handle_telemetry
    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    handle_telemetry(unit_id=1, ts=ts, payload={
        "soil_moisture_raw": 500, "light_state": 0, "pump_state": 0,
        "uptime_s": 1234, "buffer_size": 10,
    })
    rows = _outbox_rows(db_path)
    unit_entries = [r for r in rows if r[0] == "grow_units"]
    assert len(unit_entries) == 1


def test_handle_telemetry_pump_state_promotes_capability(db_path):
    """A telemetry frame with pump_state=1 should promote the pump capability
    AND enqueue grow_unit_capabilities in the outbox."""
    from mlss_monitor.grow.handlers import handle_telemetry
    # Pre-seed a pump capability row so _promote_capability_health can UPDATE it
    conn = sqlite3.connect(db_path)
    ts_init = datetime.utcnow()
    conn.execute(
        "INSERT INTO grow_unit_capabilities "
        "(unit_id, channel, hardware, is_required, installed_at, health) "
        "VALUES (1, 'pump', 'gpio', 1, ?, 'untested')",
        (ts_init,))
    conn.commit()
    conn.close()
    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    handle_telemetry(unit_id=1, ts=ts, payload={
        "soil_moisture_raw": 0, "light_state": 0, "pump_state": 1,
    })
    rows = _outbox_rows(db_path)
    assert ("grow_unit_capabilities", "1:pump") in rows


def test_handle_telemetry_no_op_promotion_does_not_enqueue(db_path):
    """If _promote_capability_health is a no-op (target health already set),
    we should NOT enqueue grow_unit_capabilities."""
    from mlss_monitor.grow.handlers import handle_telemetry
    conn = sqlite3.connect(db_path)
    ts_init = datetime.utcnow()
    conn.execute(
        "INSERT INTO grow_unit_capabilities "
        "(unit_id, channel, hardware, is_required, installed_at, health) "
        "VALUES (1, 'pump', 'gpio', 1, ?, 'connected')",
        (ts_init,))
    conn.commit()
    conn.close()
    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    handle_telemetry(unit_id=1, ts=ts, payload={
        "soil_moisture_raw": 0, "light_state": 0, "pump_state": 1,
    })
    rows = _outbox_rows(db_path)
    caps = [r for r in rows if r[0] == "grow_unit_capabilities"]
    assert caps == [], (
        "No-op promotion should not enqueue grow_unit_capabilities"
    )


def test_handle_capabilities_uses_delete_scope_and_enqueues_inserts(db_path):
    """handle_capabilities replaces the unit's capability set. It MUST
    enqueue a delete-scope for grow_unit_capabilities, then enqueue each
    new INSERTed row."""
    from mlss_monitor.grow.handlers import handle_capabilities
    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    handle_capabilities(unit_id=1, ts=ts, payload={
        "capabilities": [
            {"channel": "pump", "hardware": "gpio", "is_required": 1, "health": "untested"},
            {"channel": "light", "hardware": "gpio", "is_required": 1, "health": "untested"},
        ],
        "firmware_version": "1.0.0",
    })

    scopes = _delete_scope_rows(db_path)
    assert ("grow_unit_capabilities", json.dumps({"unit_id": 1}, sort_keys=True)) in scopes

    rows = _outbox_rows(db_path)
    assert ("grow_unit_capabilities", "1:pump") in rows
    assert ("grow_unit_capabilities", "1:light") in rows
    assert ("grow_units", "1") in rows


def test_handle_event_watering_enqueues_watering_and_unit(db_path):
    from mlss_monitor.grow.handlers import handle_event
    # Pre-seed pump capability so promote works (and exercises caps enqueue)
    conn = sqlite3.connect(db_path)
    ts_init = datetime.utcnow()
    conn.execute(
        "INSERT INTO grow_unit_capabilities "
        "(unit_id, channel, hardware, is_required, installed_at, health) "
        "VALUES (1, 'pump', 'gpio', 1, ?, 'untested')",
        (ts_init,))
    conn.commit()
    conn.close()
    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    handle_event(unit_id=1, ts=ts, payload={
        "kind": "watering_pulse",
        "details": {"trigger": "pid", "duration_s": 3.0,
                    "soil_pct_before": 32.0, "triggered_by": "system"},
    })
    rows = _outbox_rows(db_path)
    # grow_watering_events autoincrement id — just check the table is present
    tables = {t for t, _ in rows}
    assert "grow_watering_events" in tables
    assert "grow_units" in tables
    # Pump promoted -> caps enqueued
    assert "grow_unit_capabilities" in tables


def test_handle_event_sensor_degraded_enqueues_error_and_unit(db_path):
    from mlss_monitor.grow.handlers import handle_event
    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    handle_event(unit_id=1, ts=ts, payload={
        "kind": "sensor_degraded",
        "details": {"sensor": "soil_moisture", "reason": "out_of_range"},
    })
    rows = _outbox_rows(db_path)
    tables = {t for t, _ in rows}
    assert "grow_errors" in tables
    assert "grow_units" in tables


def test_handle_event_sensor_recovered_enqueues_unit_at_minimum(db_path):
    """sensor_recovered does an UPDATE on grow_errors (closes open degraded
    rows). The UPDATE may affect 0, 1, or many rows; for simplicity we only
    require grow_units to be enqueued. If the implementer chooses to also
    enqueue every affected grow_errors row, that's allowed."""
    from mlss_monitor.grow.handlers import handle_event
    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    handle_event(unit_id=1, ts=ts, payload={
        "kind": "sensor_recovered",
        "details": {"sensor": "soil_moisture"},
    })
    rows = _outbox_rows(db_path)
    tables = {t for t, _ in rows}
    assert "grow_units" in tables


def test_handle_event_safety_cap_hit_enqueues_error_and_unit(db_path):
    from mlss_monitor.grow.handlers import handle_event
    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    handle_event(unit_id=1, ts=ts, payload={
        "kind": "safety_cap_hit",
        "details": {"cap": "max_pulses_per_hour"},
    })
    rows = _outbox_rows(db_path)
    tables = {t for t, _ in rows}
    assert "grow_errors" in tables
    assert "grow_units" in tables


def test_handle_event_buffer_eviction_enqueues_error_and_unit(db_path):
    from mlss_monitor.grow.handlers import handle_event
    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    handle_event(unit_id=1, ts=ts, payload={
        "kind": "buffer_eviction",
        "details": {"reason": "row_cap", "evicted_count": 100},
    })
    rows = _outbox_rows(db_path)
    tables = {t for t, _ in rows}
    assert "grow_errors" in tables
    assert "grow_units" in tables


def test_handle_event_unknown_kind_only_enqueues_unit(db_path):
    """Unknown event kinds are log-only — no grow_errors row. But
    grow_units.last_seen_at is still bumped, so that table is enqueued."""
    from mlss_monitor.grow.handlers import handle_event
    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    handle_event(unit_id=1, ts=ts, payload={
        "kind": "identify_complete",
        "details": {},
    })
    rows = _outbox_rows(db_path)
    tables = {t for t, _ in rows}
    assert "grow_units" in tables
    # No grow_errors entry expected
    assert "grow_errors" not in tables
