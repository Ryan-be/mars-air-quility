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
    merge_similar_adjacent,
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


# ── DB persistence (regroup_all) ─────────────────────────────────────────────

import sqlite3
import database.init_db as dbi
import database.db_logger as dbl
import database.user_db as udb
import mlss_monitor.hot_tier as ht
from mlss_monitor.incident_grouper import regroup_all


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    dbi.DB_FILE = db_path
    dbl.DB_FILE = db_path
    udb.DB_FILE = db_path
    ht.DB_FILE = db_path
    dbi.create_db()
    yield db_path
    dbi.DB_FILE = "data/sensor_data.db"
    dbl.DB_FILE = "data/sensor_data.db"
    udb.DB_FILE = "data/sensor_data.db"
    ht.DB_FILE = "data/sensor_data.db"


def _seed_inference(db_path, created_at, event_type="tvoc_spike",
                    severity="info", confidence=0.8):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO inferences (created_at, event_type, severity, title, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        (created_at, event_type, severity, f"Alert {event_type}", confidence)
    )
    conn.commit()
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return row_id


# ── edge_probability ────────────────────────────────────────────────────────────

from mlss_monitor.incident_grouper import edge_probability


def _make_alert(id, ts, deps=()):
    """Build an alert dict with the signal_deps shape the grouper expects.

    deps: iterable of (sensor, r) pairs.
    """
    return {
        "id": id,
        "created_at": ts,
        "signal_deps": [
            {"sensor": s, "r": r, "lag_seconds": 0} for s, r in deps
        ],
    }


def test_edge_probability_zero_when_no_shared_sensor():
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 09:05:00", [("tvoc_ppb", 0.8)])
    assert edge_probability(a, b) == 0.0


def test_edge_probability_zero_when_shared_sensor_signs_differ():
    """Rising eCO2 alert (positive r) and falling eCO2 alert (negative r)
    should NOT link — they're physically opposite events."""
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm",  0.8)])
    b = _make_alert(2, "2026-04-23 09:05:00", [("eco2_ppm", -0.8)])
    assert edge_probability(a, b) == 0.0


def test_edge_probability_zero_when_r_below_threshold():
    """|r| must be >= 0.5 for a sensor to count as 'strongly involved'."""
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.45)])
    b = _make_alert(2, "2026-04-23 09:05:00", [("eco2_ppm", 0.8)])
    assert edge_probability(a, b) == 0.0


def test_edge_probability_full_at_zero_gap():
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 09:00:00", [("eco2_ppm", 0.9)])
    assert edge_probability(a, b) == 1.0


def test_edge_probability_full_at_30_minute_gap():
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 09:30:00", [("eco2_ppm", 0.9)])
    assert edge_probability(a, b) == 1.0


def test_edge_probability_decays_linearly_between_30_and_240():
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    # Gap of 135 minutes => halfway between 30 and 240 => P = 0.5
    b = _make_alert(2, "2026-04-23 11:15:00", [("eco2_ppm", 0.9)])
    assert abs(edge_probability(a, b) - 0.5) < 0.001


def test_edge_probability_zero_at_and_beyond_240_minutes():
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 13:00:00", [("eco2_ppm", 0.9)])  # 4h
    assert edge_probability(a, b) == 0.0
    c = _make_alert(3, "2026-04-23 14:00:00", [("eco2_ppm", 0.9)])  # 5h
    assert edge_probability(a, c) == 0.0


def test_edge_probability_symmetric_in_order():
    """Gap is abs — order of arguments doesn't matter."""
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 10:00:00", [("eco2_ppm", 0.9)])
    assert edge_probability(a, b) == edge_probability(b, a)


def test_edge_probability_handles_negative_r_matching():
    """Two falling-eCO2 alerts (both negative r) DO link."""
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", -0.7)])
    b = _make_alert(2, "2026-04-23 09:10:00", [("eco2_ppm", -0.6)])
    assert edge_probability(a, b) == 1.0


