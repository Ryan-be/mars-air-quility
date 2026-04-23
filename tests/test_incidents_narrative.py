"""Unit tests for mlss_monitor.incidents_narrative."""
from mlss_monitor.incidents_narrative import build_narrative


def _alert(**over):
    base = {
        "id": 1,
        "created_at": "2026-04-23 09:28:00",
        "event_type": "eco2_elevated",
        "severity": "warning",
        "title": "CO\u2082 elevated",
        "description": "CO\u2082 at 994 ppm",
        "confidence": 0.92,
        "detection_method": "threshold",
        "is_primary": 1,
        "signal_deps": [],
    }
    base.update(over)
    return base


def test_observed_references_first_alert_and_duration():
    inc = {"id": "INC-1", "started_at": "2026-04-23 09:28:00", "ended_at": "2026-04-23 10:00:00"}
    alerts = [_alert()]
    out = build_narrative(inc, alerts)
    assert "09:28" in out["observed"]
    assert "32 min" in out["observed"] or "32min" in out["observed"]


def test_inferred_mentions_specific_event_types_not_placeholder():
    inc = {"id": "INC-1", "started_at": "2026-04-23 09:28:00", "ended_at": "2026-04-23 10:00:00"}
    alerts = [
        _alert(id=1, event_type="eco2_elevated", title="CO\u2082 elevated — 994 ppm"),
        _alert(id=2, event_type="anomaly_tvoc_ppb", created_at="2026-04-23 09:36:00",
               title="Anomaly: TVOC", severity="info", detection_method="ml"),
    ]
    out = build_narrative(inc, alerts)
    # Should mention CO2 by name (not generic "N events")
    assert "CO" in out["inferred"] or "994" in out["inferred"] or "TVOC" in out["inferred"]
    # Should NOT use the template wording
    assert "Dominant detection type" not in out["inferred"]


def test_inferred_mentions_time_gap_between_events():
    inc = {"id": "INC-1", "started_at": "2026-04-23 09:28:00", "ended_at": "2026-04-23 10:00:00"}
    alerts = [
        _alert(id=1, created_at="2026-04-23 09:28:00", event_type="eco2_elevated",
               title="CO\u2082 elevated"),
        _alert(id=2, created_at="2026-04-23 09:36:00", event_type="anomaly_tvoc_ppb",
               title="TVOC anomaly"),
    ]
    out = build_narrative(inc, alerts)
    # 8 minutes between the two events
    assert "8 min" in out["inferred"] or "8min" in out["inferred"]


def test_impact_reflects_max_severity():
    inc = {"id": "INC-1", "started_at": "2026-04-23 09:28:00", "ended_at": "2026-04-23 10:00:00"}
    alerts = [_alert(severity="critical")]
    out = build_narrative(inc, alerts)
    assert out["impact"] != ""
    assert "critical" in out["impact"].lower() or "immediate" in out["impact"].lower()


def test_empty_alerts_returns_safe_strings():
    inc = {"id": "INC-EMPTY", "started_at": "", "ended_at": ""}
    out = build_narrative(inc, [])
    assert isinstance(out["observed"], str)
    assert isinstance(out["inferred"], str)
    assert isinstance(out["impact"], str)


def test_primary_alerts_only_in_prose():
    """Cross-incident alerts (is_primary=0) should not dominate the narrative."""
    inc = {"id": "INC-1", "started_at": "2026-04-23 09:28:00", "ended_at": "2026-04-23 10:00:00"}
    alerts = [
        _alert(id=1, event_type="eco2_elevated", title="CO\u2082 elevated", is_primary=1),
        _alert(id=2, event_type="hourly_summary", title="Hourly summary", is_primary=0),
    ]
    out = build_narrative(inc, alerts)
    assert "CO" in out["inferred"] or "994" in out["inferred"]
    assert "hourly" not in out["inferred"].lower()


# ── Correlation explanation ──────────────────────────────────────────────

def test_correlation_field_is_present():
    inc = {"id": "INC-1", "started_at": "2026-04-23 09:28:00", "ended_at": "2026-04-23 10:00:00"}
    out = build_narrative(inc, [_alert()])
    assert "correlation" in out
    assert isinstance(out["correlation"], str)


