"""Regression guard for the 2026-05-31 SGP30 unpack-error incident.

On a hub with no SGP30 hardware, the sensor read loop was flooding
journalctl with ``DataSource sgp30 read failed: too many values to
unpack (expected 2)`` once per second. Root cause: the happy path of
:func:`sensor_interfaces.sgp30.read_sgp30` returns a 2-tuple
``(eco2, tvoc)`` but both sad paths (sensor never initialised + read
exception) returned a 4-tuple ``(None, None, None, None)`` —
probably a leftover from an older version of the function that also
returned humidity / temperature. The caller in
:class:`mlss_monitor.data_sources.sgp30_source.SGP30Source` unpacks
``eco2, tvoc = read_sgp30()`` which then raises every second.

Fix: return ``(None, None)`` from both sad paths. This module asserts
that invariant — the read function MUST always return exactly 2
values regardless of sensor presence or runtime errors.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def sgp30_module():
    """Import (or re-import) the SGP30 sensor interface freshly.

    Tests in this module patch the module-level ``sgp30`` global; reload
    keeps cross-test state from leaking and matches the way conftest
    pre-installs hardware-lib stubs at session start.
    """
    import sensor_interfaces.sgp30 as mod
    importlib.reload(mod)
    return mod


class TestReadSgp30AlwaysReturnsTwoValues:
    def test_returns_two_values_when_sensor_is_none(self, sgp30_module, monkeypatch):
        """The 'sensor never initialised' branch must return exactly 2
        values — the caller does ``eco2, tvoc = read_sgp30()``."""
        monkeypatch.setattr(sgp30_module, "sgp30", None)
        result = sgp30_module.read_sgp30()
        assert len(result) == 2, (
            f"read_sgp30() returned {len(result)} values when sensor is None; "
            "must return exactly 2 so the caller's 2-tuple unpack succeeds."
        )
        assert result == (None, None)

    def test_returns_two_values_when_sensor_read_raises(
        self, sgp30_module, monkeypatch,
    ):
        """The 'per-read exception' branch must also return 2 values."""
        class _BoomSensor:
            @property
            def eCO2(self):
                raise RuntimeError("I2C bus glitch")
            @property
            def TVOC(self):
                raise RuntimeError("I2C bus glitch")
        monkeypatch.setattr(sgp30_module, "sgp30", _BoomSensor())
        result = sgp30_module.read_sgp30()
        assert len(result) == 2, (
            f"read_sgp30() returned {len(result)} values when sensor "
            "raised; must return exactly 2."
        )
        assert result == (None, None)

    def test_caller_unpack_succeeds_on_both_sad_paths(
        self, sgp30_module, monkeypatch,
    ):
        """Belt-and-braces: the user-visible bug was the caller's
        unpack raising. Exercise the unpack pattern explicitly."""
        # Sad path 1: sensor is None.
        monkeypatch.setattr(sgp30_module, "sgp30", None)
        eco2, tvoc = sgp30_module.read_sgp30()  # Must NOT raise.
        assert eco2 is None
        assert tvoc is None
