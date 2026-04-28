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
    detection_method,
    CROSS_INCIDENT_TYPES,
    make_incident_id,
    compute_pearson_r,
    build_incident_similarity_vector,
    cosine_similarity,
    generate_incident_title,
    connected_components,
    incident_confidence,
    _load_split_markers,
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


def _make_alert(alert_id, ts, deps=()):
    """Build an alert dict with the signal_deps shape the grouper expects.

    deps: iterable of (sensor, r) pairs.
    """
    return {
        "id": alert_id,
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
    # Seed two primary alerts spanning a 20-minute window.
    _seed_inf_with_dep(tmp_db, "2026-04-19 12:00:00", "tvoc_ppb", 0.8,
                       event_type="tvoc_spike")
    _seed_inf_with_dep(tmp_db, "2026-04-19 12:20:00", "tvoc_ppb", 0.7,
                       event_type="tvoc_spike")
    # Seed a cross-incident (hourly_summary) alert at 12:10, inside the window.
    _seed_inference(tmp_db, "2026-04-19 12:10:00", event_type="hourly_summary")
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    # The cross-incident alert must be linked with is_primary=0.
    row = conn.execute(
        "SELECT is_primary FROM incident_alerts WHERE is_primary = 0"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 0



def test_regroup_all_idempotent(tmp_db):
    _seed_inference(tmp_db, "2026-04-19 12:00:00")
    regroup_all(tmp_db)
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    conn.close()
    assert count == 1


def test_regroup_all_max_severity_critical(tmp_db):
    # Use _seed_inf_with_dep so both alerts share a sensor and form one incident.
    _seed_inf_with_dep(tmp_db, "2026-04-19 12:00:00", "tvoc_ppb", 0.8,
                       event_type="tvoc_spike")
    conn = sqlite3.connect(tmp_db)
    cur = conn.execute(
        "INSERT INTO inferences (created_at, event_type, severity, title, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        ("2026-04-19 12:05:00", "tvoc_spike", "critical", "alert-crit", 0.9),
    )
    critical_id = cur.lastrowid
    conn.execute(
        "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) "
        "VALUES (?, ?, ?, ?)",
        (critical_id, "tvoc_ppb", 0.8, 0),
    )
    conn.commit()
    conn.close()
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


# ── Causal-DAG integration tests (regroup_all) ───────────────────────────────


def _seed_inf_with_dep(db_path, ts, sensor, r, event_type="tvoc_spike"):
    """Seed one inference + its alert_signal_deps row in one call."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO inferences (created_at, event_type, severity, title, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        (ts, event_type, "info", f"alert-{ts}", 0.9),
    )
    alert_id = cur.lastrowid
    conn.execute(
        "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) "
        "VALUES (?, ?, ?, ?)",
        (alert_id, sensor, r, 0),
    )
    conn.commit()
    conn.close()
    return alert_id


def test_regroup_all_causal_groups_shared_sensor(tmp_db):
    """Two alerts 15 min apart sharing eCO2 with matching sign => one incident."""
    _seed_inf_with_dep(tmp_db, "2026-04-23 09:00:00", "eco2_ppm", 0.8)
    _seed_inf_with_dep(tmp_db, "2026-04-23 09:15:00", "eco2_ppm", 0.7)
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    conn.close()
    assert count == 1


def test_regroup_all_causal_splits_disjoint_sensors(tmp_db):
    """Two alerts 10 min apart with DISJOINT strong sensors => two incidents."""
    _seed_inf_with_dep(tmp_db, "2026-04-23 09:00:00", "eco2_ppm", 0.8)
    _seed_inf_with_dep(tmp_db, "2026-04-23 09:10:00", "pm25_ug_m3", 0.8)
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    conn.close()
    assert count == 2


def test_regroup_all_causal_transitive_chain(tmp_db):
    """A-B and B-C share sensors; A-C don't. All three become one incident."""
    _seed_inf_with_dep(tmp_db, "2026-04-23 09:00:00", "eco2_ppm",  0.8)  # A
    a_id = _seed_inf_with_dep(tmp_db, "2026-04-23 09:10:00", "eco2_ppm",  0.7)  # A2 (bridge 1)
    # For the "bridge" B, give it BOTH eco2 and tvoc so it shares with A on eco2 and with C on tvoc.
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) "
        "VALUES (?, ?, ?, ?)",
        (a_id, "tvoc_ppb", 0.8, 0),
    )
    conn.commit()
    conn.close()
    _seed_inf_with_dep(tmp_db, "2026-04-23 09:25:00", "tvoc_ppb", 0.7)  # C
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    conn.close()
    assert count == 1


