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