def test_correlation_names_dominant_sensor():
    """If every primary alert strongly correlates with eCO2, say so."""
    inc = {"id": "INC-1", "started_at": "2026-04-23 09:28:00", "ended_at": "2026-04-23 10:00:00"}
    deps_eco2 = [{"sensor": "eco2_ppm", "r": 0.85, "lag_seconds": 0}]
    alerts = [
        _alert(id=1, title="CO\u2082 elevated", signal_deps=deps_eco2),
        _alert(id=2, created_at="2026-04-23 09:36:00", title="CO\u2082 dangerously high",
               signal_deps=deps_eco2),
        _alert(id=3, created_at="2026-04-23 09:42:00", title="CO\u2082 elevated",
               signal_deps=deps_eco2),
    ]
    out = build_narrative(inc, alerts)
    low = out["correlation"].lower()
    # Should name eCO2 (or its sensor key) as the link
    assert "eco2" in low or "co" in low


def test_correlation_mentions_cross_sensor_co_movement():
    """Two distinct strong sensors => mention both as a linked pair."""
    inc = {"id": "INC-1", "started_at": "2026-04-23 09:28:00", "ended_at": "2026-04-23 10:00:00"}
    deps_both = [
        {"sensor": "tvoc_ppb", "r": 0.78, "lag_seconds": 0},
        {"sensor": "eco2_ppm", "r": 0.82, "lag_seconds": 0},
    ]
    alerts = [
        _alert(id=1, title="TVOC spike", signal_deps=deps_both),
        _alert(id=2, created_at="2026-04-23 09:36:00", title="CO\u2082 elevated",
               signal_deps=deps_both),
        _alert(id=3, created_at="2026-04-23 09:42:00", title="CO\u2082 dangerously high",
               signal_deps=deps_both),
    ]
    out = build_narrative(inc, alerts)
    low = out["correlation"].lower()
    # Both sensors named
    assert "tvoc" in low
    assert "eco2" in low or "co" in low


def test_correlation_mentions_severity_escalation():
    """Narrative notes when severity escalates info -> warning -> critical."""
    inc = {"id": "INC-1", "started_at": "2026-04-23 09:28:00", "ended_at": "2026-04-23 10:00:00"}
    alerts = [
        _alert(id=1, severity="info",    created_at="2026-04-23 09:28:00"),
        _alert(id=2, severity="warning", created_at="2026-04-23 09:36:00"),
        _alert(id=3, severity="critical", created_at="2026-04-23 09:50:00"),
    ]
    out = build_narrative(inc, alerts)
    assert "escalat" in out["correlation"].lower()


def test_correlation_fallback_when_no_signal_deps():
    """When no signal_deps exist (empty or all None/weak), still return a sane string."""
    inc = {"id": "INC-1", "started_at": "2026-04-23 09:28:00", "ended_at": "2026-04-23 10:00:00"}
    alerts = [
        _alert(id=1, signal_deps=[]),
        _alert(id=2, created_at="2026-04-23 09:36:00", signal_deps=[{"sensor": "eco2_ppm", "r": None, "lag_seconds": 0}]),
    ]
    out = build_narrative(inc, alerts)
    assert out["correlation"] != ""
    # Should not contain any sensor name since nothing was strong
    low = out["correlation"].lower()
    assert "temporal" in low or "cluster" in low or "no " in low


def test_correlation_ignores_weak_r():
    """|r| < 0.5 should not count as a correlation signal."""
    inc = {"id": "INC-1", "started_at": "2026-04-23 09:28:00", "ended_at": "2026-04-23 10:00:00"}
    alerts = [
        _alert(id=1, signal_deps=[{"sensor": "humidity_pct", "r": 0.2, "lag_seconds": 0}]),
        _alert(id=2, created_at="2026-04-23 09:36:00",
               signal_deps=[{"sensor": "humidity_pct", "r": 0.25, "lag_seconds": 0}]),
    ]
    out = build_narrative(inc, alerts)
    assert "humidity" not in out["correlation"].lower()