def test_regroup_all_split_marker_breaks_chain(tmp_db):
    """An incident_splits row on an alert breaks the chain at that alert.

    Setup: A shares eco2_ppm with B (the split alert); B shares tvoc_ppb with C.
    A and C have disjoint sensors, so the only path A→C goes through B.
    Without a split, B acts as a bridge: A+B+C = one incident.
    After putting a split marker on B, edges INTO B are suppressed (A→B
    is gone), breaking the bridge. A becomes isolated; B+C stay linked.
    => 2 incidents.
    """
    _a_id = _seed_inf_with_dep(tmp_db, "2026-04-23 09:00:00", "eco2_ppm", 0.8)
    # B bridges A (eco2_ppm) and C (tvoc_ppb).
    conn = sqlite3.connect(tmp_db)
    cur = conn.execute(
        "INSERT INTO inferences (created_at, event_type, severity, title, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        ("2026-04-23 09:10:00", "tvoc_spike", "info", "bridge-B", 0.9),
    )
    b_id = cur.lastrowid
    conn.execute(
        "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) VALUES (?, ?, ?, ?)",
        (b_id, "eco2_ppm", 0.7, 0),
    )
    conn.execute(
        "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) VALUES (?, ?, ?, ?)",
        (b_id, "tvoc_ppb", 0.7, 0),
    )
    conn.commit()
    conn.close()
    _c_id = _seed_inf_with_dep(tmp_db, "2026-04-23 09:20:00", "tvoc_ppb", 0.8)

    # Without a split: A+B+C bridge => one incident.
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1
    conn.close()

    # Add a split marker on B ("break chain before B").
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        "INSERT INTO incident_splits (alert_id, created_by) VALUES (?, ?)",
        (b_id, "test"),
    )
    conn.commit()
    conn.close()

    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    conn.close()
    assert count == 2


def test_regroup_all_persists_confidence(tmp_db):
    """incidents.confidence stores min edge P (or 1.0 for singletons)."""
    _seed_inf_with_dep(tmp_db, "2026-04-23 09:00:00", "eco2_ppm", 0.8)
    # Gap of 135 min => P = 0.5
    _seed_inf_with_dep(tmp_db, "2026-04-23 11:15:00", "eco2_ppm", 0.7)
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    conf = conn.execute("SELECT confidence FROM incidents").fetchone()[0]
    conn.close()
    assert abs(conf - 0.5) < 0.001


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


# ── build_edges ──────────────────────────────────────────────────────────────

from mlss_monitor.incident_grouper import build_edges, MIN_EDGE_P_SERVER


def test_build_edges_empty_input():
    assert build_edges([], split_marker_ids=set()) == []


def test_build_edges_single_alert_no_edges():
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    assert build_edges([a], split_marker_ids=set()) == []


def test_build_edges_basic_pair():
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 09:10:00", [("eco2_ppm", 0.7)])
    edges = build_edges([a, b], split_marker_ids=set())
    assert len(edges) == 1
    src, dst, p = edges[0]
    assert src == 1 and dst == 2
    assert p == 1.0


def test_build_edges_drops_below_server_floor():
    """Edges with P < MIN_EDGE_P_SERVER are not returned."""
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    # Gap of 235 minutes => P = (240-235)/210 ≈ 0.024 < 0.05 floor
    b = _make_alert(2, "2026-04-23 12:55:00", [("eco2_ppm", 0.8)])
    edges = build_edges([a, b], split_marker_ids=set())
    assert edges == []


