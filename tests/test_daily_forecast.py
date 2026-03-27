# pylint: disable=redefined-outer-name
"""Tests for OpenMeteoClient.get_daily_forecast."""
import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from external_api_interfaces.open_meteo import OpenMeteoClient


@pytest.fixture
def client():
    return OpenMeteoClient()


def _mock_daily(n=7):
    days = [f"2024-01-{i+1:02d}" for i in range(n)]
    payload = {
        "daily": {
            "time":                          days,
            "temperature_2m_max":            [15.0 + i for i in range(n)],
            "temperature_2m_min":            [5.0  + i for i in range(n)],
            "precipitation_probability_max": [10   * i for i in range(n)],
            "weather_code":                  [1]       * n,
            "wind_speed_10m_max":            [8.0  + i for i in range(n)],
        }
    }
    return BytesIO(json.dumps(payload).encode())


def _cm(n=7):
    cm = MagicMock()
    cm.__enter__ = lambda s: _mock_daily(n)
    cm.__exit__  = MagicMock(return_value=False)
    return cm


@patch("urllib.request.urlopen")
def test_returns_seven_days(mock_urlopen, client):
    mock_urlopen.return_value = _cm(7)
    result = client.get_daily_forecast(51.5, -0.1)
    assert "days" in result
    assert len(result["days"]) == 7


@patch("urllib.request.urlopen")
def test_day_has_required_keys(mock_urlopen, client):
    mock_urlopen.return_value = _cm(3)
    result = client.get_daily_forecast(51.5, -0.1, days=3)
    day = result["days"][0]
    for key in ("date", "temp_max", "temp_min", "precip_prob", "weather_code", "wind_speed"):
        assert key in day, f"Missing key: {key}"


@patch("urllib.request.urlopen")
def test_url_contains_daily_params(mock_urlopen, client):
    mock_urlopen.return_value = _cm()
    client.get_daily_forecast(53.7, -1.5)
    url = mock_urlopen.call_args[0][0]
    assert "daily=" in url
    assert "temperature_2m_max" in url
    assert "53.7" in url


@patch("urllib.request.urlopen")
def test_network_error_propagates(mock_urlopen, client):
    mock_urlopen.side_effect = urllib.error.URLError("timeout")
    with pytest.raises(urllib.error.URLError):
        client.get_daily_forecast(51.5, -0.1)
