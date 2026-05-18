"""SeesawSoilSensor: detect-or-not, channels, read."""
from unittest.mock import MagicMock
from mlss_grow.sensors.seesaw import SeesawSoilSensor


def test_detect_returns_none_when_seesaw_lib_unavailable(monkeypatch):
    """On dev laptops without adafruit-circuitpython-seesaw installed."""
    monkeypatch.setattr("mlss_grow.sensors.seesaw._seesaw_module", None)
    assert SeesawSoilSensor.detect(i2c_bus=MagicMock()) is None


def test_detect_returns_instance_when_lib_present_and_device_responds(monkeypatch):
    fake_seesaw_module = MagicMock()
    fake_seesaw_module.Seesaw = MagicMock(return_value=MagicMock(
        moisture_read=MagicMock(return_value=612),
        get_temp=MagicMock(return_value=21.4),
    ))
    monkeypatch.setattr("mlss_grow.sensors.seesaw._seesaw_module", fake_seesaw_module)
    s = SeesawSoilSensor.detect(i2c_bus=MagicMock())
    assert s is not None
    assert isinstance(s, SeesawSoilSensor)


def test_detect_returns_none_when_device_not_present(monkeypatch):
    fake_seesaw_module = MagicMock()
    fake_seesaw_module.Seesaw = MagicMock(side_effect=OSError("no device"))
    monkeypatch.setattr("mlss_grow.sensors.seesaw._seesaw_module", fake_seesaw_module)
    assert SeesawSoilSensor.detect(i2c_bus=MagicMock()) is None


def test_channels_includes_moisture_and_temp():
    s = SeesawSoilSensor(driver=MagicMock())
    assert "soil_moisture" in s.channels()
    assert "soil_temp_c" in s.channels()


def test_read_returns_both_values():
    drv = MagicMock(moisture_read=MagicMock(return_value=612),
                    get_temp=MagicMock(return_value=21.4))
    s = SeesawSoilSensor(driver=drv)
    out = s.read()
    assert out["soil_moisture"] == 612
    assert out["soil_temp_c"] == 21.4


def test_read_marks_bad_read_when_value_out_of_sane_range():
    drv = MagicMock(moisture_read=MagicMock(return_value=50),  # below sane 200
                    get_temp=MagicMock(return_value=21))
    s = SeesawSoilSensor(driver=drv)
    s.read()
    assert s._bad_reads == 1
