"""
Unit tests for OpenMeteoClient
================================
All HTTP calls are mocked — no real network required.

Coverage:
  - UK full postcode detection and postcodes.io lookup  (e.g. LS26 0AU)
  - UK outcode detection and postcodes.io lookup        (e.g. LS26)
  - Place-name geocoding via Open-Meteo
  - Graceful fallback when postcodes.io returns non-200
  - Graceful fallback when postcodes.io raises a network error
  - get_current_weather() happy path
  - get_current_weather() propagates network errors (caller should wrap)
  - Regex patterns accept / reject representative strings
"""
import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from external_api_interfaces.open_meteo import OpenMeteoClient, UK_FULL_POSTCODE_RE, UK_OUTCODE_RE


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _urlopen_ctx(data: dict):
    """Context-manager mock for urllib.request.urlopen that returns `data`."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(data).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


@pytest.fixture
def client():
    return OpenMeteoClient()


# --------------------------------------------------------------------------- #
# Regex pattern tests (no network)
# --------------------------------------------------------------------------- #

class TestPostcodeRegex:
    """Verify the compiled patterns accept / reject representative strings."""

    FULL_VALID = ["LS26 0AU", "SW1A 2AA", "EC1A 1BB", "W1A 1AA", "B1 1BB", "LS260AU"]
    FULL_INVALID = ["LS26", "London", "12345", "SW1A"]

    OUT_VALID = ["LS26", "SW1A", "EC1A", "W1A", "B1", "M1"]
    OUT_INVALID = ["London", "12345", "LS26 0AU", "SW1A 2AA", ""]

    @pytest.mark.parametrize("postcode", FULL_VALID)
    def test_full_postcode_accepted(self, postcode):
        assert UK_FULL_POSTCODE_RE.match(postcode), f"Expected match for {postcode!r}"

    @pytest.mark.parametrize("postcode", FULL_INVALID)
    def test_full_postcode_rejected(self, postcode):
        assert not UK_FULL_POSTCODE_RE.match(postcode), f"Expected no match for {postcode!r}"

    @pytest.mark.parametrize("outcode", OUT_VALID)
    def test_outcode_accepted(self, outcode):
        assert UK_OUTCODE_RE.match(outcode), f"Expected match for {outcode!r}"

    @pytest.mark.parametrize("outcode", OUT_INVALID)
    def test_outcode_rejected(self, outcode):
        assert not UK_OUTCODE_RE.match(outcode), f"Expected no match for {outcode!r}"


# --------------------------------------------------------------------------- #
# Geocoding — UK postcodes via postcodes.io
# --------------------------------------------------------------------------- #

class TestGeocodeUKPostcode:

    @patch("urllib.request.urlopen")
    def test_full_postcode_returns_single_result(self, mock_open, client):
        mock_open.return_value = _urlopen_ctx({
            "status": 200,
            "result": {"latitude": 53.745, "longitude": -1.504, "admin_district": "Leeds"},
        })
        results = client.geocode("LS26 0AU")
        assert len(results) == 1
        assert abs(results[0]["lat"] - 53.745) < 0.001
        assert "Leeds" in results[0]["display"]
        assert "LS260AU" in results[0]["display"]

    @patch("urllib.request.urlopen")
    def test_full_postcode_case_insensitive(self, mock_open, client):
        mock_open.return_value = _urlopen_ctx({
            "status": 200,
            "result": {"latitude": 53.745, "longitude": -1.504, "admin_district": "Leeds"},
        })
        results = client.geocode("ls26 0au")
        assert len(results) == 1

    @patch("urllib.request.urlopen")
    def test_outcode_ls26_returns_single_result(self, mock_open, client):
        mock_open.return_value = _urlopen_ctx({
            "status": 200,
            "result": {
                "latitude": 53.726, "longitude": -1.500,
                "admin_district": ["Leeds"],
            },
        })
        results = client.geocode("LS26")
        assert len(results) == 1
        assert abs(results[0]["lat"] - 53.726) < 0.001
        assert "Leeds" in results[0]["display"]

    @patch("urllib.request.urlopen")
    def test_outcode_with_alpha_suffix(self, mock_open, client):
        """SW1A is a valid outcode (alphanumeric sector)."""
        mock_open.return_value = _urlopen_ctx({
            "status": 200,
            "result": {"latitude": 51.499, "longitude": -0.125, "admin_district": ["Westminster"]},
        })
        results = client.geocode("SW1A")
        assert len(results) == 1
        assert "Westminster" in results[0]["display"]

    @patch("urllib.request.urlopen")
    def test_postcodes_io_non_200_falls_back_to_open_meteo(self, mock_open, client):
        """If postcodes.io returns status != 200 for an outcode, fall back to name search."""
        # First call: postcodes.io returns 404-style payload
        postcodes_resp = _urlopen_ctx({"status": 404, "error": "Outcode not found"})
        # Second call: Open-Meteo geocoding
        open_meteo_resp = _urlopen_ctx({
            "results": [{"name": "Leeds", "latitude": 53.8, "longitude": -1.54,
                         "admin1": "England", "country": "United Kingdom"}]
        })
        mock_open.side_effect = [postcodes_resp, open_meteo_resp]
        results = client.geocode("LS26")
        assert len(results) == 1
        assert results[0]["name"] == "Leeds"

    @patch("urllib.request.urlopen")
    def test_postcodes_io_network_error_falls_back(self, mock_open, client):
        """Network failure on postcodes.io should fall back to Open-Meteo."""
        open_meteo_resp = _urlopen_ctx({
            "results": [{"name": "Leeds", "latitude": 53.8, "longitude": -1.54,
                         "admin1": "England", "country": "United Kingdom"}]
        })
        mock_open.side_effect = [OSError("connection refused"), open_meteo_resp]
        results = client.geocode("LS26")
        assert len(results) == 1


# --------------------------------------------------------------------------- #
# Geocoding — place name via Open-Meteo
# --------------------------------------------------------------------------- #

class TestGeocodeByName:

    @patch("urllib.request.urlopen")
    def test_place_name_returns_results(self, mock_open, client):
        mock_open.return_value = _urlopen_ctx({
            "results": [
                {"name": "London", "latitude": 51.507, "longitude": -0.127,
                 "admin1": "England", "country": "United Kingdom"},
                {"name": "London", "latitude": 42.983, "longitude": -81.233,
                 "admin1": "Ontario", "country": "Canada"},
            ]
        })
        results = client.geocode("London")
        assert len(results) == 2
        assert results[0]["name"] == "London"
        assert "England" in results[0]["display"]

    @patch("urllib.request.urlopen")
    def test_empty_open_meteo_results(self, mock_open, client):
        mock_open.return_value = _urlopen_ctx({"results": []})
        results = client.geocode("XYZ_nowhere")
        assert results == []

    @patch("urllib.request.urlopen")
    def test_network_error_returns_empty_list(self, mock_open, client):
        mock_open.side_effect = OSError("timeout")
        results = client.geocode("London")
        assert results == []


# --------------------------------------------------------------------------- #
# Current weather
# --------------------------------------------------------------------------- #

class TestGetCurrentWeather:

    MOCK_RESPONSE = {
        "current": {
            "temperature_2m": 14.2,
            "relative_humidity_2m": 78,
            "apparent_temperature": 12.1,
            "weather_code": 3,
            "wind_speed_10m": 11.5,
            "uv_index": 1.0,
        }
    }

    @patch("urllib.request.urlopen")
    def test_returns_correct_fields(self, mock_open, client):
        mock_open.return_value = _urlopen_ctx(self.MOCK_RESPONSE)
        w = client.get_current_weather(53.726, -1.500)
        assert w["temp"] == 14.2
        assert w["humidity"] == 78
        assert w["feels_like"] == 12.1
        assert w["weather_code"] == 3
        assert w["wind_speed"] == 11.5
        assert w["uv_index"] == 1.0
        assert w["source"] == "Open-Meteo"

    @patch("urllib.request.urlopen")
    def test_request_url_contains_lat_lon(self, mock_open, client):
        mock_open.return_value = _urlopen_ctx(self.MOCK_RESPONSE)
        client.get_current_weather(53.726, -1.500)
        called_url = mock_open.call_args[0][0]
        assert "53.726" in called_url
        assert "-1.5" in called_url

    @patch("urllib.request.urlopen")
    def test_network_error_propagates(self, mock_open, client):
        """Callers must wrap in try/except — the client does not swallow errors."""
        mock_open.side_effect = OSError("unreachable")
        with pytest.raises(OSError):
            client.get_current_weather(53.726, -1.500)

    @patch("urllib.request.urlopen")
    def test_none_fields_handled(self, mock_open, client):
        """Open-Meteo may omit fields (e.g. uv_index at night) — must not KeyError."""
        mock_open.return_value = _urlopen_ctx({
            "current": {
                "temperature_2m": 10.0,
                "relative_humidity_2m": 90,
                "apparent_temperature": 8.0,
                "weather_code": 61,
                "wind_speed_10m": 5.0,
                # uv_index deliberately omitted
            }
        })
        w = client.get_current_weather(53.726, -1.500)
        assert w["uv_index"] is None  # .get() returns None for missing keys
