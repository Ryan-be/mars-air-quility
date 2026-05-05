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
