import pytest

from mlss_contracts.ws_messages import (
    EventPayload, CapabilitiesPayload,
)
from mlss_contracts.enums import EventKind
from mlss_contracts.capabilities import Capability


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


# CommandPayload + ConfigPayload round-trip tests removed in pre-Phase-4
# audit Bucket C4 — the models were deleted from ws_messages.py because
# no production endpoint validated against them. The actual command
# framing is the loose `{type, ts, payload: {name|kind, args}}` shape
# in mlss_grow.dispatch, and config pushes use a `kind: config_changed`
# notification + a separate bearer-authenticated GET to fetch the
# resolved-overrides dict. See ws_messages.py module-level comment.


def test_capabilities_accepts_optional_uptime_s():
    """Phase 3 diagnostics: capabilities envelope carries uptime_s so the
    server can cache it on capabilities receipt (alongside the existing
    firmware_version)."""
    c = CapabilitiesPayload(
        capabilities=[
            Capability(channel="soil_moisture", hardware="Seesaw",
                       is_required=True, unit_label="raw"),
        ],
        firmware_version="1.2.3",
        hardware_serial="hw-1",
        uptime_s=42.5,
    )
    blob = c.model_dump_json()
    parsed = CapabilitiesPayload.model_validate_json(blob)
    assert parsed.firmware_version == "1.2.3"
    assert parsed.uptime_s == 42.5


def test_capabilities_omits_uptime_s_by_default():
    """uptime_s is Optional with None default — old firmware that doesn't
    yet emit it must validate without modification."""
    c = CapabilitiesPayload(
        capabilities=[
            Capability(channel="soil_moisture", hardware="Seesaw",
                       is_required=True, unit_label="raw"),
        ],
        firmware_version="1.2.3",
        hardware_serial="hw-1",
    )
    assert c.uptime_s is None


def test_ack_payload_was_removed():
    """AckPayload existed in an earlier draft but was deleted in Commit C2
    after the implementation deliberately deviated from spec — see
    `mlss_contracts.ws_messages` module docstring for the rationale.

    Pinning this in a test stops a "let's add it back" PR from being
    quietly merged: if there's a legitimate need (e.g. an ML training
    pipeline that needs reliable per-command callbacks), it should
    arrive with an updated spec section + a discussion of why the
    existing telemetry/event channels aren't enough.
    """
    with pytest.raises(ImportError):
        # pylint: disable=no-name-in-module,unused-import,import-outside-toplevel
        from mlss_contracts.ws_messages import AckPayload  # noqa: F401
