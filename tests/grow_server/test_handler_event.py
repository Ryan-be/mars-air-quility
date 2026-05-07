import sqlite3
import tempfile
from datetime import datetime
import pytest


@pytest.fixture
def db_with_unit(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.handlers.DB_FILE", tmp.name)
    init_db.create_db()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'hw1', 'X', ?, 'h', ?)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.commit()
    conn.close()
    return tmp.name


def test_watering_pulse_event_writes_to_grow_watering_events(db_with_unit):
    from mlss_monitor.grow.handlers import handle_event
    handle_event(unit_id=1, ts=datetime(2026, 5, 3, 12, 0, 0), payload={
        "kind": "watering_pulse",
        "details": {"duration_s": 5.2, "trigger": "pid",
                    "soil_pct_before": 42, "pid_error": 13,
                    "pid_p_term": 5.2, "pid_i_term": 0, "pid_d_term": 0,
                    "triggered_by": "system"},
    })
    conn = sqlite3.connect(db_with_unit)
    row = conn.execute(
        "SELECT trigger, duration_s, soil_pct_before, triggered_by "
        "FROM grow_watering_events WHERE unit_id=1"
    ).fetchone()
    assert row == ("pid", 5.2, 42.0, "system")


def test_sensor_degraded_event_writes_to_grow_errors(db_with_unit):
    from mlss_monitor.grow.handlers import handle_event
    handle_event(unit_id=1, ts=datetime.utcnow(), payload={
        "kind": "sensor_degraded",
        "details": {"sensor": "Seesaw", "consecutive_bad_reads": 3},
    })
    conn = sqlite3.connect(db_with_unit)
    row = conn.execute(
        "SELECT severity, kind, message FROM grow_errors WHERE unit_id=1"
    ).fetchone()
    assert row[0] == "warning"
    assert row[1] == "sensor_degraded"
    assert "Seesaw" in row[2]


def test_sensor_recovered_resolves_open_sensor_errors(db_with_unit):
    from mlss_monitor.grow.handlers import handle_event
    handle_event(unit_id=1, ts=datetime.utcnow(), payload={
        "kind": "sensor_degraded", "details": {"sensor": "Seesaw"},
    })
    handle_event(unit_id=1, ts=datetime.utcnow(), payload={
        "kind": "sensor_recovered", "details": {"sensor": "Seesaw"},
    })
    conn = sqlite3.connect(db_with_unit)
    n_open = conn.execute(
        "SELECT COUNT(*) FROM grow_errors "
        "WHERE unit_id=1 AND kind='sensor_degraded' AND resolved_at IS NULL"
    ).fetchone()[0]
    assert n_open == 0


def test_sensor_recovered_with_special_chars_resolves_correctly(db_with_unit):
    """Sensor names containing % or _ would have caused LIKE wildcard issues."""
    from mlss_monitor.grow.handlers import handle_event
    handle_event(unit_id=1, ts=datetime.utcnow(), payload={
        "kind": "sensor_degraded", "details": {"sensor": "100%_humidity"},
    })
    handle_event(unit_id=1, ts=datetime.utcnow(), payload={
        "kind": "sensor_recovered", "details": {"sensor": "100%_humidity"},
    })
    conn = sqlite3.connect(db_with_unit)
    n_open = conn.execute(
        "SELECT COUNT(*) FROM grow_errors "
        "WHERE unit_id=1 AND kind='sensor_degraded' AND resolved_at IS NULL"
    ).fetchone()[0]
    assert n_open == 0


def test_watering_pulse_promotes_pump_capability_to_connected(db_with_unit):
    """Phase 2 sense-only-mode: a completed watering_pulse is the strongest
    evidence the pump works (the firmware only emits this AFTER the
    actuation completes). Promote pump capability to "connected" so a
    previously-untested or unresponsive flag clears immediately."""
    from mlss_monitor.grow.handlers import handle_event
    # Seed pump capability with no_hardware (simulate the boot-time
    # outcome before the user wires up the PSU). C1 schema cleanup:
    # health is now a typed column.
    conn = sqlite3.connect(db_with_unit)
    conn.execute(
        "INSERT INTO grow_unit_capabilities "
        "(unit_id, channel, hardware, is_required, unit_label, "
        " installed_at, health) "
        "VALUES (1, 'pump', 'automation_phat', 0, 'bool', ?, 'no_hardware')",
        (datetime.utcnow(),),
    )
    conn.commit()
    conn.close()

    handle_event(unit_id=1, ts=datetime.utcnow(), payload={
        "kind": "watering_pulse",
        "details": {"duration_s": 5, "trigger": "manual",
                    "triggered_by": "user"},
    })
    conn = sqlite3.connect(db_with_unit)
    health = conn.execute(
        "SELECT health FROM grow_unit_capabilities "
        "WHERE unit_id=1 AND channel='pump'"
    ).fetchone()[0]
    assert health == "connected"


def test_sensor_recovered_only_resolves_matching_sensor(db_with_unit):
    """Two different sensors degraded; recover one — the other stays open."""
    from mlss_monitor.grow.handlers import handle_event
    handle_event(unit_id=1, ts=datetime.utcnow(), payload={
        "kind": "sensor_degraded", "details": {"sensor": "Seesaw"},
    })
    handle_event(unit_id=1, ts=datetime.utcnow(), payload={
        "kind": "sensor_degraded", "details": {"sensor": "TSL2591"},
    })
    handle_event(unit_id=1, ts=datetime.utcnow(), payload={
        "kind": "sensor_recovered", "details": {"sensor": "Seesaw"},
    })
    conn = sqlite3.connect(db_with_unit)
    rows = conn.execute(
        "SELECT subject_sensor, resolved_at FROM grow_errors "
        "WHERE unit_id=1 AND kind='sensor_degraded' "
        "ORDER BY subject_sensor"
    ).fetchall()
    # Seesaw resolved; TSL2591 still open
    by_sensor = {r[0]: r[1] for r in rows}
    assert by_sensor["Seesaw"] is not None
    assert by_sensor["TSL2591"] is None
