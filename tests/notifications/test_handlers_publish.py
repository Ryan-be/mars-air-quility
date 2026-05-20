"""Test that grow handlers publish grow_error_logged on each INSERT."""

import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from database.init_db import create_db


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("MLSS_DB_FILE", str(db_path))
    from config import config as _config
    _config.reload()
    monkeypatch.setattr("database.init_db.DB_FILE", str(db_path))
    monkeypatch.setattr("mlss_monitor.grow.handlers.DB_FILE", str(db_path))
    create_db()
    # Insert a unit to satisfy FK constraints. Mirror the column set used in
    # tests/grow_server/test_handler_event.py (hardware_serial is UNIQUE NOT
    # NULL; phase_set_at is NOT NULL).
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "INSERT INTO grow_units (hardware_serial, label, bearer_token_hash, "
        "                         enrolled_at, phase_set_at) "
        "VALUES ('hw-test', 'test', 'h', "
        "        '2026-05-20T10:00:00Z', '2026-05-20T10:00:00Z')"
    )
    unit_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"db": str(db_path), "unit_id": unit_id}


def _stub_bus(monkeypatch):
    """Replace state.event_bus with a recording mock."""
    bus = MagicMock()
    bus.publish = MagicMock()
    monkeypatch.setattr("mlss_monitor.state.event_bus", bus, raising=False)
    return bus


def test_sensor_degraded_publishes_grow_error_logged(env, monkeypatch):
    bus = _stub_bus(monkeypatch)
    from mlss_monitor.grow import handlers
    handlers.handle_event(env["unit_id"], datetime.now(timezone.utc), {
        "kind": "sensor_degraded",
        "details": {"sensor": "soil_temp"},
    })
    pubs = [c for c in bus.publish.call_args_list
            if c.args and c.args[0] == "grow_error_logged"]
    assert len(pubs) == 1, "exactly one grow_error_logged"
    data = pubs[0].args[1]
    assert data["unit_id"] == env["unit_id"]
    assert data["severity"] == "warning"
    assert "soil_temp" in (data.get("title", "") + data.get("message", ""))


def test_safety_cap_hit_publishes_grow_error_logged(env, monkeypatch):
    bus = _stub_bus(monkeypatch)
    from mlss_monitor.grow import handlers
    handlers.handle_event(env["unit_id"], datetime.now(timezone.utc), {
        "kind": "safety_cap_hit",
        "details": {"cap": "pump_on_time_max"},
    })
    pubs = [c for c in bus.publish.call_args_list
            if c.args and c.args[0] == "grow_error_logged"]
    assert len(pubs) == 1
    assert pubs[0].args[1]["severity"] == "warning"


def test_watering_pulse_does_NOT_publish(env, monkeypatch):
    # Watering pulses are routine, not errors — should not generate notifications.
    bus = _stub_bus(monkeypatch)
    from mlss_monitor.grow import handlers
    handlers.handle_event(env["unit_id"], datetime.now(timezone.utc), {
        "kind": "watering_pulse",
        "details": {"duration_s": 2.5, "soil_pct_before": 35.0},
    })
    pubs = [c for c in bus.publish.call_args_list
            if c.args and c.args[0] == "grow_error_logged"]
    assert len(pubs) == 0
