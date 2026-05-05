from mlss_contracts.capabilities import Capability
from mlss_contracts.enums import Channel
from pydantic import ValidationError
import pytest


def test_capability_required_fields():
    c = Capability(
        channel=Channel.SOIL_MOISTURE,
        hardware="Adafruit_Seesaw_4026",
        is_required=True,
        unit_label="raw",
    )
    assert c.channel == Channel.SOIL_MOISTURE
    assert c.hardware == "Adafruit_Seesaw_4026"
    assert c.is_required is True
    assert c.unit_label == "raw"
    assert c.details is None


def test_capability_optional_details_dict():
    c = Capability(
        channel=Channel.SOIL_MOISTURE,
        hardware="Adafruit_Seesaw_4026",
        is_required=True,
        unit_label="raw",
        details={"i2c_address": "0x36"},
    )
    assert c.details == {"i2c_address": "0x36"}


def test_capability_serialises_round_trip():
    c = Capability(
        channel=Channel.AMBIENT_LUX,
        hardware="TSL2591",
        is_required=False,
        unit_label="lux",
        details={"i2c_address": "0x29"},
    )
    blob = c.model_dump_json()
    parsed = Capability.model_validate_json(blob)
    assert parsed == c


def test_capability_rejects_unknown_channel():
    with pytest.raises(ValidationError):
        Capability(
            channel="not_a_real_channel",
            hardware="X",
            is_required=False,
            unit_label="x",
        )
