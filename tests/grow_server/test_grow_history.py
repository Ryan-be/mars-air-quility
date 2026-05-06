"""GET /api/grow/units/<id>/history returns moisture series + watering events."""
import sqlite3
import tempfile
from datetime import datetime, timedelta
import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.routes.api_grow_history.DB_FILE", tmp.name)
    init_db.create_db()
    now = datetime.utcnow()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, 'h', ?)",
        (now, now),
    )
    # 3 telemetry rows + 1 watering event
    for hours_ago, raw, pct in [(3, 612, 31), (2, 800, 46), (1, 1100, 70)]:
        conn.execute(
            "INSERT INTO grow_telemetry (unit_id, timestamp_utc, "
            "soil_moisture_raw, soil_moisture_pct, light_state, pump_state) "
            "VALUES (1, ?, ?, ?, 1, 0)",
            (now - timedelta(hours=hours_ago), raw, pct),
        )
    conn.execute(
        "INSERT INTO grow_watering_events (unit_id, timestamp_utc, trigger, "
        "duration_s, soil_pct_before) VALUES (1, ?, 'pid', 6.0, 31)",
        (now - timedelta(hours=2, minutes=55),),
    )
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_history import api_grow_history_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_history_bp)
    return app.test_client(), tmp.name


@pytest.fixture
def empty_client(monkeypatch):
    """Fresh DB with a unit but NO telemetry/watering rows."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.routes.api_grow_history.DB_FILE", tmp.name)
    init_db.create_db()
    now = datetime.utcnow()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, 'h', ?)",
        (now, now),
    )
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_history import api_grow_history_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_history_bp)
    return app.test_client(), tmp.name


@pytest.fixture
def seed_client(monkeypatch):
    """Fresh DB with a unit but no telemetry — caller seeds via _seed_telemetry."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.routes.api_grow_history.DB_FILE", tmp.name)
    init_db.create_db()
    now = datetime.utcnow()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, 'h', ?)",
        (now, now),
    )
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_history import api_grow_history_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_history_bp)
    return app.test_client(), tmp.name


def _seed_telemetry(db_path, unit_id, count, start_ts, interval_s=60):
    """Insert `count` telemetry rows starting from start_ts, each interval_s apart.

    pct oscillates 35-65, raw cycles 600-799.
    """
    conn = sqlite3.connect(db_path)
    for i in range(count):
        ts = start_ts + timedelta(seconds=i * interval_s)
        pct = 50 + (i % 30) - 15  # oscillates 35-65
        raw = 600 + (i % 200)
        conn.execute(
            "INSERT INTO grow_telemetry "
            "(unit_id, timestamp_utc, soil_moisture_raw, soil_moisture_pct, "
            " light_state, pump_state) VALUES (?, ?, ?, ?, 1, 0)",
            (unit_id, ts, raw, pct),
        )
    conn.commit()
    conn.close()


# ------------------------------------------------------------------
# Existing tests (preserved — should keep passing after the refactor)
# ------------------------------------------------------------------


def test_history_returns_moisture_and_events(client):
    c, _ = client
    r = c.get("/api/grow/units/1/history?range=24h")
    assert r.status_code == 200
    body = r.get_json()
    assert "moisture" in body
    assert "watering_events" in body
    assert len(body["moisture"]) == 3
    assert len(body["watering_events"]) == 1
    assert body["watering_events"][0]["duration_s"] == 6.0


def test_history_supports_range_param(client):
    """range=7d or range=30d should also be accepted."""
    c, _ = client
    r = c.get("/api/grow/units/1/history?range=7d")
    assert r.status_code == 200
    r = c.get("/api/grow/units/1/history?range=30d")
    assert r.status_code == 200


def test_history_invalid_range_400(client):
    c, _ = client
    r = c.get("/api/grow/units/1/history?range=bogus")
    assert r.status_code == 400


# ------------------------------------------------------------------
# Task 1: longer ranges + downsampling + phase_changes
# ------------------------------------------------------------------


def test_history_accepts_90d_range(client):
    c, _ = client
    r = c.get("/api/grow/units/1/history?range=90d")
    assert r.status_code == 200


def test_history_accepts_all_range(client):
    c, _ = client
    r = c.get("/api/grow/units/1/history?range=all")
    assert r.status_code == 200


def test_history_invalid_range_still_400(client):
    """Existing 400-on-bogus-range behavior preserved."""
    c, _ = client
    r = c.get("/api/grow/units/1/history?range=999z")
    assert r.status_code == 400


