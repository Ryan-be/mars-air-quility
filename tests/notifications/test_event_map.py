"""Tests for event_bus event_type -> notification mapping."""

from mlss_monitor.notifications import event_map


def test_inference_fired_maps_to_air_quality():
    result = event_map.map_event("inference_fired", {
        "severity": "warning",
        "title": "TVOC spike: 850 ppb",
        "description": "Elevated reading on the SGP30 sensor over 5 minutes",
    })
    assert result is not None
    assert result.category == "air_quality"
    assert result.severity == "warning"
    assert result.title == "TVOC spike: 850 ppb"
    assert result.body == "Elevated reading on the SGP30 sensor over 5 minutes"
    assert result.deep_link == "/incidents"


def test_backup_status_backoff_maps_to_warning():
    result = event_map.map_event("backup_status_changed", {
        "pipeline": "db",
        "state": "BACKOFF",
        "backoff_seconds": 120,
        "pending": 47,
    })
    assert result.category == "backup_pipeline"
    assert result.severity == "warning"
    assert "BACKOFF" in result.title
    assert "120" in result.body
    assert result.deep_link == "/admin/backup"


def test_backup_status_disabled_by_error_is_critical():
    result = event_map.map_event("backup_status_changed", {
        "pipeline": "files",
        "state": "DISABLED_BY_ERROR",
    })
    assert result.severity == "critical"


def test_backup_status_idle_returns_none():
    assert event_map.map_event("backup_status_changed", {
        "pipeline": "db", "state": "IDLE",
    }) is None


def test_health_update_transition_to_unavailable():
    result = event_map.map_event("health_update", {
        "AHT20": "OK", "SGP30": "UNAVAILABLE",
        "PM_sensor": "OK", "MICS6814": "OK",
    })
    assert result is not None
    assert result.category == "system_health"
    assert result.severity == "warning"
    assert "SGP30" in result.title


def test_health_update_multiple_failures_is_critical():
    result = event_map.map_event("health_update", {
        "AHT20": "UNAVAILABLE", "SGP30": "UNAVAILABLE",
        "PM_sensor": "OK", "MICS6814": "OK",
    })
    assert result.severity == "critical"


def test_health_update_all_ok_returns_none():
    assert event_map.map_event("health_update", {
        "AHT20": "OK", "SGP30": "OK",
        "PM_sensor": "OK", "MICS6814": "OK",
    }) is None


def test_grow_error_logged_maps_to_grow_units():
    result = event_map.map_event("grow_error_logged", {
        "unit_id": 3,
        "severity": "critical",
        "title": "Pump stuck on",
        "message": "Watering pump has been on for 600s — manual shutoff needed",
    })
    assert result.category == "grow_units"
    assert result.severity == "critical"
    assert "#3" in result.title
    assert result.deep_link == "/grow/3"


def test_unmapped_event_returns_none():
    assert event_map.map_event("sensor_update", {}) is None
    assert event_map.map_event("weather_update", {}) is None
    assert event_map.map_event("fan_status", {}) is None
    assert event_map.map_event("anomaly_scores", {}) is None
    assert event_map.map_event("backup_config_changed", {}) is None
