from mlss_monitor.data_sources.base import NormalisedReading, DataSource
from datetime import datetime, timezone


def test_normalised_reading_all_none():
    r = NormalisedReading(timestamp=datetime.now(timezone.utc), source="test")
    assert r.tvoc_ppb is None
    assert r.eco2_ppm is None
    assert r.temperature_c is None
    assert r.humidity_pct is None
    assert r.pm25_ug_m3 is None
    assert r.co_ppb is None
    assert r.no2_ppb is None
    assert r.nh3_ppb is None


def test_normalised_reading_with_values():
    r = NormalisedReading(
        timestamp=datetime.now(timezone.utc),
        source="sgp30",
        eco2_ppm=850.0,
        tvoc_ppb=120.0,
    )
    assert r.eco2_ppm == 850.0
    assert r.tvoc_ppb == 120.0
    assert r.temperature_c is None


def test_data_source_is_abstract():
    import pytest
    with pytest.raises(TypeError):
        DataSource()


def test_merge_readings_combines_fields():
    from mlss_monitor.data_sources.base import merge_readings

    ts = datetime.now(timezone.utc)
    r1 = NormalisedReading(timestamp=ts, source="sgp30", eco2_ppm=850.0, tvoc_ppb=120.0)
    r2 = NormalisedReading(timestamp=ts, source="aht20", temperature_c=21.5, humidity_pct=55.0)
    before = datetime.now(timezone.utc)
    merged = merge_readings([r1, r2])
    after = datetime.now(timezone.utc)
    assert merged.eco2_ppm == 850.0
    assert merged.tvoc_ppb == 120.0
    assert merged.temperature_c == 21.5
    assert merged.humidity_pct == 55.0
    assert merged.source == "merged"
    assert before <= merged.timestamp <= after


def test_merge_readings_first_non_none_wins():
    from mlss_monitor.data_sources.base import merge_readings

    ts = datetime.now(timezone.utc)
    r1 = NormalisedReading(timestamp=ts, source="a", temperature_c=21.0)
    r2 = NormalisedReading(timestamp=ts, source="b", temperature_c=99.0)
    before = datetime.now(timezone.utc)
    merged = merge_readings([r1, r2])
    after = datetime.now(timezone.utc)
    assert merged.temperature_c == 21.0
    assert before <= merged.timestamp <= after


def test_merge_readings_empty_list():
    from mlss_monitor.data_sources.base import merge_readings

    before = datetime.now(timezone.utc)
    merged = merge_readings([])
    after = datetime.now(timezone.utc)
    assert merged.tvoc_ppb is None
    assert merged.source == "merged"
    assert before <= merged.timestamp <= after


from unittest.mock import patch
from mlss_monitor.data_sources.sgp30_source import SGP30Source
from mlss_monitor.data_sources.aht20_source import AHT20Source


def test_sgp30_source_name():
    with patch("mlss_monitor.data_sources.sgp30_source.read_sgp30", return_value=(850, 120)):
        source = SGP30Source()
        assert source.name == "sgp30"


def test_sgp30_source_returns_reading():
    with patch("mlss_monitor.data_sources.sgp30_source.read_sgp30", return_value=(850, 120)):
        source = SGP30Source()
        reading = source.get_latest()
        assert reading.eco2_ppm == 850.0
        assert reading.tvoc_ppb == 120.0
        assert reading.source == "sgp30"
        assert reading.temperature_c is None


def test_sgp30_source_handles_none():
    with patch("mlss_monitor.data_sources.sgp30_source.read_sgp30", return_value=(None, None)):
        source = SGP30Source()
        reading = source.get_latest()
        assert reading.eco2_ppm is None
        assert reading.tvoc_ppb is None


def test_aht20_source_name():
    with patch("mlss_monitor.data_sources.aht20_source.read_aht20", return_value=(21.5, 55.0)):
        source = AHT20Source()
        assert source.name == "aht20"


def test_aht20_source_returns_reading():
    with patch("mlss_monitor.data_sources.aht20_source.read_aht20", return_value=(21.5, 55.0)):
        source = AHT20Source()
        reading = source.get_latest()
        assert reading.temperature_c == 21.5
        assert reading.humidity_pct == 55.0
        assert reading.source == "aht20"
        assert reading.tvoc_ppb is None


from mlss_monitor.data_sources.pm_source import ParticulateSource
from mlss_monitor.data_sources.mics6814_source import MICS6814Source


def test_particulate_source_name():
    source = ParticulateSource()
    assert source.name == "pm_sensor"


def test_particulate_source_returns_pm25():
    with patch(
        "mlss_monitor.data_sources.pm_source.read_pm",
        return_value={"pm1_0": 5, "pm2_5": 12, "pm10": 18},
    ):
        source = ParticulateSource()
        reading = source.get_latest()
        assert reading.pm25_ug_m3 == 12.0
        assert reading.source == "pm_sensor"
        assert reading.tvoc_ppb is None


def test_particulate_source_handles_none():
    with patch("mlss_monitor.data_sources.pm_source.read_pm", return_value=None):
        source = ParticulateSource()
        reading = source.get_latest()
        assert reading.pm25_ug_m3 is None


def test_particulate_source_handles_exception():
    with patch(
        "mlss_monitor.data_sources.pm_source.read_pm",
        side_effect=Exception("UART timeout"),
    ):
        source = ParticulateSource()
        reading = source.get_latest()
        assert reading.pm25_ug_m3 is None


def test_mics6814_source_name():
    source = MICS6814Source()
    assert source.name == "mics6814"


def test_mics6814_source_returns_gas_readings():
    with patch(
        "mlss_monitor.data_sources.mics6814_source.read_mics6814",
        return_value=(1.23, 0.05, 8.7),
    ):
        source = MICS6814Source()
        reading = source.get_latest()
        assert reading.co_ppb == 1.23
        assert reading.no2_ppb == 0.05
        assert reading.nh3_ppb == 8.7
        assert reading.source == "mics6814"


def test_mics6814_source_handles_none_tuple():
    with patch(
        "mlss_monitor.data_sources.mics6814_source.read_mics6814",
        return_value=(None, None, None),
    ):
        source = MICS6814Source()
        reading = source.get_latest()
        assert reading.co_ppb is None
        assert reading.no2_ppb is None
        assert reading.nh3_ppb is None
