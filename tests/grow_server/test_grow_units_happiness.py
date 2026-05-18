"""GET /api/grow/units/<id> `happiness` block — zone classification +
fallback chain (specific plant → generic → empty dict).

Boundary cases live in test_zone_boundaries below (pure-function tests
against _zone) so we don't have to wire up a fixture per boundary.
"""
import sqlite3
from datetime import datetime

import pytest


@pytest.fixture
def make_client(monkeypatch, tmp_path):
    """Returns a factory that mints a Flask test client for a unit
    pre-seeded with the supplied plant_type, phase, and telemetry."""
    def _make(*, plant_type, phase, telemetry):
        db_path = str(tmp_path / "test.db")
        import database.init_db as init_db
        init_db.DB_FILE = db_path
        monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", db_path)
        init_db.create_db()

        now = datetime.utcnow()
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO grow_units (id, hardware_serial, label, "
            "  enrolled_at, bearer_token_hash, phase_set_at, last_seen_at, "
            "  plant_type, current_phase) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "hw-1", "T1", now, "hash1", now, now, plant_type, phase),
        )
        if telemetry is not None:
            conn.execute(
                "INSERT INTO grow_telemetry "
                "(unit_id, timestamp_utc, soil_moisture_raw, "
                " soil_moisture_pct, light_state, pump_state, soil_temp_c) "
                "VALUES (1, ?, 612, ?, 1, 0, ?)",
                (now, telemetry.get("soil_moisture_pct"),
                 telemetry.get("soil_temp_c")),
            )
        conn.commit()
        conn.close()

        from flask import Flask
        from mlss_monitor.routes.api_grow_units import api_grow_units_bp
        monkeypatch.setattr(
            "mlss_monitor.routes.api_grow_units.DB_FILE", db_path,
        )
        monkeypatch.setattr(
            "mlss_monitor.grow.health_watchdog.DB_FILE", db_path,
        )
        app = Flask(__name__)
        app.register_blueprint(api_grow_units_bp)
        return app.test_client()
    return _make


def test_happiness_block_present_when_thresholds_seeded(make_client):
    """The endpoint must include a top-level `happiness` block on the
    GET response for a unit with a known plant_type + phase + telemetry.
    """
    client = make_client(
        plant_type="chili", phase="vegetative",
        telemetry={"soil_temp_c": 22.8, "soil_moisture_pct": 50},
    )
    r = client.get("/api/grow/units/1")
    assert r.status_code == 200
    body = r.get_json()
    assert "happiness" in body
    assert "soil_temp_c" in body["happiness"]
    assert "soil_moisture_pct" in body["happiness"]


def test_happiness_returns_ideal_for_temp_in_range(make_client):
    """chili-vegetative ideal_min=21, ideal_max=27 → 24 is ideal."""
    client = make_client(
        plant_type="chili", phase="vegetative",
        telemetry={"soil_temp_c": 24, "soil_moisture_pct": 45},
    )
    body = client.get("/api/grow/units/1").get_json()
    assert body["happiness"]["soil_temp_c"]["zone"] == "ideal"


def test_happiness_returns_critical_high_for_temp_above(make_client):
    """chili-vegetative critical_max=32 → 40 is critical_high."""
    client = make_client(
        plant_type="chili", phase="vegetative",
        telemetry={"soil_temp_c": 40, "soil_moisture_pct": 45},
    )
    body = client.get("/api/grow/units/1").get_json()
    assert body["happiness"]["soil_temp_c"]["zone"] == "critical_high"


def test_happiness_returns_tolerated_low_for_temp_just_below_ideal(make_client):
    """chili-vegetative critical_min=13, ideal_min=21 → 15 is in the
    tolerated_low band (>=13 but <21)."""
    client = make_client(
        plant_type="chili", phase="vegetative",
        telemetry={"soil_temp_c": 15, "soil_moisture_pct": 45},
    )
    body = client.get("/api/grow/units/1").get_json()
    assert body["happiness"]["soil_temp_c"]["zone"] == "tolerated_low"


