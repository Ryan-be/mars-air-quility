"""Tests for the incidents REST API."""
import json
import sys
from unittest.mock import MagicMock

for _mod in ["board", "busio", "adafruit_ahtx0", "adafruit_sgp30",
             "mics6814", "authlib", "authlib.integrations",
             "authlib.integrations.flask_client"]:
    sys.modules.setdefault(_mod, MagicMock())

import sqlite3
import pytest
import database.init_db as dbi
import database.db_logger as dbl
import database.user_db as udb


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    import mlss_monitor.hot_tier as ht
    dbi.DB_FILE = db_path
    dbl.DB_FILE = db_path
    udb.DB_FILE = db_path
    ht.DB_FILE = db_path
    dbi.create_db()
    yield db_path
    dbi.DB_FILE = "data/sensor_data.db"
    dbl.DB_FILE = "data/sensor_data.db"
    udb.DB_FILE = "data/sensor_data.db"
    import mlss_monitor.hot_tier as ht2
    ht2.DB_FILE = "data/sensor_data.db"


@pytest.fixture
def client(db, monkeypatch):
    import mlss_monitor.app as app_module
    import mlss_monitor.state as state
    monkeypatch.setattr(app_module, "LOG_INTERVAL", 99999)
    mock_plug = MagicMock()
    monkeypatch.setattr(state, "fan_smart_plug", mock_plug)
    # Patch api_incidents DB_FILE to use the temp DB
    import mlss_monitor.routes.api_incidents as api_inc
    monkeypatch.setattr(api_inc, "DB_FILE", db)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user"] = "test-admin"
            sess["user_role"] = "admin"
            sess["user_id"] = None
        yield c


def _seed_incident(db_path, incident_id="INC-20260419-1200",
                   started_at=None,
                   ended_at=None,
                   max_severity="warning"):
    from datetime import datetime, timedelta
    if started_at is None:
        started_at = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    if ended_at is None:
        ended_at = (datetime.utcnow() - timedelta(minutes=50)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    sig = json.dumps([0.0] * 32)
    conn.execute(
        "INSERT INTO incidents (id, started_at, ended_at, max_severity, confidence, title, signature) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (incident_id, started_at, ended_at, max_severity, 0.8, f"Test {incident_id}", sig)
    )
    conn.commit()
    conn.close()


def test_get_incidents_empty(client):
    rv = client.get("/api/incidents")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["incidents"] == []


def test_get_incidents_returns_list(client, db):
    _seed_incident(db)
    rv = client.get("/api/incidents")
    assert rv.status_code == 200
    data = rv.get_json()
    assert len(data["incidents"]) == 1
    assert data["incidents"][0]["id"] == "INC-20260419-1200"


def test_get_incidents_severity_filter(client, db):
    from datetime import datetime, timedelta
    t1 = (datetime.utcnow() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    t1_end = (datetime.utcnow() - timedelta(hours=1, minutes=50)).strftime("%Y-%m-%d %H:%M:%S")
    t2 = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    t2_end = (datetime.utcnow() - timedelta(minutes=50)).strftime("%Y-%m-%d %H:%M:%S")
    _seed_incident(db, "INC-20260419-1200", started_at=t1, ended_at=t1_end, max_severity="info")
    _seed_incident(db, "INC-20260419-1300",
                   started_at=t2,
                   ended_at=t2_end,
                   max_severity="critical")
    rv = client.get("/api/incidents?severity=critical")
    data = rv.get_json()
    assert len(data["incidents"]) == 1
    assert data["incidents"][0]["max_severity"] == "critical"


def test_get_incidents_includes_alert_count(client, db):
    _seed_incident(db)
    rv = client.get("/api/incidents")
    data = rv.get_json()
    assert "alert_count" in data["incidents"][0]


def test_get_incidents_search_filter(client, db):
    _seed_incident(db, "INC-20260419-1200")
    rv = client.get("/api/incidents?q=INC-20260419-1200")
    data = rv.get_json()
    assert len(data["incidents"]) == 1


def test_get_incident_detail_not_found(client):
    rv = client.get("/api/incidents/INC-MISSING")
    assert rv.status_code == 404


def test_get_incident_detail_returns_fields(client, db):
    _seed_incident(db)
    rv = client.get("/api/incidents/INC-20260419-1200")
    assert rv.status_code == 200
    data = rv.get_json()
    assert "id" in data
    assert "alerts" in data
    assert "narrative" in data
    assert "similar" in data
    assert "causal_sequence" in data


def test_get_incident_alert_not_found(client, db):
    _seed_incident(db)
    rv = client.get("/api/incidents/INC-20260419-1200/alert/9999")
    assert rv.status_code == 404


@pytest.fixture
def seed_three_incidents(db):
    """Insert 1 critical, 1 warning, 1 info incident within the last 24h."""
    from datetime import datetime, timedelta
    conn = sqlite3.connect(db)
    now = datetime.utcnow()
    rows = [
        ("INC-A", "critical"),
        ("INC-B", "warning"),
        ("INC-C", "info"),
    ]
    for inc_id, sev in rows:
        conn.execute(
            "INSERT INTO incidents (id, started_at, ended_at, max_severity, "
            "confidence, title, signature) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (inc_id, (now - timedelta(hours=1)).isoformat(sep=" "),
             now.isoformat(sep=" "), sev, 0.9, f"Test {inc_id}", json.dumps([0.0] * 32)),
        )
    conn.commit()
    conn.close()
    return rows


def test_list_incidents_includes_severity_counts(client, seed_three_incidents):
    """GET /api/incidents returns a counts dict alongside the incidents array."""
    resp = client.get("/api/incidents?window=30d")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "counts" in data
    counts = data["counts"]
    assert set(counts.keys()) >= {"critical", "warning", "info"}
    assert counts["critical"] + counts["warning"] + counts["info"] == data["total"]
