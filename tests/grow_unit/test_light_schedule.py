"""is_light_on: pure function over (now, list of (start, end) windows)."""
from datetime import datetime, time
from mlss_grow.light_schedule import is_light_on, parse_window


def test_inside_simple_window():
    windows = [parse_window("06:00", "22:00")]
    assert is_light_on(datetime(2026, 5, 3, 12, 0), windows) is True


def test_outside_simple_window():
    windows = [parse_window("06:00", "22:00")]
    assert is_light_on(datetime(2026, 5, 3, 4, 0), windows) is False
    assert is_light_on(datetime(2026, 5, 3, 23, 0), windows) is False


def test_window_inclusive_at_start_exclusive_at_end():
    """06:00:00 ON, 22:00:00 OFF (i.e. light is on for [06:00, 22:00))."""
    windows = [parse_window("06:00", "22:00")]
    assert is_light_on(datetime(2026, 5, 3, 6, 0, 0), windows) is True
    assert is_light_on(datetime(2026, 5, 3, 22, 0, 0), windows) is False
    assert is_light_on(datetime(2026, 5, 3, 21, 59, 59), windows) is True


def test_overnight_window():
    """22:00 → 06:00 (overnight, end < start). On overnight."""
    windows = [parse_window("22:00", "06:00")]
    assert is_light_on(datetime(2026, 5, 3, 23, 0), windows) is True
    assert is_light_on(datetime(2026, 5, 3, 5, 0), windows) is True
    assert is_light_on(datetime(2026, 5, 3, 12, 0), windows) is False


def test_multiple_windows_per_day():
    """06:00-12:00 + 14:00-22:00 (midday off)."""
    windows = [parse_window("06:00", "12:00"), parse_window("14:00", "22:00")]
    assert is_light_on(datetime(2026, 5, 3, 8, 0), windows) is True
    assert is_light_on(datetime(2026, 5, 3, 13, 0), windows) is False
    assert is_light_on(datetime(2026, 5, 3, 16, 0), windows) is True
    assert is_light_on(datetime(2026, 5, 3, 23, 0), windows) is False


def test_empty_window_list_is_off():
    assert is_light_on(datetime.utcnow(), []) is False


def test_parse_window_rejects_invalid():
    import pytest
    with pytest.raises(ValueError):
        parse_window("25:00", "10:00")
    with pytest.raises(ValueError):
        parse_window("06:00", "")
