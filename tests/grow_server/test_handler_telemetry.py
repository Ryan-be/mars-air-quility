"""handle_telemetry: writes one grow_telemetry row + updates grow_units.last_seen_at.

Phase 2 schema cleanup (C1) removed the denormalised
last_known_state_json cache from grow_units; the GET endpoints now
read directly from grow_telemetry. handle_telemetry just inserts the
row + bumps last_seen_at + promotes capability health where applicable.
"""
import sqlite3
import tempfile
from datetime import datetime
import pytest


@pytest.fixture
def db_with_unit(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.handlers.DB_FILE", tmp.name)
    init_db.create_db()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, soil_dry_raw, soil_wet_raw) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (1, "hw-1", "Tomato 1", datetime.utcnow(), "hash", datetime.utcnow(),
         200, 1500),
    )
    conn.commit()
    conn.close()
    return tmp.name


def test_handle_telemetry_inserts_row(db_with_unit):
    from mlss_monitor.grow.handlers import handle_telemetry
    handle_telemetry(unit_id=1, ts=datetime(2026, 5, 3, 12, 34, 18), payload={
        "soil_moisture_raw": 612,
        "soil_moisture_pct": 31.7,
        "light_state": True,
        "pump_state": False,
        "soil_temp_c": 21.4,
    })
    conn = sqlite3.connect(db_with_unit)
    row = conn.execute(
        "SELECT soil_moisture_raw, soil_moisture_pct, light_state, "
        "pump_state, soil_temp_c FROM grow_telemetry WHERE unit_id=1"
    ).fetchone()
    assert row == (612, 31.7, 1, 0, 21.4)


def test_handle_telemetry_updates_last_seen_at(db_with_unit):
    """C1: the denormalised last_known_state_json cache is gone — the GET
    endpoints SELECT against grow_telemetry directly. handle_telemetry
    only needs to bump last_telemetry_at + last_seen_at on grow_units."""
    from mlss_monitor.grow.handlers import handle_telemetry
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612,
        "soil_moisture_pct": 31.7,
        "light_state": True,
        "pump_state": False,
    })
    conn = sqlite3.connect(db_with_unit)
    last_seen, last_tele = conn.execute(
        "SELECT last_seen_at, last_telemetry_at FROM grow_units WHERE id=1"
    ).fetchone()
    assert last_seen is not None
    assert last_tele is not None
    # And the row landed in grow_telemetry where future GETs will find it.
    pct = conn.execute(
        "SELECT soil_moisture_pct FROM grow_telemetry "
        "WHERE unit_id=1 ORDER BY timestamp_utc DESC LIMIT 1"
    ).fetchone()[0]
    assert pct == 31.7


def test_handle_telemetry_returns_inserted_id(db_with_unit):
    from mlss_monitor.grow.handlers import handle_telemetry
    inserted_id = handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612, "light_state": False, "pump_state": False,
    })
    assert isinstance(inserted_id, int)
    assert inserted_id > 0


