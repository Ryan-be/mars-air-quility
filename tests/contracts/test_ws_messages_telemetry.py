from datetime import datetime, timezone
from mlss_contracts.ws_messages import WSMessage, TelemetryPayload
from pydantic import ValidationError
import pytest


def test_telemetry_minimum_required_fields():
    p = TelemetryPayload(
        soil_moisture_raw=612,
        light_state=True,
        pump_state=False,
    )
    assert p.soil_moisture_raw == 612
    assert p.light_state is True
    assert p.pump_state is False
    assert p.soil_moisture_pct is None
    assert p.soil_temp_c is None


def test_telemetry_with_optional_sensors():
    p = TelemetryPayload(
        soil_moisture_raw=612,
        soil_moisture_pct=58.3,
        light_state=True,
        pump_state=False,
        soil_temp_c=21.4,
        ambient_lux=15420,
    )
    assert p.soil_temp_c == 21.4
    assert p.ambient_lux == 15420


def test_telemetry_rejects_missing_required():
    with pytest.raises(ValidationError):
        TelemetryPayload(soil_moisture_raw=612, light_state=True)  # missing pump_state


def test_telemetry_accepts_optional_uptime_and_buffer_size():
    """Phase 3 diagnostics: every telemetry frame carries uptime_s and
    buffer_size so the server can cache the latest values into
    grow_units for the Diagnostics tab."""
    p = TelemetryPayload(
        soil_moisture_raw=612,
        light_state=True,
        pump_state=False,
        uptime_s=12345.6,
        buffer_size=42,
    )
    blob = p.model_dump_json()
    parsed = TelemetryPayload.model_validate_json(blob)
    assert parsed.uptime_s == 12345.6
    assert parsed.buffer_size == 42


def test_telemetry_existing_shape_unchanged_when_new_fields_omitted():
    """Backward compat: a telemetry payload built without the new
    diagnostics fields must still validate identically. Old firmware in
    the field can keep talking to the server."""
    p = TelemetryPayload(
        soil_moisture_raw=612,
        light_state=True,
        pump_state=False,
    )
    assert p.uptime_s is None
    assert p.buffer_size is None
    # The pre-existing required fields keep their values.
    assert p.soil_moisture_raw == 612
    assert p.light_state is True
    assert p.pump_state is False


def test_telemetry_accepts_optional_buffer_summary():
    """Buffer-inspection UI: the buffer summary is a free-form dict
    piggybacking on every Nth telemetry frame. Validation must accept
    it as ``dict | None`` — pydantic doesn't try to enforce the inner
    keys (the firmware-side LocalBuffer.summary() owns that contract,
    and a strict schema here would force a wire-shape lockstep we don't
    want)."""
    p = TelemetryPayload(
        soil_moisture_raw=612,
        light_state=True,
        pump_state=False,
        buffer_summary={
            "size": 247,
            "total_bytes": 78423,
            "oldest_ts": "2026-05-07T03:42:00",
            "newest_ts": "2026-05-07T04:17:30",
            "kinds": {"telemetry": 240, "event": 6, "capabilities": 1},
        },
        photo_buffer_summary={
            "size": 12,
            "total_bytes": 4_800_000,
            "oldest_ts": "2026-05-07T03:00:00Z",
            "newest_ts": "2026-05-07T05:30:00Z",
        },
    )
    blob = p.model_dump_json()
    parsed = TelemetryPayload.model_validate_json(blob)
    assert parsed.buffer_summary["size"] == 247
    assert parsed.buffer_summary["kinds"]["telemetry"] == 240
    assert parsed.photo_buffer_summary["size"] == 12


def test_telemetry_buffer_summary_omitted_defaults_to_none():
    """Most telemetry frames OMIT the summary (firmware sends it on
    every Nth tick only). Pydantic must default both to None so the
    server's omit-doesnt-clobber persistence path knows to skip the
    UPDATE."""
    p = TelemetryPayload(
        soil_moisture_raw=612,
        light_state=True,
        pump_state=False,
    )
    assert p.buffer_summary is None
    assert p.photo_buffer_summary is None


def test_ws_envelope_round_trip():
    msg = WSMessage(
        type="telemetry",
        ts=datetime(2026, 5, 3, 12, 34, 18, tzinfo=timezone.utc),
        payload={
            "soil_moisture_raw": 612,
            "light_state": True,
            "pump_state": False,
        },
    )
    blob = msg.model_dump_json()
    parsed = WSMessage.model_validate_json(blob)
    assert parsed.type == "telemetry"
    assert parsed.payload["soil_moisture_raw"] == 612
