"""Tests for ``mlss_monitor.routes.api_topology``.

Phase 4 Task 4.3 — the single-fetch ``GET /api/topology`` snapshot
endpoint the /controls page uses on first paint. Subsequent live
updates land via the SSE bus (Phase 10 wiring) — this endpoint is
deliberately read-only and never publishes events.

Response shape::

    {
      "hub":       {id: "hub", kind: "hub", label: "...", sensors: {...}, ...},
      "grows":     [{id: "grow:<n>", kind: "grow",     label, sensors, phase, plant_type}, ...],
      "effectors": [{id: "effector:<n>", kind: "effector", parent, label, ...}, ...],
      "layout":    {"<node-id>": {x, y}, ...},
    }

The fixture seeds: one hub-scoped fan, one grow-scoped heat_pad, one
hub-scoped AC, and two grow_units rows — enough to assert the parent
keying (``"hub"`` for hub-scoped, ``"grow:<id>"`` for grow-scoped)
plus the mode-derivation ladder (auto > on > off).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from database.init_db import create_db


def _seed_grow_unit(db_path: str, unit_id: int, label: str, plant_type: str = "tomato"):
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, plant_type, "
        " enrolled_at, bearer_token_hash, phase_set_at) "
        "VALUES (?, ?, ?, ?, ?, 'h', ?)",
        (unit_id, f"hw-{unit_id}", label, plant_type, now, now),
    )
    conn.commit()
    conn.close()


def _seed_grow_telemetry(db_path: str, unit_id: int, **fields):
    """Insert one telemetry row so the topology endpoint surfaces values."""
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_telemetry "
        "(unit_id, timestamp_utc, soil_moisture_raw, light_state, "
        " pump_state, soil_moisture_pct, soil_temp_c, air_temp_c, "
        " air_humidity_pct) "
        "VALUES (?, ?, 600, 0, 0, ?, ?, ?, ?)",
        (
            unit_id, now,
            fields.get("soil_moisture_pct"),
            fields.get("soil_temp_c"),
            fields.get("air_temp_c"),
            fields.get("air_humidity_pct"),
        ),
    )
    conn.commit()
    conn.close()


def _seed_node_layout(db_path: str, kind: str, node_id: str, x: float, y: float):
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO node_layout (node_kind, node_id, x, y, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (kind, node_id, x, y, now),
    )
    conn.commit()
    conn.close()


def _login(client, role: str = "admin"):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user"] = f"test-{role}"
        sess["user_role"] = role
        sess["user_id"] = None


@pytest.fixture
def topo_client(monkeypatch, tmp_path):
    """Flask app with the topology + v2 effector blueprints + primed DB."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    monkeypatch.setattr("mlss_monitor.effectors.store.DB_FILE", db_path)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_effectors_v2.DB_FILE", db_path,
    )
    monkeypatch.setattr(
        "mlss_monitor.routes.api_topology.DB_FILE", db_path,
    )
    # Detach the hot_tier singleton — the endpoint reads it for hub
    # sensor values and a real one would still be wired to the
    # production DB path.
    from mlss_monitor import state as app_state
    monkeypatch.setattr(app_state, "hot_tier", None)

    create_db()

    from flask import Flask
    from mlss_monitor.routes.api_topology import api_topology_bp
    from mlss_monitor.routes.api_effectors_v2 import api_effectors_v2_bp

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.register_blueprint(api_topology_bp)
    app.register_blueprint(api_effectors_v2_bp)
    return app.test_client(), db_path


