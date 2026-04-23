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
    ht.DB_FILE = "data/sensor_data.db"


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


def test_list_incidents_includes_summary(client, seed_three_incidents):
    """List response includes top_sensors and hour_histogram summaries."""
    resp = client.get("/api/incidents?window=30d")
    data = resp.get_json()
    assert "summary" in data
    s = data["summary"]
    assert "top_sensors" in s and isinstance(s["top_sensors"], list)
    assert "hour_histogram" in s and isinstance(s["hour_histogram"], list)
    assert len(s["hour_histogram"]) == 24  # one bucket per hour of day


def test_list_incidents_rejects_unknown_window(client, db):
    """Unknown window values should 400 rather than silently ignore the filter."""
    rv = client.get("/api/incidents?window=forever")
    assert rv.status_code == 400
    body = rv.get_json()
    assert "error" in body
    assert "forever" in body["error"]


def test_list_incidents_alert_counts_single_query(client, seed_three_incidents, db):
    """Response alert_counts match the grouped query, not the old N+1 loop."""
    conn = sqlite3.connect(db)
    # Seed alerts: 2 for INC-A, 1 for INC-B, 0 for INC-C
    for inc_id, n in [("INC-A", 2), ("INC-B", 1)]:
        for i in range(n):
            cur = conn.execute(
                "INSERT INTO inferences (created_at, event_type, severity, title, "
                "description, confidence) VALUES (?, ?, ?, ?, ?, ?)",
                ("2026-04-23 09:00:00", "eco2_elevated", "warning", f"{inc_id}-a{i}",
                 "", 0.9),
            )
            conn.execute(
                "INSERT INTO incident_alerts (incident_id, alert_id, is_primary) "
                "VALUES (?, ?, 1)",
                (inc_id, cur.lastrowid),
            )
    conn.commit()
    conn.close()

    resp = client.get("/api/incidents?window=30d")
    data = resp.get_json()
    by_id = {inc["id"]: inc["alert_count"] for inc in data["incidents"]}
    assert by_id.get("INC-A") == 2
    assert by_id.get("INC-B") == 1
    assert by_id.get("INC-C") == 0


