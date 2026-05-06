"""Unit tests for the inbound-payload validation map in api_grow_ws.

These don't spin up the WS server — they exercise the payload-model
mapping directly. The end-to-end behaviour (drop bad frames, keep
connection up, log warning) is covered in test_grow_ws.py.
"""
import logging
from unittest.mock import patch

import pytest


def test_validation_map_covers_all_handler_message_types():
    """Every msg_type that a handler exists for must have an entry in
    the pydantic validation map. If a new handler is added without a
    matching schema, fail loudly here rather than silently letting bad
    payloads through."""
    from mlss_monitor.routes.api_grow_ws import _PAYLOAD_VALIDATORS

    # The handler dispatch in _connection_handler maps these msg_types
    # to handler functions; each must have a validator.
    expected = {"telemetry", "capabilities", "event"}
    assert expected.issubset(_PAYLOAD_VALIDATORS.keys()), (
        f"missing validators for {expected - _PAYLOAD_VALIDATORS.keys()}"
    )


def test_validation_map_telemetry_uses_telemetry_payload():
    from mlss_monitor.routes.api_grow_ws import _PAYLOAD_VALIDATORS
    from mlss_contracts.ws_messages import TelemetryPayload
    assert _PAYLOAD_VALIDATORS["telemetry"] is TelemetryPayload


def test_validation_map_capabilities_uses_capabilities_payload():
    from mlss_monitor.routes.api_grow_ws import _PAYLOAD_VALIDATORS
    from mlss_contracts.ws_messages import CapabilitiesPayload
    assert _PAYLOAD_VALIDATORS["capabilities"] is CapabilitiesPayload


def test_validation_map_event_uses_event_payload():
    from mlss_monitor.routes.api_grow_ws import _PAYLOAD_VALIDATORS
    from mlss_contracts.ws_messages import EventPayload
    assert _PAYLOAD_VALIDATORS["event"] is EventPayload


def test_validate_payload_returns_true_for_valid_telemetry():
    from mlss_monitor.routes.api_grow_ws import _validate_payload
    assert _validate_payload(
        "telemetry",
        {"soil_moisture_raw": 612, "light_state": True, "pump_state": False},
    ) is True


def test_validate_payload_returns_false_for_missing_required():
    from mlss_monitor.routes.api_grow_ws import _validate_payload
    # Missing required soil_moisture_raw
    assert _validate_payload(
        "telemetry",
        {"light_state": True, "pump_state": False},
    ) is False


def test_validate_payload_returns_false_for_wrong_type():
    from mlss_monitor.routes.api_grow_ws import _validate_payload
    # soil_moisture_raw must be int, not a list
    assert _validate_payload(
        "telemetry",
        {"soil_moisture_raw": [], "light_state": True, "pump_state": False},
    ) is False


def test_validate_payload_logs_warning_on_failure(caplog):
    from mlss_monitor.routes.api_grow_ws import _validate_payload
    with caplog.at_level(logging.WARNING, logger="mlss_monitor.routes.api_grow_ws"):
        _validate_payload("telemetry", {"light_state": True})
    assert any(r.levelno >= logging.WARNING for r in caplog.records), (
        "validation failure must log a warning"
    )


def test_validate_payload_unknown_type_returns_true_when_no_validator():
    """If a msg_type doesn't appear in the validator map (e.g. 'ack' which
    is log-only, or a future type before its schema is added), don't
    block it — the dispatcher decides what to do with it.
    """
    from mlss_monitor.routes.api_grow_ws import _validate_payload
    assert _validate_payload("ack", {"in_reply_to_command": "abc",
                                     "success": True}) is True
    assert _validate_payload("definitely_not_a_type", {}) is True


def test_validate_payload_event_invalid_kind_rejected():
    """The pydantic EventKind enum is the line of defence against junk
    `kind` values landing in grow_errors / grow_watering_events."""
    from mlss_monitor.routes.api_grow_ws import _validate_payload
    assert _validate_payload(
        "event",
        {"kind": "not_a_real_event_kind", "details": {}},
    ) is False


def test_validate_payload_capabilities_invalid_channel_rejected():
    """Channel enum guards what gets written to grow_unit_capabilities."""
    from mlss_monitor.routes.api_grow_ws import _validate_payload
    assert _validate_payload(
        "capabilities",
        {
            "capabilities": [
                {"channel": "not_a_real_channel", "hardware": "X",
                 "is_required": True, "unit_label": "raw"},
            ],
            "firmware_version": "0.1.0",
            "hardware_serial": "hw1",
        },
    ) is False
