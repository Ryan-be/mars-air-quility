"""Tests for mlss_monitor/narrative_engine.py — pure analysis functions."""
import pytest
from mlss_monitor.narrative_engine import (
    compute_longest_clean_period,
    compute_pattern_heatmap,
    detect_drift_flags,
    compute_trend_indicators,
    generate_period_summary,
    generate_fingerprint_narrative,
    generate_anomaly_model_narrative,
)

# ---------------------------------------------------------------------------
# compute_longest_clean_period
# ---------------------------------------------------------------------------

def test_longest_clean_period_no_events():
    result = compute_longest_clean_period(
        inferences=[],
        window_start="2026-04-04T00:00:00Z",
        window_end="2026-04-04T24:00:00Z",
    )
    assert result["hours"] == pytest.approx(24.0, abs=0.1)
    assert result["start"] == "2026-04-04T00:00:00Z"
    assert result["end"] == "2026-04-05T00:00:00Z"


def test_longest_clean_period_single_event_in_middle():
    result = compute_longest_clean_period(
        inferences=[{"created_at": "2026-04-04T06:00:00Z"}],
        window_start="2026-04-04T00:00:00Z",
        window_end="2026-04-04T24:00:00Z",
    )
    # Gap before event: 6h; gap after event: 18h → longest is 18h
    assert result["hours"] == pytest.approx(18.0, abs=0.1)


def test_longest_clean_period_multiple_events():
    inferences = [
        {"created_at": "2026-04-04T02:00:00Z"},
        {"created_at": "2026-04-04T04:00:00Z"},
        {"created_at": "2026-04-04T20:00:00Z"},
    ]
    result = compute_longest_clean_period(
        inferences=inferences,
        window_start="2026-04-04T00:00:00Z",
        window_end="2026-04-04T24:00:00Z",
    )
    # Gaps: 2h, 2h, 16h → longest is 16h
    assert result["hours"] == pytest.approx(16.0, abs=0.1)


# ---------------------------------------------------------------------------
# compute_pattern_heatmap
# ---------------------------------------------------------------------------

def test_pattern_heatmap_empty():
    assert compute_pattern_heatmap([]) == {}


def test_pattern_heatmap_counts_correctly():
    # Monday (weekday=0) at 18:00 UTC
    inferences = [
        {"created_at": "2026-04-06T18:00:00Z"},  # Monday
        {"created_at": "2026-04-06T18:30:00Z"},  # Monday same hour
        {"created_at": "2026-04-07T12:00:00Z"},  # Tuesday
    ]
    result = compute_pattern_heatmap(inferences)
    assert result.get("0_18") == 2
    assert result.get("1_12") == 1
    assert "0_19" not in result  # no events at 19:00


# ---------------------------------------------------------------------------
# detect_drift_flags
# ---------------------------------------------------------------------------

def test_drift_flags_empty_when_no_drift():
    flags = detect_drift_flags(
        baselines_now={"tvoc_ppb": 100.0},
        baselines_7d_ago={"tvoc_ppb": 102.0},
    )
    assert flags == []


def test_drift_flags_detects_significant_shift():
    flags = detect_drift_flags(
        baselines_now={"co_ppb": 15000.0},
        baselines_7d_ago={"co_ppb": 12000.0},
    )
    assert len(flags) == 1
    assert flags[0]["channel"] == "co_ppb"
    assert flags[0]["direction"] == "up"
    assert flags[0]["shift_pct"] == pytest.approx(25.0, abs=0.1)
    assert "message" in flags[0]
    assert len(flags[0]["message"]) > 10


def test_drift_flags_skips_none_baseline():
    flags = detect_drift_flags(
        baselines_now={"tvoc_ppb": 100.0},
        baselines_7d_ago={"tvoc_ppb": None},
    )
    assert flags == []


def test_drift_flags_downward():
    flags = detect_drift_flags(
        baselines_now={"tvoc_ppb": 80.0},
        baselines_7d_ago={"tvoc_ppb": 100.0},
    )
    assert len(flags) == 1
    assert flags[0]["direction"] == "down"


# ---------------------------------------------------------------------------
# compute_trend_indicators
# ---------------------------------------------------------------------------

_DUMMY_META = {
    "tvoc_ppb": {"label": "TVOC", "unit": "ppb"},
    "eco2_ppm": {"label": "eCO2", "unit": "ppm"},
}