def test_get_incident_detail_includes_edges(client, db):
    """Detail response includes an 'edges' array, one entry per edge in
    the component, each with {from, to, p, shared_sensors}."""
    # Seed two alerts sharing eco2 within the edge window + link them
    # into an incident via regroup.
    from mlss_monitor.incident_grouper import regroup_all
    import sqlite3
    conn = sqlite3.connect(db)
    for ts in ("2026-04-23 09:00:00", "2026-04-23 09:10:00"):
        cur = conn.execute(
            "INSERT INTO inferences (created_at, event_type, severity, title, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, "tvoc_spike", "info", f"t-{ts}", 0.9),
        )
        conn.execute(
            "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) "
            "VALUES (?, ?, ?, ?)",
            (cur.lastrowid, "eco2_ppm", 0.8, 0),
        )
    conn.commit()
    conn.close()
    regroup_all(db)

    # Get the single incident that was created.
    resp = client.get("/api/incidents?window=30d")
    inc_id = resp.get_json()["incidents"][0]["id"]

    resp = client.get(f"/api/incidents/{inc_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "edges" in data
    assert len(data["edges"]) == 1
    edge = data["edges"][0]
    assert {"from", "to", "p", "shared_sensors"} <= set(edge.keys())
    assert edge["p"] == 1.0
    assert "eco2_ppm" in edge["shared_sensors"]


def test_split_endpoint_creates_marker_and_regroups(client, db):
    """POST /api/incidents/<id>/split creates an incident_splits row and
    triggers a regroup so the incident actually splits."""
    from mlss_monitor.incident_grouper import regroup_all
    conn = sqlite3.connect(db)
    alert_ids = []
    # Alert A (09:00): eco2_ppm only
    cur = conn.execute(
        "INSERT INTO inferences (created_at, event_type, severity, title, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        ("2026-04-23 09:00:00", "tvoc_spike", "info", "t-09:00", 0.9),
    )
    alert_ids.append(cur.lastrowid)
    conn.execute(
        "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) "
        "VALUES (?, ?, ?, ?)",
        (cur.lastrowid, "eco2_ppm", 0.8, 0),
    )
    # Alert B (09:10): both eco2_ppm and tvoc_ppb
    cur = conn.execute(
        "INSERT INTO inferences (created_at, event_type, severity, title, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        ("2026-04-23 09:10:00", "tvoc_spike", "info", "t-09:10", 0.9),
    )
    alert_ids.append(cur.lastrowid)
    conn.execute(
        "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) "
        "VALUES (?, ?, ?, ?)",
        (cur.lastrowid, "eco2_ppm", 0.8, 0),
    )
    conn.execute(
        "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) "
        "VALUES (?, ?, ?, ?)",
        (cur.lastrowid, "tvoc_ppb", 0.8, 0),
    )
    # Alert C (09:20): tvoc_ppb only
    cur = conn.execute(
        "INSERT INTO inferences (created_at, event_type, severity, title, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        ("2026-04-23 09:20:00", "tvoc_spike", "info", "t-09:20", 0.9),
    )
    alert_ids.append(cur.lastrowid)
    conn.execute(
        "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) "
        "VALUES (?, ?, ?, ?)",
        (cur.lastrowid, "tvoc_ppb", 0.8, 0),
    )
    conn.commit()
    conn.close()
    regroup_all(db)

    # Starts as one incident.
    listing = client.get("/api/incidents?window=30d").get_json()
    assert listing["total"] == 1
    inc_id = listing["incidents"][0]["id"]

    # Split at the middle alert.
    resp = client.post(
        f"/api/incidents/{inc_id}/split",
        json={"alert_id": alert_ids[1]},
    )
    assert resp.status_code == 200

    # Incident splits now contains the marker.
    conn = sqlite3.connect(db)
    markers = conn.execute("SELECT alert_id FROM incident_splits").fetchall()
    conn.close()
    assert (alert_ids[1],) in markers

    # Now there are two incidents.
    listing2 = client.get("/api/incidents?window=30d").get_json()
    assert listing2["total"] == 2


def test_split_endpoint_requires_alert_id(client, db):
    """Missing alert_id in body => 400."""
    resp = client.post("/api/incidents/INC-X/split", json={})
    assert resp.status_code == 400


def test_unsplit_endpoint_removes_marker_and_regroups(client, db):
    """POST /unsplit removes the marker and regroups, merging the incidents back."""
    from mlss_monitor.incident_grouper import regroup_all
    conn = sqlite3.connect(db)
    alert_ids = []
    # Same disjoint-sensor-chain pattern as the split test: A=eco2 only,
    # B=eco2+tvoc (bridge), C=tvoc only. With a split marker on B the
    # chain becomes {A}, {B, C}. Removing the marker lets B re-link A.
    sensor_sets = [
        [("eco2_ppm", 0.8)],
        [("eco2_ppm", 0.8), ("tvoc_ppb", 0.8)],
        [("tvoc_ppb", 0.8)],
    ]
    for ts, deps in zip(
        ("2026-04-23 09:00:00", "2026-04-23 09:10:00", "2026-04-23 09:20:00"),
        sensor_sets,
    ):
        cur = conn.execute(
            "INSERT INTO inferences (created_at, event_type, severity, title, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, "tvoc_spike", "info", f"t-{ts}", 0.9),
        )
        alert_ids.append(cur.lastrowid)
        for sensor, r in deps:
            conn.execute(
                "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) "
                "VALUES (?, ?, ?, ?)",
                (cur.lastrowid, sensor, r, 0),
            )
    conn.execute(
        "INSERT INTO incident_splits (alert_id, created_by) VALUES (?, ?)",
        (alert_ids[1], "test"),
    )
    conn.commit()
    conn.close()
    regroup_all(db)

    # Starts as two incidents because of the split marker.
    listing = client.get("/api/incidents?window=30d").get_json()
    assert listing["total"] == 2
    inc_id = listing["incidents"][0]["id"]

    resp = client.post(
        f"/api/incidents/{inc_id}/unsplit",
        json={"alert_id": alert_ids[1]},
    )
    assert resp.status_code == 200

    # Marker gone, single incident.
    conn = sqlite3.connect(db)
    remaining = conn.execute(
        "SELECT COUNT(*) FROM incident_splits"
    ).fetchone()[0]
    conn.close()
    assert remaining == 0
    listing2 = client.get("/api/incidents?window=30d").get_json()
    assert listing2["total"] == 1
