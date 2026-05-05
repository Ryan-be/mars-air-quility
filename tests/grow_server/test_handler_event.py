import sqlite3
import tempfile
from datetime import datetime
import pytest


@pytest.fixture
def db_with_unit(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
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
