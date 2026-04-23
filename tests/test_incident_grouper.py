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
    compute_pearson_r,
    build_incident_similarity_vector,
    cosine_similarity,
    generate_incident_title,
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


# ── compute_pearson_r ─────────────────────────────────────────────────────────

def test_compute_pearson_r_perfect_correlation():
    xs = [float(i) for i in range(10)]
    ys = [float(i) for i in range(10)]
    r = compute_pearson_r(xs, ys)
    assert r is not None
    assert abs(r - 1.0) < 1e-9


def test_compute_pearson_r_anti_correlation():
    xs = [float(i) for i in range(10)]
    ys = [float(-i) for i in range(10)]
    r = compute_pearson_r(xs, ys)
    assert r is not None
    assert abs(r + 1.0) < 1e-9


def test_compute_pearson_r_none_when_too_few_points():
    """Returns None (not 0.0) when < MIN_DATA_POINTS overlapping points."""
    xs = [1.0, 2.0, 3.0]
    ys = [1.0, 2.0, 3.0]
    r = compute_pearson_r(xs, ys)
    assert r is None


def test_compute_pearson_r_none_not_zero_for_missing():
    """Invariant: missing data → None, never 0.0."""
    r = compute_pearson_r([], [])
    assert r is None
    assert r != 0.0


def test_compute_pearson_r_filters_none_pairs():
    """None values in either series are excluded; if < 10 remain → None."""
    xs = [1.0, None, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    ys = [1.0, 2.0,  None, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    # Only 8 clean pairs → below MIN_DATA_POINTS=10 → None
    r = compute_pearson_r(xs, ys)
    assert r is None


def test_compute_pearson_r_constant_input_returns_none():
    """statistics.correlation raises StatisticsError for constant input — must return None."""
    xs = [5.0] * 10
    ys = [float(i) for i in range(10)]
    r = compute_pearson_r(xs, ys)
    assert r is None


# ── build_incident_similarity_vector ─────────────────────────────────────────

def test_build_incident_similarity_vector_length():
    alerts = [_alert(0), _alert(5)]
    sig = build_incident_similarity_vector(alerts)
    assert len(sig) == 32


def test_build_incident_similarity_vector_returns_floats():
    alerts = [_alert(0)]
    sig = build_incident_similarity_vector(alerts)
    assert all(isinstance(v, float) for v in sig)


def test_build_incident_similarity_vector_duration_at_index_29():
    """Index 29 = incident duration in minutes."""
    alerts = [_alert(0), _alert(10)]
    sig = build_incident_similarity_vector(alerts)
    assert sig[29] == pytest.approx(10.0)


def test_build_incident_similarity_vector_confidence_at_index_30():
    """Index 30 = mean confidence of all alerts."""
    a1 = _alert(0)
    a1["confidence"] = 0.6
    a2 = _alert(5)
    a2["confidence"] = 0.8
    sig = build_incident_similarity_vector([a1, a2])
    assert sig[30] == pytest.approx(0.7)


def test_build_incident_similarity_vector_tod_bucket_at_index_31():
    """Index 31 = time-of-day bucket: base = 12:00 → afternoon → bucket 2."""
    sig = build_incident_similarity_vector([_alert(0)])
    assert sig[31] == pytest.approx(2.0)


def test_build_incident_similarity_vector_method_onehot():
    """Index 21 = ml method (0-based from index 20)."""
    ml_alert = _alert(0, event_type="anomaly_combustion_signature")
    sig = build_incident_similarity_vector([ml_alert])
    # _METHOD_ORDER = ["threshold", "ml", "fingerprint", "summary", "statistical"]
    # ml is index 1, so vec[20+1] = vec[21] should be 1.0
    assert sig[21] == pytest.approx(1.0)


def test_build_incident_similarity_vector_severity_critical_at_28():
    """Critical severity sets index 28."""
    alert = _alert(0, severity="critical")
    sig = build_incident_similarity_vector([alert])
    assert sig[28] == pytest.approx(1.0)
    assert sig[27] == pytest.approx(0.0)
    assert sig[26] == pytest.approx(0.0)


def test_build_incident_similarity_vector_empty_alerts():
    sig = build_incident_similarity_vector([])
    assert len(sig) == 32
    assert all(v == 0.0 for v in sig)


# ── cosine_similarity ─────────────────────────────────────────────────────────

def test_cosine_similarity_identical():
    vec = [1.0, 0.0, 0.0, 1.0]
    assert cosine_similarity(vec, vec) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_zero_vector():
    """Zero vectors return 0.0 without crashing."""
    a = [0.0, 0.0]
    b = [1.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_mismatched_lengths_returns_zero():
    """Mismatched vector lengths return 0.0 (guard added after review)."""
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_empty_returns_zero():
    assert cosine_similarity([], []) == pytest.approx(0.0)


# ── generate_incident_title ───────────────────────────────────────────────────

def test_generate_incident_title_critical():
    alerts = [_alert(0, severity="critical")]
    title = generate_incident_title(alerts)
    assert "Critical" in title


def test_generate_incident_title_uses_highest_severity():
    alerts = [_alert(0, severity="info"), _alert(5, severity="critical")]
    title = generate_incident_title(alerts)
    assert "Critical" in title


def test_generate_incident_title_non_empty():
    title = generate_incident_title([_alert(0)])
    assert len(title) > 0


def test_generate_incident_title_missing_title_key_no_crash():
    """Safe fallback when alert dict has no 'title' key."""
    alert = {"id": 0, "created_at": _ts(0).isoformat(),
             "event_type": "tvoc_spike", "severity": "info", "confidence": 0.8}
    title = generate_incident_title([alert])
    assert isinstance(title, str)
    assert len(title) > 0


def test_generate_incident_title_empty_list():
    assert generate_incident_title([]) == "Unknown Incident"
