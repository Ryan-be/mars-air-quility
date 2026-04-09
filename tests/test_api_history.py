"""Tests for /api/history/* endpoints."""
import sqlite3

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


def test_narratives_fingerprint_counts_rule_fired_attribution(app_client, db):
    """Rule-fired inferences that store attribution under 'attribution' (not
    'attribution_source') must be counted in fingerprint_narratives event_count."""
    client, _ = app_client
    from database.db_logger import save_inference

    # Simulate two rule-fired inferences with attribution stored in the rule key.
    # Both event_types must be in the schema's CHECK constraint allowlist.
    save_inference(
        event_type="eco2_elevated",
        title="eCO2 elevated — Cooking activity (100%)",
        description="desc",
        action="act",
        severity="warning",
        confidence=1.0,
        evidence={"attribution": "cooking", "attribution_confidence": 1.0, "fv_timestamp": "2026-04-04T12:00:00"},
    )
    save_inference(
        event_type="tvoc_spike",
        title="TVOC spike — Cooking activity (95%)",
        description="desc",
        action="act",
        severity="warning",
        confidence=0.95,
        evidence={"attribution": "cooking", "attribution_confidence": 0.95, "fv_timestamp": "2026-04-04T12:05:00"},
    )

    resp = client.get(
        "/api/history/narratives?start=2020-01-01T00:00:00Z&end=2030-01-01T00:00:00Z"
    )
    assert resp.status_code == 200
    data = resp.get_json()

    # attribution_breakdown must include cooking
    assert "cooking" in data["attribution_breakdown"], (
        "cooking must appear in attribution_breakdown for rule-fired inferences"
    )
    assert data["attribution_breakdown"]["cooking"] == 2

    # fingerprint_narratives for cooking must have event_count == 2
    fp_map = {fp["source_id"]: fp for fp in data["fingerprint_narratives"]}
    assert "cooking" in fp_map
    assert fp_map["cooking"]["event_count"] == 2, (
        f"Expected 2 cooking events in fingerprint_narratives, got {fp_map['cooking']['event_count']}"
    )
    assert fp_map["cooking"]["avg_confidence"] == pytest.approx(0.975, abs=0.01)


def test_sparkline_returns_window_around_inference(app_client, db):
    client, _ = app_client
    from database.db_logger import save_inference
    from datetime import datetime, timedelta, timezone
    _now = datetime.now(timezone.utc)

    def _fmt(dt):
        return dt.strftime("%Y-%m-%d %H:%M:%S")
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
    # All returned timestamps must be within ±15 minutes of inference_at
    inf_dt = datetime.fromisoformat(data["inference_at"].rstrip("Z")).replace(tzinfo=timezone.utc)
    for ts in data["timestamps"]:
        ts_dt = datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=timezone.utc)
        assert abs((ts_dt - inf_dt).total_seconds()) <= 900, f"Timestamp {ts} outside ±15 min window"
    # triggering_channels must include tvoc_ppb (from sensor_snapshot)
    assert "tvoc_ppb" in data["triggering_channels"]


def test_range_tag_endpoint(app_client, db):
    """Test the range-tag endpoint creates an inference with a tag."""
    client, _ = app_client
    # Insert some test sensor data
    _insert_sensor_row(db, "2023-01-01 10:00:00", tvoc=100, eco2=400)
    _insert_sensor_row(db, "2023-01-01 10:01:00", tvoc=150, eco2=450)
    _insert_sensor_row(db, "2023-01-01 10:02:00", tvoc=200, eco2=500)

    # Test the range-tag endpoint
    start = "2023-01-01T10:00:00Z"
    end = "2023-01-01T10:02:00Z"
    tag = "cooking"

    response = client.post("/api/history/range-tag", json={
        "start": start,
        "end": end,
        "tag": tag
    })
    assert response.status_code == 200
    data = response.get_json()
    assert "id" in data
    assert data["tag"] == tag

    # Verify the inference was created
    from database.db_logger import get_inferences, get_inference_tags
    inferences = get_inferences()
    inference = next((i for i in inferences if i["id"] == data["id"]), None)
    assert inference is not None
    assert inference["event_type"] == "annotation_context_user_range"
    assert inference["title"] == "User-tagged event"
    assert isinstance(inference["evidence"], dict)
    assert inference["evidence"]["feature_vector"]["tvoc_current"] == 200.0
    assert inference["evidence"]["feature_vector"]["eco2_current"] == 500.0
    assert isinstance(inference["evidence"]["readings"], list)
    assert len(inference["evidence"]["readings"]) == 3

    # Verify the tag was added
    tags = get_inference_tags(data["id"])
    assert len(tags) == 1
    assert tags[0]["tag"] == tag