def test_happiness_returns_critical_low_for_temp_below_critical_min(make_client):
    """chili-vegetative critical_min=13 → 10 is critical_low."""
    client = make_client(
        plant_type="chili", phase="vegetative",
        telemetry={"soil_temp_c": 10, "soil_moisture_pct": 45},
    )
    body = client.get("/api/grow/units/1").get_json()
    assert body["happiness"]["soil_temp_c"]["zone"] == "critical_low"


def test_happiness_moisture_critical_high(make_client):
    """chili-vegetative soil_moisture critical_max=85 → 95 is
    critical_high."""
    client = make_client(
        plant_type="chili", phase="vegetative",
        telemetry={"soil_temp_c": 22, "soil_moisture_pct": 95},
    )
    body = client.get("/api/grow/units/1").get_json()
    h = body["happiness"]["soil_moisture_pct"]
    assert h["zone"] == "critical_high"
    assert h["current"] == 95


def test_happiness_falls_back_to_generic_for_unknown_plant(make_client):
    """A unit with a custom plant_type (not in THRESHOLD_SEEDS) must
    fall back to the generic profile for the same phase. The dragonfruit
    test reading of 24 °C sits inside generic-vegetative's ideal
    (ideal_min=18, ideal_max=26)."""
    client = make_client(
        plant_type="dragonfruit", phase="vegetative",
        telemetry={"soil_temp_c": 24, "soil_moisture_pct": 50},
    )
    body = client.get("/api/grow/units/1").get_json()
    assert body["happiness"]["soil_temp_c"]["zone"] == "ideal"
    # Generic-vegetative thresholds (10, 18, 26, 32) must be the ones
    # surfaced in the response — not the chili numbers — otherwise the
    # fallback didn't actually run.
    assert body["happiness"]["soil_temp_c"]["thresholds"] == {
        "critical_min": 10, "ideal_min": 18,
        "ideal_max": 26, "critical_max": 32,
    }


def test_happiness_empty_dict_when_no_telemetry(make_client):
    """A unit that has never produced telemetry returns happiness={} —
    NOT None, so the FE doesn't need a two-tier nullability check."""
    client = make_client(
        plant_type="chili", phase="vegetative",
        telemetry=None,
    )
    body = client.get("/api/grow/units/1").get_json()
    assert body["happiness"] == {}


def test_happiness_ideal_range_text_format(make_client):
    """The `ideal_range` field is the operator-facing subtext + title
    attribute on the tile. Confirm the canonical "min–max unit" shape."""
    client = make_client(
        plant_type="chili", phase="vegetative",
        telemetry={"soil_temp_c": 22, "soil_moisture_pct": 50},
    )
    body = client.get("/api/grow/units/1").get_json()
    # chili-vegetative soil_temp ideal range = 21..27 °C
    assert body["happiness"]["soil_temp_c"]["ideal_range"] == "21–27 °C"
    # soil_moisture ideal range = 35..60 %
    assert body["happiness"]["soil_moisture_pct"]["ideal_range"] == "35–60 %"


def test_happiness_thresholds_dict_shape(make_client):
    """The `thresholds` sub-block must use the 4 short keys (no _c /
    _pct suffix) — the FE uses them generically across both dimensions
    so adding a suffix would force a per-dimension switch."""
    client = make_client(
        plant_type="chili", phase="vegetative",
        telemetry={"soil_temp_c": 22, "soil_moisture_pct": 50},
    )
    body = client.get("/api/grow/units/1").get_json()
    t = body["happiness"]["soil_temp_c"]["thresholds"]
    assert set(t.keys()) == {"critical_min", "ideal_min",
                             "ideal_max", "critical_max"}


