"""Tests for the shared `make_min_le_max_validator` factory.

The factory is consumed by both `mlss_contracts.config_payloads.PIDUpdate`
and `mlss_monitor.routes.api_grow_settings._ProfileUpdate`. These tests
cover the factory in isolation against a minimal pydantic model so the
behaviour stays pinned independently of either consumer.
"""
from typing import Optional

import pytest
from pydantic import BaseModel, ValidationError

from mlss_contracts._validators import make_min_le_max_validator


class _MinMaxModel(BaseModel):
    """Minimal pydantic model under test — mirrors the shape of
    PIDUpdate / _ProfileUpdate where both fields are optional."""
    min_pulse_s: Optional[float] = None
    max_pulse_s: Optional[float] = None
    _min_le_max = make_min_le_max_validator("min_pulse_s", "max_pulse_s")


def test_min_le_max_validator_allows_when_both_none():
    # Empty payload — partial update without either field. No comparison
    # to perform → must not raise.
    model = _MinMaxModel()
    assert model.min_pulse_s is None
    assert model.max_pulse_s is None


def test_min_le_max_validator_allows_when_only_min_set():
    # Only one of the pair set; the absent field defaults to NULL on the
    # underlying row, so no constraint exists yet → must not raise.
    model = _MinMaxModel(min_pulse_s=2.0)
    assert model.min_pulse_s == 2.0
    assert model.max_pulse_s is None


def test_min_le_max_validator_allows_when_only_max_set():
    model = _MinMaxModel(max_pulse_s=10.0)
    assert model.max_pulse_s == 10.0
    assert model.min_pulse_s is None


def test_min_le_max_validator_allows_when_min_lt_max():
    model = _MinMaxModel(min_pulse_s=2.0, max_pulse_s=5.0)
    assert model.min_pulse_s == 2.0
    assert model.max_pulse_s == 5.0


def test_min_le_max_validator_allows_when_min_eq_max():
    # Equal is fine (zero-width pulse window — pump always pulses for
    # exactly that many seconds, no pid latitude). The check is `<=`.
    model = _MinMaxModel(min_pulse_s=3.0, max_pulse_s=3.0)
    assert model.min_pulse_s == 3.0
    assert model.max_pulse_s == 3.0


def test_min_le_max_validator_rejects_when_min_gt_max():
    with pytest.raises(ValidationError):
        _MinMaxModel(min_pulse_s=10.0, max_pulse_s=5.0)


def test_min_le_max_validator_rejects_with_helpful_field_names_in_error():
    """The error message must reference the field names so a frontend
    pulling the pydantic error list back to the user sees something
    actionable rather than a generic 'invalid value' string."""
    with pytest.raises(ValidationError) as exc_info:
        _MinMaxModel(min_pulse_s=10.0, max_pulse_s=5.0)
    msg = str(exc_info.value)
    assert "min_pulse_s" in msg
    assert "max_pulse_s" in msg
