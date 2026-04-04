# tests/test_inference_evidence.py
from datetime import datetime, timezone

import pytest
from mlss_monitor.feature_vector import FeatureVector
from mlss_monitor.inference_evidence import (
    build_sensor_snapshot,
    anomaly_description,
    anomaly_action,
)

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _fv(**kwargs):
    return FeatureVector(timestamp=_TS, **kwargs)


# ── build_sensor_snapshot ─────────────────────────────────────────────────────

def test_snapshot_includes_label_unit_value():
    fv = _fv(tvoc_current=487.0, tvoc_slope_1m=10.0)
    snap = build_sensor_snapshot(fv, ["tvoc_current"], {"tvoc_current": 152.0})
    assert len(snap) == 1
    entry = snap[0]
    assert entry["label"] == "TVOC"
    assert entry["unit"] == "ppb"
    assert entry["value"] == 487.0


def test_snapshot_computes_ratio():
    fv = _fv(tvoc_current=487.0)
    snap = build_sensor_snapshot(fv, ["tvoc_current"], {"tvoc_current": 152.0})
    assert snap[0]["ratio"] == pytest.approx(3.2, abs=0.1)


def test_snapshot_ratio_band_high_above_3x():
    fv = _fv(co_current=100.0)
    snap = build_sensor_snapshot(fv, ["co_current"], {"co_current": 10.0})
    assert snap[0]["ratio_band"] == "high"


def test_snapshot_ratio_band_elevated_between_1_5_and_3():
    fv = _fv(co_current=20.0)
    snap = build_sensor_snapshot(fv, ["co_current"], {"co_current": 10.0})
    assert snap[0]["ratio_band"] == "elevated"


def test_snapshot_ratio_band_normal_below_1_5():
    fv = _fv(co_current=11.0)
    snap = build_sensor_snapshot(fv, ["co_current"], {"co_current": 10.0})
    assert snap[0]["ratio_band"] == "normal"


def test_snapshot_trend_rising_when_slope_above_threshold():
    fv = _fv(tvoc_current=300.0, tvoc_slope_1m=20.0)  # threshold=5.0
    snap = build_sensor_snapshot(fv, ["tvoc_current"], {})
    assert snap[0]["trend"] == "rising"


def test_snapshot_trend_falling_when_slope_below_negative_threshold():
    fv = _fv(tvoc_current=300.0, tvoc_slope_1m=-20.0)
    snap = build_sensor_snapshot(fv, ["tvoc_current"], {})
    assert snap[0]["trend"] == "falling"


def test_snapshot_trend_stable_when_slope_near_zero():
    fv = _fv(tvoc_current=300.0, tvoc_slope_1m=0.1)
    snap = build_sensor_snapshot(fv, ["tvoc_current"], {})
    assert snap[0]["trend"] == "stable"


def test_snapshot_skips_channel_with_none_value():
    fv = _fv(tvoc_current=None)
    snap = build_sensor_snapshot(fv, ["tvoc_current"], {})
    assert snap == []


def test_snapshot_no_baseline_leaves_ratio_none():
    fv = _fv(tvoc_current=300.0)
    snap = build_sensor_snapshot(fv, ["tvoc_current"], {})
    assert snap[0]["ratio"] is None
    assert snap[0]["ratio_band"] == "unknown"


# ── anomaly_description ───────────────────────────────────────────────────────

def test_description_single_channel_includes_value_and_ratio():
    snap = [{"label": "TVOC", "value": 487.0, "unit": "ppb",
              "baseline": 152.0, "ratio": 3.2, "trend": "rising"}]
    desc = anomaly_description(snap)
    assert "487" in desc
    assert "3.2" in desc
    assert "rising" in desc.lower()


def test_description_multivar_mentions_model_label():
    snap = [
        {"label": "CO",   "value": 50.0, "unit": "ppb", "baseline": 10.0, "ratio": 5.0, "trend": "rising"},
        {"label": "PM2.5","value": 40.0, "unit": "µg/m³","baseline": 12.0, "ratio": 3.3, "trend": "stable"},
    ]
    desc = anomaly_description(snap, model_label="Combustion signature")
    assert "Combustion signature" in desc or "combustion" in desc.lower()


def test_description_empty_snapshot_returns_fallback():
    desc = anomaly_description([])
    assert "anomaly" in desc.lower()


# ── anomaly_action ────────────────────────────────────────────────────────────

def test_action_combustion_signature_mentions_ventilate():
    action = anomaly_action(model_id="combustion_signature")
    assert "ventilat" in action.lower()


def test_action_single_channel_tvoc():
    action = anomaly_action(channel="tvoc_ppb")
    assert action  # non-empty


def test_action_unknown_returns_generic():
    action = anomaly_action(model_id="nonexistent_model")
    assert action  # non-empty string, not blank