def test_edge_probability_handles_null_r_in_deps():
    """signal_deps rows with r=None are skipped (pre-Pearson data)."""
    a = _make_alert(1, "2026-04-23 09:00:00",
                    [("eco2_ppm", None), ("tvoc_ppb", 0.8)])
    b = _make_alert(2, "2026-04-23 09:05:00",
                    [("tvoc_ppb", 0.7)])
    assert edge_probability(a, b) == 1.0


def test_regroup_all_creates_incident(tmp_db):
    _seed_inference(tmp_db, "2026-04-19 12:00:00")
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    rows = conn.execute("SELECT id FROM incidents").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0].startswith("INC-")


def test_regroup_all_links_alert_to_incident(tmp_db):
    _seed_inference(tmp_db, "2026-04-19 12:00:00")
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    links = conn.execute("SELECT incident_id, alert_id FROM incident_alerts").fetchall()
    conn.close()
    assert len(links) == 1


def test_regroup_all_cross_incident_alert_not_primary(tmp_db):
    _seed_inference(tmp_db, "2026-04-19 12:00:00", event_type="hourly_summary")
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT is_primary FROM incident_alerts").fetchone()
    conn.close()
    assert row[0] == 0


def test_regroup_all_two_groups_two_incidents(tmp_db):
    """Two sessions with NO event-type overlap stay as two incidents even
    when they're within the merge_similar_adjacent max gap (4 h)."""
    _seed_inference(tmp_db, "2026-04-19 12:00:00", event_type="tvoc_spike")
    _seed_inference(tmp_db, "2026-04-19 13:00:00", event_type="pm25_elevated")
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    conn.close()
    assert count == 2


def test_regroup_all_merges_similar_sessions(tmp_db):
    """Two sessions with identical event types within the merge window
    collapse into ONE incident (the similarity-aware-grouping case)."""
    _seed_inference(tmp_db, "2026-04-19 12:00:00", event_type="tvoc_spike")
    _seed_inference(tmp_db, "2026-04-19 13:00:00", event_type="tvoc_spike")
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    conn.close()
    assert count == 1


def test_regroup_all_idempotent(tmp_db):
    _seed_inference(tmp_db, "2026-04-19 12:00:00")
    regroup_all(tmp_db)
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    conn.close()
    assert count == 1


def test_regroup_all_max_severity_critical(tmp_db):
    _seed_inference(tmp_db, "2026-04-19 12:00:00", severity="info")
    _seed_inference(tmp_db, "2026-04-19 12:05:00", severity="critical")
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    sev = conn.execute("SELECT max_severity FROM incidents").fetchone()[0]
    conn.close()
    assert sev == "critical"


def test_regroup_all_incident_id_format(tmp_db):
    _seed_inference(tmp_db, "2026-04-19 12:55:00")
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    inc_id = conn.execute("SELECT id FROM incidents").fetchone()[0]
    conn.close()
    assert inc_id == "INC-20260419-1255"


def test_regroup_all_empty_db_no_crash(tmp_db):
    """regroup_all on empty inferences table should not crash."""
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    conn.close()
    assert count == 0


# ── Similarity-aware adjacent-group merging ──────────────────────────────


def _alert_with_type(ts, et, sev="warning"):
    return {"id": hash((ts, et)) & 0xffff, "created_at": ts,
            "event_type": et, "severity": sev}


def test_merge_similar_merges_same_event_type():
    """Two sessions 1h apart with identical event types → one merged session."""
    g1 = [_alert_with_type("2026-04-23 09:00:00", "eco2_elevated")]
    g2 = [_alert_with_type("2026-04-23 10:00:00", "eco2_elevated")]
    result = merge_similar_adjacent([g1, g2])
    assert len(result) == 1
    assert len(result[0]) == 2
    assert result[0][0]["created_at"] < result[0][1]["created_at"]


