"""Tests for /api/history/* endpoints."""
import json
import pytest


def _insert_sensor_row(db_path, timestamp, tvoc=100, eco2=500, temp=21.0, hum=50.0,
                        pm1=2.0, pm25=3.0, pm10=5.0, co=12000, no2=8000, nh3=15000):
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO sensor_data
           (timestamp, tvoc, eco2, temperature, humidity,
            pm1_0, pm2_5, pm10, gas_co, gas_no2, gas_nh3)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (timestamp, tvoc, eco2, temp, hum, pm1, pm25, pm10, co, no2, nh3),
    )
    conn.commit()
    conn.close()


def test_sensor_endpoint_returns_all_channels(app_client, db):
    client, _ = app_client
    _insert_sensor_row(db, "2026-04-04 14:00:00")
    _insert_sensor_row(db, "2026-04-04 14:01:00", tvoc=110)
    resp = client.get(
        "/api/history/sensor?start=2026-04-04T13:00:00Z&end=2026-04-04T15:00:00Z"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "timestamps" in data
    assert "channels" in data
    expected_channels = [
        "tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
        "pm1_ug_m3", "pm25_ug_m3", "pm10_ug_m3", "co_ppb", "no2_ppb", "nh3_ppb",
    ]
    for ch in expected_channels:
        assert ch in data["channels"], f"Missing channel: {ch}"
    assert len(data["timestamps"]) == 2
    assert data["channels"]["tvoc_ppb"][0] == 100
    assert data["channels"]["tvoc_ppb"][1] == 110
    # Timestamps must be UTC ISO
    for ts in data["timestamps"]:
        assert ts.endswith("Z"), f"Timestamp not UTC ISO: {ts}"


def test_baselines_endpoint_returns_all_channels(app_client):
    """GET /api/history/baselines returns a baseline per channel plus threshold factor."""
    client, _ = app_client

    import mlss_monitor.state as st

    class _FakeAnomalyDetector:
        def baseline(self, ch):
            return {"tvoc_ppb": 118.4}.get(ch)

    class _FakeEngine:
        _anomaly_detector = _FakeAnomalyDetector()

    original = st.detection_engine
    st.detection_engine = _FakeEngine()
    try:
        resp = client.get("/api/history/baselines")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "tvoc_ppb" in data
        assert data["tvoc_ppb"] == pytest.approx(118.4)
        assert "anomaly_threshold_factor" in data
        assert data["anomaly_threshold_factor"] == pytest.approx(0.25)
        # Channels with no baseline should be null
        assert data.get("eco2_ppm") is None
    finally:
        st.detection_engine = original


def test_ml_context_returns_inferences_with_detection_method(app_client, db):
    client, _ = app_client
    from database.db_logger import save_inference
    save_inference(
        event_type="anomaly_combustion_signature",
        title="ML event",
        description="desc",
        action="act",
        severity="warning",
        confidence=0.85,
        evidence={"attribution_source": "combustion", "attribution_confidence": 0.81},
    )
    resp = client.get(
        "/api/history/ml-context?start=2020-01-01T00:00:00Z&end=2030-01-01T00:00:00Z"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "inferences" in data
    assert "attribution_summary" in data
    assert "dominant_source" in data
    assert len(data["inferences"]) >= 1
    inf = data["inferences"][0]
    assert inf["detection_method"] == "ml"
    assert inf["event_type"] == "anomaly_combustion_signature"


def test_narratives_endpoint_returns_required_keys(app_client, db):
    client, _ = app_client
    resp = client.get(
        "/api/history/narratives?start=2020-01-01T00:00:00Z&end=2030-01-01T00:00:00Z"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    required_keys = [
        "period_summary", "trend_indicators", "longest_clean_hours",
        "longest_clean_start", "longest_clean_end", "attribution_breakdown",
        "dominant_source_sentence", "fingerprint_narratives",
        "anomaly_model_narratives", "pattern_heatmap", "pattern_sentence",
        "drift_flags",
    ]
    for key in required_keys:
        assert key in data, f"Missing key: {key}"


def test_sparkline_returns_window_around_inference(app_client, db):
    client, _ = app_client
    from database.db_logger import save_inference
    from datetime import datetime, timedelta, timezone
    _now = datetime.now(timezone.utc)
    _fmt = lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S")
    _insert_sensor_row(db, _fmt(_now - timedelta(minutes=10)), tvoc=100)
    _insert_sensor_row(db, _fmt(_now), tvoc=350)
    _insert_sensor_row(db, _fmt(_now + timedelta(minutes=5)), tvoc=200)
    inf_id = save_inference(
        event_type="tvoc_spike",
        title="TVOC spike",
        description="desc",
        action="act",
        severity="warning",
        confidence=0.9,
        evidence={"sensor_snapshot": [{"channel": "tvoc_current"}]},
    )
    resp = client.get(f"/api/inferences/{inf_id}/sparkline")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "timestamps" in data
    assert "channels" in data
    assert "inference_at" in data
    assert "triggering_channels" in data
    assert data["inference_at"].endswith("Z")
    assert len(data["timestamps"]) >= 1