def test_topology_returns_full_snapshot(topo_client):
    """End-to-end shape: hub + grows + effectors + layout."""
    client, db_path = topo_client
    _login(client, "admin")
    # Seed: 2 grow units + 3 effectors (1 hub fan, 1 grow heat_pad, 1 hub AC)
    _seed_grow_unit(db_path, 1, "Tomato 1", plant_type="tomato")
    _seed_grow_unit(db_path, 2, "Basil 1",  plant_type="basil")
    _seed_grow_telemetry(
        db_path, 1, soil_moisture_pct=58.0, soil_temp_c=22.5,
        air_temp_c=24.0, air_humidity_pct=55.0,
    )
    client.post("/api/effectors", json={
        "label": "Room fan", "effector_type": "fan", "scope": "hub",
        "kasa_host": "192.0.2.50",
    })
    client.post("/api/effectors", json={
        "label": "Pad 1", "effector_type": "heat_pad",
        "scope": "grow_unit", "grow_unit_id": 1,
        "kasa_host": "192.0.2.51",
    })
    client.post("/api/effectors", json={
        "label": "Room AC", "effector_type": "ac", "scope": "hub",
        "kasa_host": "192.0.2.52",
    })
    # And one persisted hub layout entry to prove the layout merger works
    _seed_node_layout(db_path, "hub", "hub", 100.0, 50.0)

    r = client.get("/api/topology")
    assert r.status_code == 200
    body = r.get_json()

    # ── Hub ────────────────────────────────────────────────────────
    assert body["hub"]["id"] == "hub"
    assert body["hub"]["kind"] == "hub"
    assert body["hub"]["label"] == "MLSS Hub"
    # Sensor keys are present even when no readings — values nullable.
    for key in ("temp", "rh", "co2"):
        assert key in body["hub"]["sensors"], f"hub sensors missing {key!r}"

    # ── Grows ──────────────────────────────────────────────────────
    grows = body["grows"]
    assert len(grows) == 2
    g1 = next(g for g in grows if g["id"] == "grow:1")
    assert g1["kind"] == "grow"
    assert g1["label"] == "Tomato 1"
    assert g1["plant_type"] == "tomato"
    assert g1["phase"] == "vegetative"  # the schema default
    # Telemetry-backed sensors surfaced
    assert g1["sensors"]["soil_moisture"] == 58.0
    assert g1["sensors"]["soil_temp_c"] == 22.5
    assert g1["sensors"]["air_temp_c"] == 24.0
    assert g1["sensors"]["air_humidity_pct"] == 55.0
    # Grow 2 has no telemetry yet — should still appear with None sensors.
    g2 = next(g for g in grows if g["id"] == "grow:2")
    assert g2["sensors"]["soil_moisture"] is None

    # ── Effectors ──────────────────────────────────────────────────
    effs = body["effectors"]
    assert len(effs) == 3
    # Hub-scoped fan + ac have parent == "hub"
    fan = next(e for e in effs if e["label"] == "Room fan")
    assert fan["kind"] == "effector"
    assert fan["id"].startswith("effector:")
    assert fan["parent"] == "hub"
    assert fan["effector_type"] == "fan"
    # auto_mode default = 1 → mode == "auto"
    assert fan["mode"] == "auto"
    assert fan["current_state"] == "unknown"
    # Grow-scoped heat_pad has parent == "grow:1"
    pad = next(e for e in effs if e["label"] == "Pad 1")
    assert pad["parent"] == "grow:1"
    assert pad["effector_type"] == "heat_pad"
    # Hub-scoped AC also has parent == "hub"
    ac = next(e for e in effs if e["label"] == "Room AC")
    assert ac["parent"] == "hub"
    assert ac["effector_type"] == "ac"

    # Wire-level details surfaced so the side panel can read them on
    # first paint without a follow-up GET /api/effectors/<id>.
    for eff in effs:
        assert "kasa_host" in eff
        assert "protocol" in eff
        assert "auto_mode" in eff
        # Per-tick reasoning blob — None until the evaluator runs.
        assert "last_evaluation" in eff
        assert eff["last_evaluation"] is None

    # ── Layout ─────────────────────────────────────────────────────
    assert body["layout"]["hub"] == {"x": 100.0, "y": 50.0}


def test_topology_returns_empty_layout_when_none_persisted(topo_client):
    """No node_layout rows and no layout_json blobs → empty dict."""
    client, _ = topo_client
    _login(client, "viewer")
    r = client.get("/api/topology")
    assert r.status_code == 200
    body = r.get_json()
    assert body["layout"] == {}


def test_topology_hub_sensors_pull_from_hot_tier(topo_client, monkeypatch):
    """When hot_tier has a NormalisedReading, hub sensors surface it.

    Mocks the singleton with a small stub so the endpoint doesn't have
    to spin up the real hardware pipeline.
    """
    client, _ = topo_client
    from mlss_monitor import state as app_state
    fake = MagicMock()
    fake_reading = MagicMock()
    fake_reading.temperature_c = 23.7
    fake_reading.humidity_pct = 41.0
    fake_reading.eco2_ppm = 612
    fake.snapshot.return_value = [fake_reading]
    monkeypatch.setattr(app_state, "hot_tier", fake)
    _login(client, "viewer")
    r = client.get("/api/topology")
    assert r.status_code == 200
    sensors = r.get_json()["hub"]["sensors"]
    assert sensors["temp"] == 23.7
    assert sensors["rh"] == 41.0
    assert sensors["co2"] == 612


def test_topology_effector_layout_merges_into_layout(topo_client):
    """An effector with persisted layout_json shows up under the key
    ``effector:<id>`` in the merged layout dict."""
    client, _ = topo_client
    _login(client, "admin")
    # Use the v2 API to create an effector then patch its layout
    new = client.post("/api/effectors", json={
        "label": "Room fan", "effector_type": "fan", "scope": "hub",
        "kasa_host": "192.0.2.60",
    }).get_json()
    plug_id = new["id"]
    client.patch("/api/effectors/layout", json={
        "positions": [
            {"kind": "effector", "id": plug_id, "x": 42.0, "y": 7.5},
        ],
    })
    body = client.get("/api/topology").get_json()
    assert body["layout"][f"effector:{plug_id}"] == {"x": 42.0, "y": 7.5}


def test_topology_inactive_grow_units_excluded(topo_client):
    """``is_active=0`` units don't appear in the grows list."""
    client, db_path = topo_client
    _login(client, "admin")
    _seed_grow_unit(db_path, 1, "Active 1")
    _seed_grow_unit(db_path, 2, "Soft-deleted")
    # Soft-delete unit 2
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE grow_units SET is_active=0 WHERE id=2")
    conn.commit()
    conn.close()
    body = client.get("/api/topology").get_json()
    grows = body["grows"]
    assert len(grows) == 1
    assert grows[0]["id"] == "grow:1"
