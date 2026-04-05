"""Integration tests for /api/insights-engine/ endpoints.

Uses Flask test client with a minimal app fixture that wires up
state.detection_engine with real engine objects pointing at tmp config files.
"""
from __future__ import annotations


import pytest
import yaml


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def rules_yaml(tmp_path):
    data = {
        "rules": [
            {
                "id": "tvoc_spike",
                "expression": "tvoc_current > 250",
                "event_type": "tvoc_spike",
                "severity": "warning",
                "confidence": 0.8,
                "enabled": True,
                "title_template": "TVOC spike",
                "description_template": "TVOC {tvoc_current:.0f}",
                "action": "Ventilate",
            }
        ]
    }
    p = tmp_path / "rules.yaml"
    p.write_text(yaml.dump(data))
    return p


@pytest.fixture()
def fingerprints_yaml(tmp_path):
    data = {
        "sources": [
            {
                "id": "test_fp",
                "label": "Test",
                "description": "A test",
                "examples": "test",
                "sensors": {"tvoc": "elevated"},
                "temporal": {"rise_rate": "fast"},
                "confidence_floor": 0.5,
                "description_template": "TVOC: {tvoc_current:.0f}",
                "action_template": "Do something.",
            }
        ]
    }
    p = tmp_path / "fingerprints.yaml"
    p.write_text(yaml.dump(data))
    return p


@pytest.fixture()
def anomaly_yaml(tmp_path):
    data = {
        "anomaly": {
            "algorithm": "half_space_trees",
            "score_threshold": 0.7,
            "cold_start_readings": 500,
            "channels": ["tvoc_ppb", "eco2_ppm"],
        }
    }
    p = tmp_path / "anomaly.yaml"
    p.write_text(yaml.dump(data))
    return p


@pytest.fixture()
def app_client(tmp_path, rules_yaml, fingerprints_yaml, anomaly_yaml, monkeypatch):
    """Minimal Flask app wired to real engine objects with tmp config files."""
    from flask import Flask
    from mlss_monitor import state
    from mlss_monitor.threshold_engine import RuleEngine
    from mlss_monitor.attribution.engine import AttributionEngine
    from mlss_monitor.anomaly_detector import AnomalyDetector

    class _FakeEngine:
        _dry_run = True
        _rule_engine = RuleEngine(rules_yaml)
        _attribution_engine = AttributionEngine(fingerprints_yaml)
        _anomaly_detector = AnomalyDetector(anomaly_yaml, tmp_path / "models")
        _multivar_detector = None
        _rules_path = rules_yaml
        _fingerprints_path = fingerprints_yaml
        _anomaly_config_path = anomaly_yaml

    monkeypatch.setattr(state, "detection_engine", _FakeEngine())
    monkeypatch.setattr(state, "data_source_enabled", {"sgp30": True, "aht20": True})
    monkeypatch.setattr(state, "hot_tier", None)

    app = Flask(__name__, template_folder="../templates")
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test"

    from mlss_monitor.routes.api_insights import api_insights_bp
    app.register_blueprint(api_insights_bp)

    with app.test_client() as client:
        # Simulate a logged-in admin session so require_role passes
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user_role"] = "admin"
        yield client


# ── Rules endpoint tests ─────────────────────────────────────────────────────

def test_get_rules_returns_list(app_client):
    resp = app_client.get("/api/insights-engine/rules")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert data[0]["id"] == "tvoc_spike"


def test_patch_rule_updates_severity(app_client, rules_yaml):
    resp = app_client.patch(
        "/api/insights-engine/rules/tvoc_spike",
        json={"severity": "critical"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    # Verify the YAML was written
    loaded = yaml.safe_load(rules_yaml.read_text())
    assert loaded["rules"][0]["severity"] == "critical"


def test_patch_nonexistent_rule_returns_404(app_client):
    resp = app_client.patch(
        "/api/insights-engine/rules/does_not_exist",
        json={"severity": "critical"},
        content_type="application/json",
    )
    assert resp.status_code == 404


# ── Fingerprints endpoint tests ──────────────────────────────────────────────

def test_get_fingerprints_returns_list(app_client):
    resp = app_client.get("/api/insights-engine/fingerprints")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert data[0]["id"] == "test_fp"


def test_patch_fingerprint_updates_floor(app_client, fingerprints_yaml):
    resp = app_client.patch(
        "/api/insights-engine/fingerprints/test_fp",
        json={"confidence_floor": 0.75},
        content_type="application/json",
    )
    assert resp.status_code == 200
    loaded = yaml.safe_load(fingerprints_yaml.read_text())
    assert loaded["sources"][0]["confidence_floor"] == pytest.approx(0.75)


# ── Anomaly endpoint tests ───────────────────────────────────────────────────

def test_get_anomaly_returns_channels(app_client):
    resp = app_client.get("/api/insights-engine/anomaly")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "channels" in data
    ch = data["channels"]
    assert any(c["channel"] == "tvoc_ppb" for c in ch)


def test_reset_channel(app_client):
    resp = app_client.post("/api/insights-engine/anomaly/tvoc_ppb/reset")
    assert resp.status_code == 200


def test_reset_unknown_channel_returns_404(app_client):
    resp = app_client.post("/api/insights-engine/anomaly/nonexistent/reset")
    assert resp.status_code == 404


# ── Sources endpoint tests ───────────────────────────────────────────────────

def test_get_sources_returns_list(app_client):
    resp = app_client.get("/api/insights-engine/sources")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    names = [s["name"] for s in data]
    assert "sgp30" in names


def test_disable_source(app_client):
    resp = app_client.post("/api/insights-engine/sources/sgp30/disable")
    assert resp.status_code == 200
    from mlss_monitor import state
    assert state.data_source_enabled["sgp30"] is False


def test_enable_source(app_client):
    from mlss_monitor import state
    state.data_source_enabled["aht20"] = False
    resp = app_client.post("/api/insights-engine/sources/aht20/enable")
    assert resp.status_code == 200
    assert state.data_source_enabled["aht20"] is True


def test_enable_unknown_source_returns_404(app_client):
    resp = app_client.post("/api/insights-engine/sources/nonexistent/enable")
    assert resp.status_code == 404
