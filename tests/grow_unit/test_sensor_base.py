"""Sensor ABC defines the .detect / .channels / .read / .healthy contract."""
from mlss_grow.sensors.base import Sensor
import pytest


class FakeSensor(Sensor):
    @classmethod
    def detect(cls, i2c_bus):
        return cls()

    def channels(self):
        return ["soil_moisture"]

    def read(self):
        return {"soil_moisture": 612}


def test_sensor_subclass_can_be_instantiated_and_used():
    s = FakeSensor.detect(i2c_bus=None)
    assert s is not None
    assert s.channels() == ["soil_moisture"]
    assert s.read() == {"soil_moisture": 612}


def test_sensor_default_healthy_starts_true():
    s = FakeSensor()
    assert s.healthy() is True


def test_sensor_marks_unhealthy_after_consecutive_bad_reads():
    s = FakeSensor()
    s.record_bad_read()
    s.record_bad_read()
    assert s.healthy() is True   # threshold 3
    s.record_bad_read()
    assert s.healthy() is False


def test_sensor_recovery_resets_bad_count():
    s = FakeSensor()
    for _ in range(3):
        s.record_bad_read()
    assert s.healthy() is False
    s.record_good_read()
    assert s.healthy() is True


def test_abstract_methods_must_be_implemented():
    with pytest.raises(TypeError):
        Sensor()  # cannot instantiate abstract class directly