def test_build_edges_directed_by_created_at():
    """src has the earlier created_at, dst has the later."""
    a = _make_alert(1, "2026-04-23 09:10:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    edges = build_edges([a, b], split_marker_ids=set())
    assert len(edges) == 1
    src, dst, _ = edges[0]
    assert src == 2 and dst == 1


def test_build_edges_respects_split_marker():
    """A split-marker on B means any edge A→B where A is earlier than B
    is suppressed."""
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 09:10:00", [("eco2_ppm", 0.7)])
    edges = build_edges([a, b], split_marker_ids={2})
    assert edges == []


def test_build_edges_split_marker_is_later_alert_only():
    """A split-marker on the EARLIER alert doesn't suppress the edge —
    only markers on the LATER alert do (the marker means 'break
    chain BEFORE this alert')."""
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 09:10:00", [("eco2_ppm", 0.7)])
    edges = build_edges([a, b], split_marker_ids={1})
    assert len(edges) == 1  # marker on a (earlier) does not affect A→B


def test_build_edges_all_pairs():
    """N(N-1)/2 edges for N fully-connected alerts."""
    alerts = [
        _make_alert(i, f"2026-04-23 09:{i:02d}:00", [("eco2_ppm", 0.8)])
        for i in range(4)
    ]
    edges = build_edges(alerts, split_marker_ids=set())
    assert len(edges) == 6  # 4C2


def test_min_edge_p_server_is_0_05():
    assert MIN_EDGE_P_SERVER == 0.05


# ── connected_components ─────────────────────────────────────────────────────

def _ids(components):
    """Return components as sorted lists of ids, for order-independent assertions."""
    return sorted([sorted([a["id"] for a in c]) for c in components])


def test_connected_components_empty():
    assert connected_components([], []) == []


def test_connected_components_single_alert_singleton():
    alerts = [_make_alert(1, "2026-04-23 09:00:00")]
    components = connected_components(alerts, edges=[])
    assert _ids(components) == [[1]]


def test_connected_components_no_edges_all_singletons():
    alerts = [_make_alert(i, f"2026-04-23 09:{i:02d}:00") for i in range(3)]
    components = connected_components(alerts, edges=[])
    assert _ids(components) == [[0], [1], [2]]


def test_connected_components_one_edge_one_component():
    alerts = [
        _make_alert(1, "2026-04-23 09:00:00"),
        _make_alert(2, "2026-04-23 09:10:00"),
    ]
    components = connected_components(alerts, edges=[(1, 2, 0.9)])
    assert _ids(components) == [[1, 2]]


def test_connected_components_transitive_chain():
    """A→B and B→C but no A↔C edge. All three should be one component."""
    alerts = [
        _make_alert(1, "2026-04-23 09:00:00"),
        _make_alert(2, "2026-04-23 09:15:00"),
        _make_alert(3, "2026-04-23 09:30:00"),
    ]
    edges = [(1, 2, 0.8), (2, 3, 0.7)]
    components = connected_components(alerts, edges)
    assert _ids(components) == [[1, 2, 3]]


def test_connected_components_two_disjoint_subgraphs():
    alerts = [_make_alert(i, f"2026-04-23 09:{i:02d}:00") for i in range(1, 5)]
    # Edges {1-2} and {3-4}; 1 and 3 never connect.
    edges = [(1, 2, 0.9), (3, 4, 0.9)]
    components = connected_components(alerts, edges)
    assert _ids(components) == [[1, 2], [3, 4]]


def test_connected_components_returns_alert_dicts_not_ids():
    """Components are lists of the original alert dicts (not just ids),
    so downstream code can read created_at, severity, etc. without
    re-looking-up."""
    a1 = _make_alert(1, "2026-04-23 09:00:00")
    a2 = _make_alert(2, "2026-04-23 09:10:00")
    components = connected_components([a1, a2], edges=[(1, 2, 0.9)])
    assert len(components) == 1
    # Same object identity — we pass through the dicts.
    assert set(id(a) for a in components[0]) == {id(a1), id(a2)}


# ── incident_confidence ──────────────────────────────────────────────────────

def test_incident_confidence_singleton_is_one():
    """No edges inside the component => max confidence (nothing to doubt)."""
    assert incident_confidence(edges_in_component=[]) == 1.0


def test_incident_confidence_single_edge():
    assert incident_confidence(edges_in_component=[(1, 2, 0.72)]) == 0.72


def test_incident_confidence_min_over_edges():
    """Weakest link sets the confidence."""
    edges = [(1, 2, 0.9), (2, 3, 0.31), (3, 4, 0.65)]
    assert incident_confidence(edges) == 0.31


def test_incident_confidence_ignores_edges_order():
    edges = [(3, 4, 0.65), (1, 2, 0.9), (2, 3, 0.31)]
    assert incident_confidence(edges) == 0.31


def test_load_split_markers_empty(tmp_db):
    assert _load_split_markers(tmp_db) == set()


def test_load_split_markers_returns_ids(tmp_db):
    conn = sqlite3.connect(tmp_db)
    for alert_id in (101, 202, 303):
        # Parent inference row so FK is valid.
        conn.execute(
            "INSERT INTO inferences (id, created_at, event_type, severity, "
            "title, confidence) VALUES (?, ?, ?, ?, ?, ?)",
            (alert_id, "2026-04-23 09:00:00", "tvoc_spike",
             "info", "t", 0.9),
        )
        conn.execute(
            "INSERT INTO incident_splits (alert_id, created_by) VALUES (?, ?)",
            (alert_id, "test-user"),
        )
    conn.commit()
    conn.close()

    assert _load_split_markers(tmp_db) == {101, 202, 303}


# ── temporal_edge_probability ────────────────────────────────────────────────

def test_temporal_edge_probability_ignores_sensor_gate():
    """temporal_edge_probability must NOT short-circuit on missing shared
    sensors — that's the entire point of the helper."""
    from mlss_monitor.incident_grouper import temporal_edge_probability
    a = {"created_at": "2026-04-26T15:27:22.707337", "signal_deps": []}
    b = {"created_at": "2026-04-26T15:27:22.787104", "signal_deps": []}
    p = temporal_edge_probability(a, b)
    assert p == 1.0  # within 30 min window, no sensor overlap required


def test_edge_probability_still_requires_sensor_overlap():
    """edge_probability (used by the grouper) MUST still return 0 when
    no shared sensor — the strict gate is preserved."""
    from mlss_monitor.incident_grouper import edge_probability
    a = {"created_at": "2026-04-26T15:27:22.707337", "signal_deps": []}
    b = {"created_at": "2026-04-26T15:27:22.787104", "signal_deps": []}
    p = edge_probability(a, b)
    assert p == 0.0  # no shared sensors → 0 regardless of time
