"""Tests for compute_detection_method() in db_logger."""
import pytest
from database.db_logger import compute_detection_method


def test_ml_event_types_return_ml():
    for et in [
        "anomaly_combustion_signature",
        "anomaly_particle_distribution",
        "anomaly_ventilation_quality",
        "anomaly_gas_relationship",
        "anomaly_thermal_moisture",
    ]:
        assert compute_detection_method(et) == "ml", f"Expected 'ml' for {et}"


def test_statistical_event_types_return_statistical():
    for et in [
        "anomaly_tvoc", "anomaly_eco2", "anomaly_temperature",
        "anomaly_humidity", "anomaly_pm25", "anomaly_pm1",
        "anomaly_pm10", "anomaly_co", "anomaly_no2", "anomaly_nh3",
    ]:
        assert compute_detection_method(et) == "statistical", f"Expected 'statistical' for {et}"


def test_rule_event_types_return_rule():
    for et in [
        "tvoc_spike", "eco2_danger", "eco2_elevated", "mould_risk",
        "correlated_pollution", "sustained_poor_air",
        "pm1_spike", "pm25_spike", "pm10_spike",
        "temp_high", "temp_low", "humidity_high", "humidity_low",
        "vpd_high", "vpd_low",
        "rapid_tvoc_rise", "rapid_eco2_rise", "rapid_pm25_rise",
        "hourly_summary", "daily_summary",
    ]:
        assert compute_detection_method(et) == "rule", f"Expected 'rule' for {et}"


def test_annotation_context_prefix_returns_rule():
    assert compute_detection_method("annotation_context_cooking") == "rule"
    assert compute_detection_method("annotation_context_anything") == "rule"


def test_unknown_event_type_returns_rule():
    assert compute_detection_method("totally_unknown_type") == "rule"


def test_get_inferences_includes_detection_method(db):
    """db fixture from conftest monkeypatches database.db_logger.DB_FILE."""
    from database.db_logger import save_inference, get_inferences
    save_inference(
        event_type="anomaly_combustion_signature",
        title="ML test",
        description="desc",
        action="act",
        severity="warning",
        confidence=0.8,
        evidence={},
    )
    rows = get_inferences(limit=1)
    assert rows[0]["detection_method"] == "ml"
