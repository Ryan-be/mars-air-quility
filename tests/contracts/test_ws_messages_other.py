from mlss_contracts.ws_messages import (
    EventPayload, CapabilitiesPayload, CommandPayload,
    ConfigPayload, AckPayload,
)
from mlss_contracts.enums import EventKind, CommandName, Phase
from mlss_contracts.capabilities import Capability
from mlss_contracts.plant_profiles import LightWindow, WateringConfig


def test_event_payload():
    e = EventPayload(
        kind=EventKind.WATERING_PULSE,
        details={"duration_s": 5.2, "soil_pct_before": 42},
    )
    assert e.kind == EventKind.WATERING_PULSE
    assert e.details["duration_s"] == 5.2


def test_capabilities_payload_round_trip():
    c = CapabilitiesPayload(
        capabilities=[
            Capability(channel="soil_moisture", hardware="Seesaw",
                       is_required=True, unit_label="raw"),
            Capability(channel="camera", hardware="picamera2",
                       is_required=True, unit_label="jpeg"),
        ],
        firmware_version="0.1.0",
        hardware_serial="100000000c0a8014b",
    )
    blob = c.model_dump_json()
    parsed = CapabilitiesPayload.model_validate_json(blob)
    assert len(parsed.capabilities) == 2
    assert parsed.firmware_version == "0.1.0"


def test_command_payload_with_args():
    c = CommandPayload(name=CommandName.IDENTIFY, args={"duration_s": 10})
    assert c.name == CommandName.IDENTIFY
    assert c.args == {"duration_s": 10}


def test_command_payload_no_args():
    c = CommandPayload(name=CommandName.RELOAD_CONFIG)
    assert c.args is None


def test_config_payload_round_trip():
    cfg = ConfigPayload(
        plant_type="tomato",
        current_phase=Phase.VEGETATIVE,
        light_windows=[LightWindow(start_hh_mm="06:00", end_hh_mm="22:00")],
        watering=WateringConfig(target_moisture_pct=55),
        photo_interval_min=30,
        photo_active_hours=(6, 22),
        soil_dry_raw=200,
        soil_wet_raw=1500,
        buffer_retention_days=7,
    )
    blob = cfg.model_dump_json()
    parsed = ConfigPayload.model_validate_json(blob)
    assert parsed == cfg


def test_ack_payload():
    a = AckPayload(in_reply_to_command="identify", success=True,
                   extra={"actual_duration_s": 9.97})
    assert a.success is True
    assert a.extra["actual_duration_s"] == 9.97


def test_ack_payload_failure():
    a = AckPayload(in_reply_to_command="water_now", success=False,
                   error="locked_in_soak_window")
    assert a.success is False
    assert a.error == "locked_in_soak_window"