def _seed_capability(db_path, unit_id, channel, hardware, is_required, health):
    """Helper: insert a grow_unit_capabilities row with the typed health column."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_unit_capabilities "
        "(unit_id, channel, hardware, is_required, unit_label, "
        " installed_at, health) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (unit_id, channel, hardware, int(is_required), "bool",
         datetime.utcnow(), health),
    )
    conn.commit()
    conn.close()


def _read_capability_health(db_path, unit_id, channel):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT health FROM grow_unit_capabilities "
        "WHERE unit_id=? AND channel=?",
        (unit_id, channel),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def test_handle_telemetry_with_pump_state_1_promotes_pump_to_connected(db_with_unit):
    """Phase 2 sense-only-mode: when telemetry shows pump_state=1, the server
    promotes the pump capability's health to "connected" — that's strong
    evidence the actuator is wired and working."""
    from mlss_monitor.grow.handlers import handle_telemetry
    _seed_capability(db_with_unit, 1, "pump", "automation_phat", False, "untested")
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612, "light_state": False, "pump_state": True,
    })
    assert _read_capability_health(db_with_unit, 1, "pump") == "connected"


def test_handle_telemetry_with_light_state_1_promotes_light_to_connected(db_with_unit):
    from mlss_monitor.grow.handlers import handle_telemetry
    _seed_capability(db_with_unit, 1, "light", "automation_phat", False, "untested")
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612, "light_state": True, "pump_state": False,
    })
    assert _read_capability_health(db_with_unit, 1, "light") == "connected"


def test_handle_telemetry_with_state_0_does_not_demote_connected(db_with_unit):
    """Once a capability is "connected", routine off-state telemetry must NOT
    flip it back. The pump being off most of the time is the normal idle
    state, not evidence of disconnection. Only the watchdog (after a
    command without follow-up evidence) demotes."""
    from mlss_monitor.grow.handlers import handle_telemetry
    _seed_capability(db_with_unit, 1, "pump", "automation_phat", False, "connected")
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612, "light_state": False, "pump_state": False,
    })
    assert _read_capability_health(db_with_unit, 1, "pump") == "connected"


def test_handle_telemetry_persists_uptime_and_buffer_size(db_with_unit):
    """Phase 3 diagnostics: handle_telemetry caches uptime_s + buffer_size
    onto grow_units.last_uptime_s + last_buffer_size so the Diagnostics
    tab can render them without joining against grow_telemetry."""
    from mlss_monitor.grow.handlers import handle_telemetry
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612, "light_state": False, "pump_state": False,
        "uptime_s": 1234.5,
        "buffer_size": 42,
    })
    conn = sqlite3.connect(db_with_unit)
    uptime, buf = conn.execute(
        "SELECT last_uptime_s, last_buffer_size FROM grow_units WHERE id=1"
    ).fetchone()
    assert uptime == 1234.5
    assert buf == 42


def test_handle_telemetry_omitting_uptime_does_not_clobber_existing_value(db_with_unit):
    """Backward compat: a telemetry frame without uptime_s must NOT
    overwrite a previously-cached last_uptime_s with NULL. Keeps the
    Diagnostics tab from flipping to "unknown" each time an old
    firmware (no diagnostics fields) sends a frame between modern
    frames."""
    conn = sqlite3.connect(db_with_unit)
    conn.execute(
        "UPDATE grow_units SET last_uptime_s=100, last_buffer_size=5 WHERE id=1"
    )
    conn.commit()
    conn.close()

    from mlss_monitor.grow.handlers import handle_telemetry
    # Old-firmware-shaped payload: no uptime_s, no buffer_size.
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612, "light_state": False, "pump_state": False,
    })
    conn = sqlite3.connect(db_with_unit)
    uptime, buf = conn.execute(
        "SELECT last_uptime_s, last_buffer_size FROM grow_units WHERE id=1"
    ).fetchone()
    assert uptime == 100  # untouched
    assert buf == 5  # untouched


def test_handle_telemetry_partial_diagnostics_updates_only_provided_field(db_with_unit):
    """A frame with uptime_s but no buffer_size must update only
    last_uptime_s and leave last_buffer_size alone (and vice versa).
    Hedges against firmware that only emits one of the two if e.g. the
    buffer hasn't initialised yet."""
    conn = sqlite3.connect(db_with_unit)
    conn.execute(
        "UPDATE grow_units SET last_uptime_s=100, last_buffer_size=5 WHERE id=1"
    )
    conn.commit()
    conn.close()

    from mlss_monitor.grow.handlers import handle_telemetry
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612, "light_state": False, "pump_state": False,
        "uptime_s": 999.0,  # only uptime, no buffer_size
    })
    conn = sqlite3.connect(db_with_unit)
    uptime, buf = conn.execute(
        "SELECT last_uptime_s, last_buffer_size FROM grow_units WHERE id=1"
    ).fetchone()
    assert uptime == 999.0  # updated
    assert buf == 5  # untouched


