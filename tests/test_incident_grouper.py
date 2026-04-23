"""Tests for mlss_monitor.incident_grouper (pure logic only — no DB calls)."""
import sys
from unittest.mock import MagicMock

# Stub hardware libs before any app import
for _mod in ["board", "busio", "adafruit_ahtx0", "adafruit_sgp30",
             "mics6814", "authlib", "authlib.integrations",
             "authlib.integrations.flask_client"]:
    sys.modules.setdefault(_mod, MagicMock())

from datetime import datetime, timedelta
import pytest
from mlss_monitor.incident_grouper import (
    sessionise,
    detection_method,
    CROSS_INCIDENT_TYPES,
    make_incident_id,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _ts(minutes_offset: int) -> datetime:
    base = datetime(2026, 4, 19, 12, 0, 0)
    return base + timedelta(minutes=minutes_offset)


def _alert(minutes_offset: int, event_type: str = "tvoc_spike", severity: str = "info"):
    return {
        "id": minutes_offset,
        "created_at": _ts(minutes_offset).isoformat(),
        "event_type": event_type,
        "severity": severity,
        "title": f"Alert {minutes_offset}",
        "confidence": 0.8,
    }


# ── sessionise ───────────────────────────────────────────────────────────────

def test_sessionise_single_alert_one_group():
    alerts = [_alert(0)]
    groups = sessionise(alerts)
    assert len(groups) == 1
    assert len(groups[0]) == 1


def test_sessionise_two_close_alerts_one_group():
    """29-minute gap → same group."""
    alerts = [_alert(0), _alert(29)]
    groups = sessionise(alerts)
    assert len(groups) == 1


def test_sessionise_gap_over_30_splits():
    """31-minute gap → two groups (uses .total_seconds(), not .seconds)."""
    alerts = [_alert(0), _alert(31)]
    groups = sessionise(alerts)
    assert len(groups) == 2


def test_sessionise_exactly_30min_is_same_group():
    """Exactly 30 minutes → same group (> not >=)."""
    alerts = [_alert(0), _alert(30)]
    groups = sessionise(alerts)
    assert len(groups) == 1


def test_sessionise_large_gap_uses_total_seconds():
    """60-minute gap; .seconds would return 0, .total_seconds() returns 3600."""
    alerts = [_alert(0), _alert(60)]
    groups = sessionise(alerts)
    assert len(groups) == 2


def test_sessionise_preserves_order():
    """Alerts are sorted chronologically before grouping."""
    alerts = [_alert(10), _alert(0), _alert(5)]
    groups = sessionise(alerts)
    assert len(groups) == 1
    assert [a["id"] for a in groups[0]] == [0, 5, 10]


def test_sessionise_empty_list():
    assert sessionise([]) == []


# ── detection_method ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("event_type,expected", [
    ("anomaly_combustion_signature", "ml"),
    ("anomaly_thermal_moisture",     "ml"),
    ("anomaly_anything_new",         "ml"),
    ("ml_learned_pattern",           "ml"),
    ("fingerprint_match",            "fingerprint"),
    ("hourly_summary",               "summary"),
    ("daily_summary",                "summary"),
    ("daily_pattern",                "summary"),
    ("annotation_context_cooking",   "summary"),
    ("annotation_context_",          "summary"),
    ("correlated_pollution",         "statistical"),
    ("sustained_poor_air",           "statistical"),
    ("tvoc_spike",                   "threshold"),
    ("eco2_danger",                  "threshold"),
    ("pm25_elevated",                "threshold"),
    ("temp_high",                    "threshold"),
    ("mould_risk",                   "threshold"),
])
def test_detection_method_mapping(event_type, expected):
    assert detection_method(event_type) == expected


# ── CROSS_INCIDENT_TYPES ─────────────────────────────────────────────────────

def test_cross_incident_types_contains_summaries():
    assert "hourly_summary" in CROSS_INCIDENT_TYPES
    assert "daily_summary" in CROSS_INCIDENT_TYPES
    assert "daily_pattern" in CROSS_INCIDENT_TYPES


# ── make_incident_id ─────────────────────────────────────────────────────────

def test_make_incident_id_format():
    ts = datetime(2026, 4, 19, 12, 55)
    assert make_incident_id(ts) == "INC-20260419-1255"


def test_make_incident_id_deterministic():
    ts = datetime(2026, 4, 19, 12, 55)
    assert make_incident_id(ts) == make_incident_id(ts)
