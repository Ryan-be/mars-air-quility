"""Phase 2 smoke test — exercise multiple writers and verify the outbox
sees them all.

Acts as a Phase 2 completion check: if any wired writer drops its
enqueue silently in a future refactor, this catches it without
requiring you to know which test file owns that specific writer.
"""
import sqlite3
import tempfile
import gc
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pytest


@pytest.fixture
def db_path(monkeypatch):
    """Fresh DB + monkeypatched DB_FILE references so every writer
    hits this temp file."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    original = init_db.DB_FILE
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("database.db_logger.DB_FILE", tmp.name)
    monkeypatch.setattr("mlss_monitor.grow.handlers.DB_FILE", tmp.name)
    init_db.create_db()

    # Seed a grow_unit so grow handlers don't refuse on FK validation
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


def test_phase2_smoke_multiple_writers_populate_outbox(db_path):
    """Exercise representative writers from each layer and verify the
    outbox sees every expected table.

    Acts as the Phase 2 completion gate: if this fails, a writer
    slipped through unrefactored or a future change accidentally
    skipped its outbox enqueue.
    """
    from database.db_logger import log_sensor_data, log_weather, save_inference
    from mlss_monitor.grow.handlers import handle_telemetry, handle_capabilities, handle_event

    # 1. db_logger writers
    log_sensor_data(22.0, 45.0, 400, 20)
    log_weather(temp=15.0, humidity=70.0, feels_like=14.0,
                wind_speed=5.0, weather_code=801, uv_index=2.0)
    save_inference(
        event_type="temp_high",
        severity="warning",
        title="Temperature high",
        description="Smoke test inference",
        action="Increase ventilation",
        evidence="{}",
        confidence=0.9,
    )

    # 2. grow handler writers
    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    handle_telemetry(unit_id=1, ts=ts, payload={
        "soil_moisture_raw": 500, "light_state": 0, "pump_state": 0,
    })
    handle_capabilities(unit_id=1, ts=ts, payload={
        "capabilities": [
            {"channel": "pump", "hardware": "gpio", "is_required": 1, "health": "untested"},
        ],
        "firmware_version": "1.0.0",
    })
    handle_event(unit_id=1, ts=ts + timedelta(seconds=10), payload={
        "kind": "watering_pulse",
        "details": {"trigger": "pid", "duration_s": 3.0,
                    "soil_pct_before": 32.0, "triggered_by": "system"},
    })

    conn = sqlite3.connect(db_path)
    try:
        seen_tables = {row[0] for row in conn.execute(
            "SELECT DISTINCT table_name FROM outbox_changes")}
        delete_scope_tables = {row[0] for row in conn.execute(
            "SELECT DISTINCT table_name FROM outbox_delete_scope")}
    finally:
        conn.close()

    expected_tables = {
        "sensor_data", "weather_log", "inferences",
        "grow_telemetry", "grow_units", "grow_unit_capabilities",
        "grow_watering_events",
    }
    missing = expected_tables - seen_tables
    assert not missing, (
        f"Phase 2 smoke: expected {expected_tables} in outbox_changes; "
        f"missing: {missing}; saw: {seen_tables}"
    )

    # handle_capabilities is strict-mirror — expect a delete_scope marker
    assert "grow_unit_capabilities" in delete_scope_tables, (
        f"Phase 2 smoke: expected grow_unit_capabilities delete_scope marker; "
        f"saw delete_scope tables: {delete_scope_tables}"
    )


def test_phase2_smoke_strict_mirror_delete_scopes_recorded(db_path):
    """Exercise strict-mirror writers and verify delete-scope markers."""
    from mlss_monitor.grow.handlers import handle_capabilities
    from mlss_monitor.incident_grouper import regroup_all

    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    handle_capabilities(unit_id=1, ts=ts, payload={
        "capabilities": [
            {"channel": "pump", "hardware": "gpio", "is_required": 1, "health": "untested"},
        ],
    })

    # regroup_all on an empty inferences table is a valid no-op: it still
    # enqueues delete_scope markers for the three incident tables.
    regroup_all(db_path)

    conn = sqlite3.connect(db_path)
    try:
        scope_rows = list(conn.execute(
            "SELECT table_name, scope_json FROM outbox_delete_scope ORDER BY id"))
    finally:
        conn.close()

    scope_table_names = {r[0] for r in scope_rows}
    expected = {
        "grow_unit_capabilities",
        "incidents", "incident_alerts", "incident_signature_features",
    }
    missing = expected - scope_table_names
    assert not missing, (
        f"Phase 2 smoke (delete_scope): expected {expected}; "
        f"missing: {missing}; saw: {scope_table_names}"
    )