# ---------------------------------------------------------------------------
# _compute_historical_baselines
# ---------------------------------------------------------------------------

def _create_sensor_db(db_path: str) -> None:
    """Create a minimal sensor_data table in a fresh SQLite file."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE sensor_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            tvoc REAL, eco2 REAL, temperature REAL, humidity REAL,
            pm1_0 REAL, pm2_5 REAL, pm10 REAL,
            gas_co REAL, gas_no2 REAL, gas_nh3 REAL
        )"""
    )
    conn.commit()
    conn.close()


def _insert_baseline_row(db_path: str, timestamp: str, **kwargs) -> None:
    defaults = dict(tvoc=100.0, eco2=500.0, temperature=21.0, humidity=50.0,
                    pm1_0=2.0, pm2_5=3.0, pm10=5.0, gas_co=12000.0,
                    gas_no2=8000.0, gas_nh3=15000.0)
    defaults.update(kwargs)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO sensor_data
           (timestamp, tvoc, eco2, temperature, humidity,
            pm1_0, pm2_5, pm10, gas_co, gas_no2, gas_nh3)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (timestamp, defaults["tvoc"], defaults["eco2"], defaults["temperature"],
         defaults["humidity"], defaults["pm1_0"], defaults["pm2_5"], defaults["pm10"],
         defaults["gas_co"], defaults["gas_no2"], defaults["gas_nh3"]),
    )
    conn.commit()
    conn.close()


def test_compute_historical_baselines_returns_median(tmp_path, monkeypatch):
    """_compute_historical_baselines returns the median of the 60-min pre-event window."""
    db_file = str(tmp_path / "test_sensor.db")
    _create_sensor_db(db_file)

    # event start: 2026-01-01T12:00:00Z
    # baseline window: 2026-01-01T11:00:00Z – 2026-01-01T12:00:00Z (exclusive)
    _insert_baseline_row(db_file, "2026-01-01 11:00:00", tvoc=100.0)
    _insert_baseline_row(db_file, "2026-01-01 11:30:00", tvoc=200.0)
    _insert_baseline_row(db_file, "2026-01-01 11:59:00", tvoc=300.0)
    # Row at exactly the event start must NOT be included (window is < start)
    _insert_baseline_row(db_file, "2026-01-01 12:00:00", tvoc=999.0)
    # Row outside the window must NOT be included
    _insert_baseline_row(db_file, "2026-01-01 10:59:00", tvoc=1.0)

    import database.db_logger as _dbl
    monkeypatch.setattr(_dbl, "DB_FILE", db_file)

    from mlss_monitor.routes.api_history import _compute_historical_baselines

    result = _compute_historical_baselines("2026-01-01T12:00:00Z")

    # Median of [100, 200, 300] = 200
    assert result["tvoc_ppb"] == pytest.approx(200.0)
    # All other channels have a uniform value of the default; spot-check temperature
    assert result["temperature_c"] == pytest.approx(21.0)
    # All expected fields must be present
    expected_fields = [
        "tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
        "pm1_ug_m3", "pm25_ug_m3", "pm10_ug_m3", "co_ppb", "no2_ppb", "nh3_ppb",
    ]
    for f in expected_fields:
        assert f in result, f"Missing field: {f}"


def test_compute_historical_baselines_no_data_returns_none(tmp_path, monkeypatch):
    """_compute_historical_baselines returns None for all channels when no rows exist."""
    db_file = str(tmp_path / "empty_sensor.db")
    _create_sensor_db(db_file)

    import database.db_logger as _dbl
    monkeypatch.setattr(_dbl, "DB_FILE", db_file)

    from mlss_monitor.routes.api_history import _compute_historical_baselines

    result = _compute_historical_baselines("2026-01-01T12:00:00Z")

    for field, value in result.items():
        assert value is None, f"Expected None for {field}, got {value}"


def test_compute_historical_baselines_even_count_uses_average(tmp_path, monkeypatch):
    """With an even number of rows the median averages the two middle values."""
    db_file = str(tmp_path / "even_sensor.db")
    _create_sensor_db(db_file)

    _insert_baseline_row(db_file, "2026-01-01 11:00:00", tvoc=100.0)
    _insert_baseline_row(db_file, "2026-01-01 11:30:00", tvoc=200.0)

    import database.db_logger as _dbl
    monkeypatch.setattr(_dbl, "DB_FILE", db_file)

    from mlss_monitor.routes.api_history import _compute_historical_baselines

    result = _compute_historical_baselines("2026-01-01T12:00:00Z")

    # Median of [100, 200] (even) = (100 + 200) / 2 = 150
    assert result["tvoc_ppb"] == pytest.approx(150.0)