def test_trend_indicators_green_when_stable():
    indicators = compute_trend_indicators(
        baselines_now={"tvoc_ppb": 100.0},
        baselines_7d_ago={"tvoc_ppb": 98.0},
        channel_meta=_DUMMY_META,
    )
    assert len(indicators) == 1
    assert indicators[0]["colour"] == "green"
    assert indicators[0]["direction"] == "up"
    assert indicators[0]["pct_change"] == pytest.approx(2.04, abs=0.1)


def test_trend_indicators_amber_when_moderate():
    indicators = compute_trend_indicators(
        baselines_now={"tvoc_ppb": 115.0},
        baselines_7d_ago={"tvoc_ppb": 100.0},
        channel_meta=_DUMMY_META,
    )
    assert indicators[0]["colour"] == "amber"


def test_trend_indicators_red_when_large():
    indicators = compute_trend_indicators(
        baselines_now={"tvoc_ppb": 135.0},
        baselines_7d_ago={"tvoc_ppb": 100.0},
        channel_meta=_DUMMY_META,
    )
    assert indicators[0]["colour"] == "red"


def test_trend_indicators_skips_missing_channels():
    # eco2 not in baselines_now → should be omitted
    indicators = compute_trend_indicators(
        baselines_now={"tvoc_ppb": 100.0},
        baselines_7d_ago={"tvoc_ppb": 100.0, "eco2_ppm": 500.0},
        channel_meta=_DUMMY_META,
    )
    channels = [i["channel"] for i in indicators]
    assert "eco2_ppm" not in channels


# ---------------------------------------------------------------------------
# generate_period_summary
# ---------------------------------------------------------------------------

def test_period_summary_no_events():
    text = generate_period_summary(
        inferences=[],
        trend_indicators=[],
        dominant_source=None,
    )
    assert isinstance(text, str)
    assert len(text) > 20
    # Should convey "clean" or "no events"
    assert any(word in text.lower() for word in ("clean", "no event", "no detection"))


def test_period_summary_with_events_and_source():
    inferences = [{"severity": "warning"}, {"severity": "warning"}]
    text = generate_period_summary(
        inferences=inferences,
        trend_indicators=[{"colour": "green"}],
        dominant_source="cooking",
    )
    assert isinstance(text, str)
    assert len(text) > 20


# ---------------------------------------------------------------------------
# generate_fingerprint_narrative
# ---------------------------------------------------------------------------

def test_fingerprint_narrative_zero_events():
    text = generate_fingerprint_narrative(
        source_id="cooking",
        label="Cooking",
        events=[],
        avg_confidence=0.0,
        typical_hours=[],
    )
    assert "Cooking" in text
    assert "no" in text.lower() or "not detected" in text.lower() or "0" in text


def test_fingerprint_narrative_with_events():
    events = [{"id": 1}, {"id": 2}, {"id": 3}]
    text = generate_fingerprint_narrative(
        source_id="cooking",
        label="Cooking",
        events=events,
        avg_confidence=0.71,
        typical_hours=[12, 13, 18, 19],
    )
    assert "3" in text or "three" in text.lower()
    assert isinstance(text, str)
    assert len(text) > 30


def test_fingerprint_narrative_includes_advice():
    text = generate_fingerprint_narrative(
        source_id="combustion",
        label="Combustion",
        events=[{"id": 1}],
        avg_confidence=0.80,
        typical_hours=[19],
    )
    # Should contain actionable advice (non-empty)
    assert len(text) > 40


# ---------------------------------------------------------------------------
# generate_anomaly_model_narrative
# ---------------------------------------------------------------------------

def test_anomaly_model_narrative():
    text = generate_anomaly_model_narrative(
        model_id="combustion_signature",
        label="Combustion Signature",
        event_count=2,
        description="Watches CO, NO2, PM2.5 and PM10 for co-rises consistent with combustion.",
    )
    assert "Combustion Signature" in text or "combustion" in text.lower()
    assert "2" in text
    assert isinstance(text, str)
    assert len(text) > 30


def test_period_summary_single_event():
    text = generate_period_summary(
        inferences=[{"severity": "warning"}],
        trend_indicators=[],
        dominant_source=None,
    )
    assert isinstance(text, str)
    assert len(text) > 20
    # Should mention "one" or "1"
    assert "one" in text.lower() or "1" in text


def test_anomaly_model_narrative_singular():
    text = generate_anomaly_model_narrative(
        model_id="thermal_moisture",
        label="Thermal-Moisture Stress",
        event_count=1,
        description="Watches temperature, humidity and VPD together.",
    )
    assert "1 time" in text
    assert "times" not in text
