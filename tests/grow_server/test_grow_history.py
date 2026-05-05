"""GET /api/grow/units/<id>/history?range=24h returns moisture series + watering events."""
import sqlite3
import tempfile
from datetime import datetime, timedelta
import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.routes.api_grow_history.DB_FILE", tmp.name)
    init_db.create_db()
    now = datetime.utcnow()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, 'h', ?)",
        (now, now),
    )
    # 3 telemetry rows + 1 watering event
    for hours_ago, raw, pct in [(3, 612, 31), (2, 800, 46), (1, 1100, 70)]:
        conn.execute(
            "INSERT INTO grow_telemetry (unit_id, timestamp_utc, "
            "soil_moisture_raw, soil_moisture_pct, light_state, pump_state) "
            "VALUES (1, ?, ?, ?, 1, 0)",
            (now - timedelta(hours=hours_ago), raw, pct),
        )
    conn.execute(
        "INSERT INTO grow_watering_events (unit_id, timestamp_utc, trigger, "
        "duration_s, soil_pct_before) VALUES (1, ?, 'pid', 6.0, 31)",
        (now - timedelta(hours=2, minutes=55),),
    )
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_history import api_grow_history_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_history_bp)
    return app.test_client()


def test_history_returns_moisture_and_events(client):
    r = client.get("/api/grow/units/1/history?range=24h")
    assert r.status_code == 200
    body = r.get_json()
    assert "moisture" in body
    assert "watering_events" in body
    assert len(body["moisture"]) == 3
    assert len(body["watering_events"]) == 1
    assert body["watering_events"][0]["duration_s"] == 6.0


def test_history_supports_range_param(client):
    """range=7d or range=30d should also be accepted."""
    r = client.get("/api/grow/units/1/history?range=7d")
    assert r.status_code == 200
    r = client.get("/api/grow/units/1/history?range=30d")
    assert r.status_code == 200


def test_history_invalid_range_400(client):
    r = client.get("/api/grow/units/1/history?range=bogus")
    assert r.status_code == 400
