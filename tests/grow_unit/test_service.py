"""Service entrypoint orchestrates: load config → enrol if needed → boot WS + safety loop."""
from unittest.mock import MagicMock, patch
from mlss_grow.service import bootstrap_unit_state, _build_reconnect_sync
import pytest


def test_bootstrap_uses_existing_token_if_present(tmp_path, monkeypatch):
    token_path = str(tmp_path / "grow.token")
    boot_path = str(tmp_path / "mlss-grow.yaml")
    # Pre-existing token
    from mlss_grow.config import save_token
    save_token(token_path, unit_id=42, token="existing-token")

    # Boot YAML present (would normally trigger enroll) but token already exists
    open(boot_path, "w").write(  # pylint: disable=R1732,unspecified-encoding
        "mlss_host: mlss.local\nenrollment_key: x\nplant:\n  name: X\n"
    )

    state = bootstrap_unit_state(
        firstboot_path=boot_path,
        token_path=token_path,
        enroll_fn=MagicMock(side_effect=AssertionError("should not enroll")),
        get_serial_fn=MagicMock(return_value="hw-1"),
    )
    assert state.unit_id == 42
    assert state.token == "existing-token"
    assert state.mlss_host == "mlss.local"


def test_bootstrap_enrolls_when_no_token(tmp_path, monkeypatch):
    token_path = str(tmp_path / "grow.token")
    boot_path = str(tmp_path / "mlss-grow.yaml")
    open(boot_path, "w").write(  # pylint: disable=R1732,unspecified-encoding
        "mlss_host: mlss.local\nenrollment_key: ek\nplant:\n  name: Test\n"
    )

    state = bootstrap_unit_state(
        firstboot_path=boot_path,
        token_path=token_path,
        enroll_fn=MagicMock(return_value=(99, "freshly-minted")),
        get_serial_fn=MagicMock(return_value="hw-1"),
    )
    assert state.unit_id == 99
    assert state.token == "freshly-minted"

    # Token persisted
    from mlss_grow.config import load_token
    assert load_token(token_path) == (99, "freshly-minted")

    # YAML file removed (don't leave enrollment key on SD card)
    import os
    assert not os.path.exists(boot_path)


def test_bootstrap_raises_when_no_token_and_no_firstboot(tmp_path):
    with pytest.raises(RuntimeError, match="no firstboot config"):
        bootstrap_unit_state(
            firstboot_path=str(tmp_path / "absent.yaml"),
            token_path=str(tmp_path / "absent.token"),
            enroll_fn=MagicMock(),
            get_serial_fn=MagicMock(return_value="hw-1"),
        )


# ---------------------------------------------------------------------------
# _build_reconnect_sync: closure binds pull_unit_config + apply_config so
# the WSClient can re-sync config on every reconnect without knowing
# anything about HTTP or the loop config layout.
# ---------------------------------------------------------------------------


def test_build_reconnect_sync_pulls_and_applies_with_bound_args():
    """The closure must call pull_unit_config with the exact args bound
    at construction time, then apply the result to the SAME loop_cfg
    instance — so the safety loop sees the in-place mutation without
    any return-value plumbing."""
    fake_unit_cfg = MagicMock(current_phase="vegetative", plant_type="tomato")
    loop_cfg = MagicMock()

    with patch("mlss_grow.config_sync.pull_unit_config",
                return_value=fake_unit_cfg) as mock_pull, \
         patch("mlss_grow.config_sync.apply_config") as mock_apply:
        sync = _build_reconnect_sync(
            server_url="https://mlss.local:5000",
            unit_id=42,
            token="bearer-tok",
            server_cert_path="/etc/mlss/server.crt",
            loop_cfg=loop_cfg,
        )
        # Calling the closure should pull and apply.
        sync()

    mock_pull.assert_called_once_with(
        "https://mlss.local:5000", 42, "bearer-tok",
        server_cert_path="/etc/mlss/server.crt",
    )
    mock_apply.assert_called_once_with(fake_unit_cfg, loop_cfg)


def test_build_reconnect_sync_swallows_pull_failure():
    """If pull raises (network blip, server down), the closure must log
    and return — NOT propagate. WSClient.run_forever catches anyway, but
    swallowing here lets the WS keep its receive loop alive without
    even hitting that fallback path."""
    loop_cfg = MagicMock()

    with patch("mlss_grow.config_sync.pull_unit_config",
                side_effect=ConnectionError("dns fail")), \
         patch("mlss_grow.config_sync.apply_config") as mock_apply:
        sync = _build_reconnect_sync(
            server_url="https://mlss.local:5000",
            unit_id=1,
            token="t",
            server_cert_path=None,
            loop_cfg=loop_cfg,
        )
        # Must not raise.
        sync()

    # apply_config never reached because pull raised.
    mock_apply.assert_not_called()


def test_build_reconnect_sync_swallows_apply_failure():
    """If apply_config raises (e.g. malformed light_window from server),
    the closure must log and return — NOT propagate. Symmetric with the
    pull failure path: a single bad config push must not kill the WS."""
    loop_cfg = MagicMock()
    fake_unit_cfg = MagicMock(current_phase="x", plant_type="y")

    with patch("mlss_grow.config_sync.pull_unit_config",
                return_value=fake_unit_cfg), \
         patch("mlss_grow.config_sync.apply_config",
                side_effect=ValueError("bad window")):
        sync = _build_reconnect_sync(
            server_url="https://mlss.local:5000",
            unit_id=1,
            token="t",
            server_cert_path=None,
            loop_cfg=loop_cfg,
        )
        # Must not raise.
        sync()


