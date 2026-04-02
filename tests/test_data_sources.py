from mlss_monitor.data_sources.base import NormalisedReading, DataSource
from datetime import datetime


def test_normalised_reading_all_none():
    r = NormalisedReading(timestamp=datetime.utcnow(), source="test")
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
        timestamp=datetime.utcnow(),
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

    ts = datetime.utcnow()
    r1 = NormalisedReading(timestamp=ts, source="sgp30", eco2_ppm=850.0, tvoc_ppb=120.0)
    r2 = NormalisedReading(timestamp=ts, source="aht20", temperature_c=21.5, humidity_pct=55.0)
    merged = merge_readings([r1, r2])
    assert merged.eco2_ppm == 850.0
    assert merged.tvoc_ppb == 120.0
    assert merged.temperature_c == 21.5
    assert merged.humidity_pct == 55.0
    assert merged.source == "merged"


def test_merge_readings_first_non_none_wins():
    from mlss_monitor.data_sources.base import merge_readings

    ts = datetime.utcnow()
    r1 = NormalisedReading(timestamp=ts, source="a", temperature_c=21.0)
    r2 = NormalisedReading(timestamp=ts, source="b", temperature_c=99.0)
    merged = merge_readings([r1, r2])
    assert merged.temperature_c == 21.0


def test_merge_readings_empty_list():
    from mlss_monitor.data_sources.base import merge_readings

    merged = merge_readings([])
    assert merged.tvoc_ppb is None
    assert merged.source == "merged"
