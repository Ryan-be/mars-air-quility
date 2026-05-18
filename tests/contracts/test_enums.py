from mlss_contracts.enums import Channel, Phase, EventKind, CommandName


def test_channel_required_set():
    assert Channel.SOIL_MOISTURE.value == "soil_moisture"
    assert Channel.LIGHT.value == "light"
    assert Channel.PUMP.value == "pump"
    assert Channel.CAMERA.value == "camera"


def test_channel_optional_set():
    assert Channel.SOIL_TEMP_C.value == "soil_temp_c"
    assert Channel.AMBIENT_LUX.value == "ambient_lux"
    assert Channel.AIR_TEMP_C.value == "air_temp_c"
    assert Channel.AIR_HUMIDITY_PCT.value == "air_humidity_pct"
    assert Channel.RESERVOIR_LEVEL_PCT.value == "reservoir_level_pct"


def test_phase_values():
    assert {p.value for p in Phase} == {
        "seedling", "vegetative", "flowering", "fruiting", "dormant"
    }


# MediumType + Severity tests removed in pre-Phase-4 audit Bucket C4 —
# both enums were defined but unused in production. Medium values are
# enforced via the `_MEDIUM` Literal in config_payloads.py; severity
# values via bare strings in handlers.py + grow_errors.severity column.


def test_event_kind_values():
    # `buffer_eviction` added 2026-05-08 (Bucket A2 of pre-Phase-4
    # cleanup) — see grow-data-flow-audit Flow 6 #1.
    expected = {
        "watering_pulse", "sensor_degraded", "sensor_recovered",
        "config_applied", "identify_complete", "safety_cap_hit",
        "startup", "shutdown", "buffer_replay_started", "buffer_replay_complete",
        "buffer_eviction",
    }
    assert {e.value for e in EventKind} == expected


def test_command_name_values():
    expected = {
        "identify", "water_now", "light_override",
        "snap_photo", "reload_config", "reboot",
    }
    assert {c.value for c in CommandName} == expected