# ─── Capability frame building (Bucket A1) ────────────────────────


from mlss_grow.service import _build_capabilities


class _FakeSensor:
    """Test stub mirroring Sensor.channels() — minimal surface so
    _build_capabilities can iterate it. Mirrors the real SeesawSoilSensor's
    multi-channel emit (one driver, two channels)."""
    def __init__(self, channels_list):
        self._channels = channels_list

    def channels(self):
        return self._channels


def test_build_capabilities_camera_only_posture():
    """Pi user's first-deployment posture: camera detected but no
    soil sensor / pHAT wired. Should emit the four required channels
    with health="no_hardware" for the missing three."""
    caps = _build_capabilities(
        sensors=[],
        sensor_healths={},
        pump=None, pump_health="no_hardware",
        light=None, light_health="no_hardware",
        camera=object(), camera_health="connected",
        hardware_serial="abc123",
    )
    by_channel = {c["channel"]: c for c in caps}
    assert set(by_channel) == {"pump", "light", "camera", "soil_moisture"}
    assert by_channel["camera"]["health"] == "connected"
    assert by_channel["pump"]["health"] == "no_hardware"
    assert by_channel["light"]["health"] == "no_hardware"
    assert by_channel["soil_moisture"]["health"] == "no_hardware"
    # All required
    assert all(c["is_required"] for c in caps)


def test_build_capabilities_full_deployment():
    """Soil sensor + pump + light + camera all wired and reading."""
    sensor = _FakeSensor(["soil_moisture", "soil_temp_c"])
    caps = _build_capabilities(
        sensors=[sensor],
        sensor_healths={id(sensor): "connected"},
        pump=object(), pump_health="untested",
        light=object(), light_health="untested",
        camera=object(), camera_health="connected",
        hardware_serial="abc123",
    )
    by_channel = {c["channel"]: c for c in caps}
    # Required + optional sensor channel both present
    assert set(by_channel) == {
        "pump", "light", "camera", "soil_moisture", "soil_temp_c",
    }
    assert by_channel["soil_moisture"]["health"] == "connected"
    assert by_channel["soil_moisture"]["is_required"] is True
    # Optional sensor channel
    assert by_channel["soil_temp_c"]["health"] == "connected"
    assert by_channel["soil_temp_c"]["is_required"] is False
    # Actuators are "untested" until first command echo
    assert by_channel["pump"]["health"] == "untested"
    assert by_channel["light"]["health"] == "untested"


def test_build_capabilities_dead_sensor_still_declares_channels():
    """A sensor that detected on the bus but failed first-read still
    declares its channels — the UI needs to know the channel exists
    even if it's currently broken."""
    sensor = _FakeSensor(["soil_moisture"])
    caps = _build_capabilities(
        sensors=[sensor],
        sensor_healths={id(sensor): "no_hardware"},
        pump=None, pump_health="no_hardware",
        light=None, light_health="no_hardware",
        camera=None, camera_health="no_hardware",
        hardware_serial="abc123",
    )
    by_channel = {c["channel"]: c for c in caps}
    assert by_channel["soil_moisture"]["health"] == "no_hardware"
    # Still has hardware metadata so ops debugging knows what driver was tried
    assert by_channel["soil_moisture"]["hardware"] == "_FakeSensor"


def test_build_capabilities_required_channels_always_emitted():
    """Even with zero hardware, the four required channels must all
    appear (the placeholder 'unknown' hardware path) so the UI tile
    grid renders consistently across postures."""
    caps = _build_capabilities(
        sensors=[],
        sensor_healths={},
        pump=None, pump_health="no_hardware",
        light=None, light_health="no_hardware",
        camera=None, camera_health="no_hardware",
        hardware_serial="abc123",
    )
    required = {"soil_moisture", "light", "pump", "camera"}
    assert required.issubset({c["channel"] for c in caps})
    # The soil_moisture placeholder uses "unknown" as the hardware
    soil = next(c for c in caps if c["channel"] == "soil_moisture")
    assert soil["hardware"] == "unknown"


def test_build_capabilities_payload_validates_against_contract():
    """Round-trip the firmware-built dicts through the pydantic
    Capability + CapabilitiesPayload models so a future contract
    change (e.g. a new required field) breaks here, not in production."""
    from mlss_contracts.ws_messages import CapabilitiesPayload

    sensor = _FakeSensor(["soil_moisture", "soil_temp_c"])
    caps = _build_capabilities(
        sensors=[sensor],
        sensor_healths={id(sensor): "connected"},
        pump=object(), pump_health="untested",
        light=object(), light_health="untested",
        camera=object(), camera_health="connected",
        hardware_serial="abc123",
    )
    # CapabilitiesPayload(capabilities=caps, ...) raises ValidationError
    # on any drift — channel must match the Channel enum, health must
    # match the CapabilityHealth literal, etc.
    payload = CapabilitiesPayload(
        capabilities=caps,
        firmware_version="1.0.0",
        hardware_serial="abc123",
        uptime_s=1.5,
    )
    # Ensure the round-trip preserves every channel
    assert {c.channel.value for c in payload.capabilities} == {
        c["channel"] for c in caps
    }