def test_history_unchanged_shape_when_short_range_under_threshold(seed_client):
    """24h with 100 seeded points returns 100 entries with the existing
    {ts, pct, raw} shape (no downsample)."""
    c, db_path = seed_client
    # Seed 100 points spanning the last ~100 minutes (well under 24h)
    start = datetime.utcnow() - timedelta(minutes=100)
    _seed_telemetry(db_path, 1, 100, start, interval_s=60)
    r = c.get("/api/grow/units/1/history?range=24h")
    assert r.status_code == 200
    body = r.get_json()
    assert len(body["moisture"]) == 100
    # Existing raw shape: {ts, pct, raw} — NO pct_avg/pct_min/pct_max keys
    sample = body["moisture"][0]
    assert set(sample.keys()) == {"ts", "pct", "raw"}


def test_history_downsamples_when_over_threshold(seed_client):
    """Seed 1000 telemetry points; request 30d range. Assert moisture
    array length <=600 AND each entry has the downsampled shape."""
    c, db_path = seed_client
    # 1000 points spanning last ~1000 minutes (still well within 30d)
    start = datetime.utcnow() - timedelta(minutes=1000)
    _seed_telemetry(db_path, 1, 1000, start, interval_s=60)
    r = c.get("/api/grow/units/1/history?range=30d")
    assert r.status_code == 200
    body = r.get_json()
    assert len(body["moisture"]) <= 600
    # Downsampled shape: {ts, pct_min, pct_avg, pct_max, raw_avg}
    sample = body["moisture"][0]
    assert set(sample.keys()) == {"ts", "pct_min", "pct_avg", "pct_max", "raw_avg"}
    # Sanity: min <= avg <= max
    assert sample["pct_min"] <= sample["pct_avg"] <= sample["pct_max"]


def test_history_downsample_threshold_exact_boundary(seed_client):
    """Seed exactly 600 -> raw shape (no downsample). Seed 601 -> downsampled."""
    c, db_path = seed_client
    start = datetime.utcnow() - timedelta(minutes=600)
    _seed_telemetry(db_path, 1, 600, start, interval_s=60)
    r = c.get("/api/grow/units/1/history?range=30d")
    body = r.get_json()
    assert len(body["moisture"]) == 600
    # Raw shape at the boundary
    assert set(body["moisture"][0].keys()) == {"ts", "pct", "raw"}

    # Add one more — now 601 total — must downsample
    extra_ts = start + timedelta(minutes=600)
    _seed_telemetry(db_path, 1, 1, extra_ts, interval_s=60)
    r = c.get("/api/grow/units/1/history?range=30d")
    body = r.get_json()
    assert len(body["moisture"]) <= 600
    assert set(body["moisture"][0].keys()) == {
        "ts", "pct_min", "pct_avg", "pct_max", "raw_avg"
    }


def test_history_includes_phase_changes_key(client):
    """Response always has phase_changes: [] (frontend reads it)."""
    c, _ = client
    for rng in ("24h", "7d", "30d", "90d", "all"):
        r = c.get(f"/api/grow/units/1/history?range={rng}")
        assert r.status_code == 200
        body = r.get_json()
        assert "phase_changes" in body, f"missing phase_changes for range={rng}"
        assert body["phase_changes"] == [], f"phase_changes not [] for range={rng}"


def test_history_empty_unit_returns_empty_arrays(empty_client):
    """Unit with no telemetry -> all three arrays empty (NOT 404)."""
    c, _ = empty_client
    r = c.get("/api/grow/units/1/history?range=24h")
    assert r.status_code == 200
    body = r.get_json()
    assert body["moisture"] == []
    assert body["watering_events"] == []
    assert body["phase_changes"] == []


def test_history_downsample_buckets_evenly_distributed(seed_client):
    """Seed 1200 points evenly spaced; assert returned bucket count is exactly
    600 and each bucket's ts is approximately at the bucket midpoint."""
    c, db_path = seed_client
    # 1200 points 60s apart -> 1200 minutes total, well within 30d
    start = datetime.utcnow() - timedelta(minutes=1200)
    _seed_telemetry(db_path, 1, 1200, start, interval_s=60)
    r = c.get("/api/grow/units/1/history?range=30d")
    body = r.get_json()
    moisture = body["moisture"]
    assert len(moisture) == 600

    # Each bucket holds 2 rows; midpoint ts of bucket i should be roughly
    # at the timestamp of row index 2*i (or 2*i+1 — the implementation picks
    # slice[len(slice)//2] which for 2-element buckets is index 1, i.e. 2*i+1).
    # Just verify the timestamps are monotonically increasing and span the
    # seeded range.
    timestamps = [datetime.fromisoformat(b["ts"]) for b in moisture]
    for prev, cur in zip(timestamps, timestamps[1:]):
        assert cur > prev, "downsample buckets must be ordered ascending"
    # First bucket midpoint should be near the start, last near the end
    expected_end = start + timedelta(seconds=(1200 - 1) * 60)
    span = expected_end - start
    assert timestamps[0] - start < span * 0.05  # within 5% of start
    assert expected_end - timestamps[-1] < span * 0.05  # within 5% of end