def test_merge_similar_keeps_dissimilar():
    """Different event types → stay separate even if adjacent in time."""
    g1 = [_alert_with_type("2026-04-23 09:00:00", "eco2_elevated")]
    g2 = [_alert_with_type("2026-04-23 10:00:00", "pm25_spike")]
    result = merge_similar_adjacent([g1, g2])
    assert len(result) == 2


def test_merge_similar_respects_max_gap():
    """Groups >4h apart stay separate even if event types match."""
    g1 = [_alert_with_type("2026-04-23 09:00:00", "eco2_elevated")]
    g2 = [_alert_with_type("2026-04-23 20:00:00", "eco2_elevated")]  # 11 h later
    result = merge_similar_adjacent([g1, g2])
    assert len(result) == 2


def test_merge_similar_transitive_chain():
    """A~B~C collapses into one group via chained adjacency."""
    g1 = [_alert_with_type("2026-04-23 09:00:00", "eco2_elevated")]
    g2 = [_alert_with_type("2026-04-23 10:00:00", "eco2_elevated")]
    g3 = [_alert_with_type("2026-04-23 11:00:00", "eco2_elevated")]
    result = merge_similar_adjacent([g1, g2, g3])
    assert len(result) == 1
    assert len(result[0]) == 3


def test_merge_similar_idempotent():
    """Running the merge twice produces the same output (monotonic)."""
    g1 = [_alert_with_type("2026-04-23 09:00:00", "eco2_elevated")]
    g2 = [_alert_with_type("2026-04-23 10:00:00", "eco2_elevated")]
    once = merge_similar_adjacent([g1, g2])
    twice = merge_similar_adjacent(once)
    assert once == twice


def test_merge_similar_empty_and_single():
    """Edge cases: empty input, single group — no-op."""
    assert merge_similar_adjacent([]) == []
    single = [[_alert_with_type("2026-04-23 09:00:00", "eco2_elevated")]]
    assert merge_similar_adjacent(single) == single


def test_merge_similar_partial_overlap_above_threshold():
    """Jaccard ≥ 0.3: 2 shared event types out of 4 unique = 0.5 → merged."""
    g1 = [_alert_with_type("2026-04-23 09:00:00", "eco2_elevated"),
          _alert_with_type("2026-04-23 09:05:00", "tvoc_elevated")]
    g2 = [_alert_with_type("2026-04-23 10:00:00", "eco2_elevated"),
          _alert_with_type("2026-04-23 10:05:00", "tvoc_elevated"),
          _alert_with_type("2026-04-23 10:10:00", "vpd_high")]
    result = merge_similar_adjacent([g1, g2])
    assert len(result) == 1


def test_merge_similar_low_overlap_stays_split():
    """Jaccard < 0.3: 1 shared out of 5 unique = 0.2 → stays split."""
    g1 = [_alert_with_type("2026-04-23 09:00:00", "eco2_elevated"),
          _alert_with_type("2026-04-23 09:05:00", "tvoc_elevated")]
    g2 = [_alert_with_type("2026-04-23 10:00:00", "pm25_spike"),
          _alert_with_type("2026-04-23 10:05:00", "co_elevated"),
          _alert_with_type("2026-04-23 10:10:00", "no2_elevated")]
    result = merge_similar_adjacent([g1, g2])
    assert len(result) == 2


# ── incident_splits table schema ────────────────────────────────────────

def test_incident_splits_table_created(tmp_db):
    """init_db.create_db() should create the incident_splits table."""
    conn = sqlite3.connect(tmp_db)
    row = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='incident_splits'"
    ).fetchone()
    conn.close()
    assert row is not None, "incident_splits table should exist after create_db()"


def test_incident_splits_columns(tmp_db):
    """incident_splits has alert_id PK, created_by, created_at columns."""
    conn = sqlite3.connect(tmp_db)
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(incident_splits)").fetchall()
    }
    conn.close()
    assert cols == {"alert_id", "created_by", "created_at"}