def test_happiness_ideal_boundary_inclusive_at_top(make_client):
    """Exact ideal_max value is in the ideal zone (inclusive). For
    chili-vegetative ideal_max=27 → reading of 27 must classify ideal,
    NOT tolerated_high. Matches operator intuition that "27 °C is
    still ideal"."""
    client = make_client(
        plant_type="chili", phase="vegetative",
        telemetry={"soil_temp_c": 27, "soil_moisture_pct": 50},
    )
    body = client.get("/api/grow/units/1").get_json()
    assert body["happiness"]["soil_temp_c"]["zone"] == "ideal"


def test_happiness_just_above_critical_max_is_critical_high(make_client):
    """One step above critical_max trips critical_high (NOT
    tolerated_high). chili-vegetative critical_max=32 → 32.01 ⇒
    critical_high."""
    client = make_client(
        plant_type="chili", phase="vegetative",
        telemetry={"soil_temp_c": 32.01, "soil_moisture_pct": 50},
    )
    body = client.get("/api/grow/units/1").get_json()
    assert body["happiness"]["soil_temp_c"]["zone"] == "critical_high"


def test_happiness_zone_null_when_telemetry_value_none(make_client):
    """A unit reporting only one of {soil_temp_c, soil_moisture_pct} —
    e.g. moisture-only — should get zone=null on the unsensed dimension
    so the FE can tell "no sensor" apart from "no thresholds"."""
    # Seed telemetry without soil_temp_c at all (passing None means we
    # omit it from the INSERT — but our fixture passes None as the
    # bound parameter, which sqlite stores as NULL).
    client = make_client(
        plant_type="chili", phase="vegetative",
        telemetry={"soil_temp_c": None, "soil_moisture_pct": 50},
    )
    body = client.get("/api/grow/units/1").get_json()
    assert body["happiness"]["soil_temp_c"]["zone"] is None
    # Moisture dimension still has a current value + zone.
    assert body["happiness"]["soil_moisture_pct"]["zone"] == "ideal"


# ---------------------------------------------------------------------------
# Pure-function boundary tests against _zone() — no DB fixture needed.
# ---------------------------------------------------------------------------

def test_zone_value_none():
    from mlss_monitor.routes.api_grow_units import _zone
    t = {"critical_min": 0, "ideal_min": 10, "ideal_max": 20, "critical_max": 30}
    assert _zone(None, t) is None


def test_zone_thresholds_partial_none():
    from mlss_monitor.routes.api_grow_units import _zone
    # Any missing threshold => fall through to None signal.
    t = {"critical_min": 0, "ideal_min": None, "ideal_max": 20, "critical_max": 30}
    assert _zone(15, t) is None


def test_zone_exact_critical_min_is_tolerated_low():
    from mlss_monitor.routes.api_grow_units import _zone
    t = {"critical_min": 13, "ideal_min": 21, "ideal_max": 27, "critical_max": 32}
    # value < critical_min => critical_low; value == critical_min =>
    # tolerated_low (it's the inclusive lower bound of the tolerated band).
    assert _zone(13, t) == "tolerated_low"


def test_zone_exact_ideal_min_is_ideal():
    from mlss_monitor.routes.api_grow_units import _zone
    t = {"critical_min": 13, "ideal_min": 21, "ideal_max": 27, "critical_max": 32}
    assert _zone(21, t) == "ideal"


def test_zone_exact_ideal_max_is_ideal():
    from mlss_monitor.routes.api_grow_units import _zone
    t = {"critical_min": 13, "ideal_min": 21, "ideal_max": 27, "critical_max": 32}
    assert _zone(27, t) == "ideal"


def test_zone_exact_critical_max_is_tolerated_high():
    from mlss_monitor.routes.api_grow_units import _zone
    t = {"critical_min": 13, "ideal_min": 21, "ideal_max": 27, "critical_max": 32}
    # value <= critical_max => tolerated_high.
    assert _zone(32, t) == "tolerated_high"