def test_handle_telemetry_persists_buffer_summary_to_grow_units(db_with_unit):
    """Buffer-inspection UI cache: when a piggyback telemetry frame
    carries `buffer_summary`, handle_telemetry serialises it into
    grow_units.last_buffer_summary_json. The Diagnostics tab parses
    that back to render WHAT is queued."""
    import json
    from mlss_monitor.grow.handlers import handle_telemetry

    summary = {
        "size": 247,
        "total_bytes": 78423,
        "oldest_ts": "2026-05-07T03:42:00",
        "newest_ts": "2026-05-07T04:17:30",
        "kinds": {"telemetry": 240, "event": 6, "capabilities": 1},
    }
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612, "light_state": False, "pump_state": False,
        "buffer_summary": summary,
    })
    conn = sqlite3.connect(db_with_unit)
    raw = conn.execute(
        "SELECT last_buffer_summary_json FROM grow_units WHERE id=1"
    ).fetchone()[0]
    assert raw is not None, "handler must persist buffer_summary"
    assert json.loads(raw) == summary


def test_handle_telemetry_persists_photo_buffer_summary(db_with_unit):
    """Same shape as buffer_summary but lands in
    last_photo_buffer_summary_json. Both summaries are independently
    nullable — a frame can carry either, both, or neither."""
    import json
    from mlss_monitor.grow.handlers import handle_telemetry

    photo_summary = {
        "size": 12,
        "total_bytes": 4_800_000,
        "oldest_ts": "2026-05-07T03:00:00Z",
        "newest_ts": "2026-05-07T05:30:00Z",
    }
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612, "light_state": False, "pump_state": False,
        "photo_buffer_summary": photo_summary,
    })
    conn = sqlite3.connect(db_with_unit)
    raw = conn.execute(
        "SELECT last_photo_buffer_summary_json FROM grow_units WHERE id=1"
    ).fetchone()[0]
    assert raw is not None
    assert json.loads(raw) == photo_summary


def test_handle_telemetry_omitting_buffer_summary_does_not_clobber_existing(db_with_unit):
    """Omit-doesnt-clobber for the summary columns matches the existing
    contract for last_uptime_s + last_buffer_size. Most telemetry
    frames OMIT the summary (only every 10th tick carries it); those
    in-between frames must NOT overwrite the cached summary with NULL,
    or the Diagnostics tab would flap on every non-piggyback frame."""
    import json
    conn = sqlite3.connect(db_with_unit)
    # Pre-seed both columns with a previous summary.
    conn.execute(
        "UPDATE grow_units SET "
        " last_buffer_summary_json='{\"size\":100,\"kinds\":{\"telemetry\":100}}',"
        " last_photo_buffer_summary_json='{\"size\":3}'"
        " WHERE id=1"
    )
    conn.commit()
    conn.close()

    from mlss_monitor.grow.handlers import handle_telemetry
    # Non-piggyback frame: no buffer_summary, no photo_buffer_summary.
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612, "light_state": False, "pump_state": False,
    })
    conn = sqlite3.connect(db_with_unit)
    bs, pbs = conn.execute(
        "SELECT last_buffer_summary_json, last_photo_buffer_summary_json "
        "FROM grow_units WHERE id=1"
    ).fetchone()
    assert json.loads(bs) == {"size": 100, "kinds": {"telemetry": 100}}, (
        "non-piggyback frame must NOT clobber the cached buffer_summary"
    )
    assert json.loads(pbs) == {"size": 3}, (
        "non-piggyback frame must NOT clobber the cached photo_buffer_summary"
    )


def test_handle_telemetry_computes_pct_when_unit_calibrated(db_with_unit):
    """If pct is missing but raw + calibration are present, server fills it in."""
    from mlss_monitor.grow.handlers import handle_telemetry
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 850,  # midway between dry=200 and wet=1500
        "light_state": False, "pump_state": False,
    })
    conn = sqlite3.connect(db_with_unit)
    pct = conn.execute(
        "SELECT soil_moisture_pct FROM grow_telemetry WHERE unit_id=1"
    ).fetchone()[0]
    # (850-200)/(1500-200) = 0.5 → 50%
    assert pct == pytest.approx(50.0, abs=0.5)
