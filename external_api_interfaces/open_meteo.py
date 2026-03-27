"""
Open-Meteo API client
=====================
Docs         : https://open-meteo.com/
Geocoding API: https://open-meteo.com/en/docs/geocoding-api

UK postcode & outward-code support is handled via postcodes.io
(https://postcodes.io) — a free, open-source Royal Mail dataset API.

Examples
--------
>>> client = OpenMeteoClient()
>>> client.geocode("LS26")          # UK outcode  → lat/lon from postcodes.io
>>> client.geocode("LS26 0AU")      # Full UK postcode
>>> client.geocode("London")        # Place name  → Open-Meteo geocoding
>>> client.get_current_weather(53.7, -1.5)
"""

import json
import re
import urllib.request
import urllib.parse

# --------------------------------------------------------------------------- #
# UK postcode patterns
# --------------------------------------------------------------------------- #
# Full postcode  e.g. "LS26 0AU"  "SW1A 2AA"  "EC1A 1BB"
UK_FULL_POSTCODE_RE = re.compile(
    r"^[A-Z]{1,2}[0-9][A-Z0-9]?\s?[0-9][A-Z]{2}$", re.IGNORECASE
)
# Outward code only  e.g. "LS26"  "SW1A"  "EC1A"
UK_OUTCODE_RE = re.compile(
    r"^[A-Z]{1,2}[0-9][A-Z0-9]?$", re.IGNORECASE
)


class OpenMeteoClient:
    """Thin synchronous wrapper around Open-Meteo + postcodes.io."""

    GEOCODE_URL   = "https://geocoding-api.open-meteo.com/v1/search"
    FORECAST_URL  = "https://api.open-meteo.com/v1/forecast"
    POSTCODES_URL = "https://api.postcodes.io"

    # ------------------------------------------------------------------ #
    # Geocoding
    # ------------------------------------------------------------------ #

    def geocode(self, query: str, timeout: int = 5) -> list:
        """
        Search for a location by name or UK postcode/outcode.

        Strategy:
        1. If the query matches a full UK postcode → postcodes.io /postcodes
        2. If it matches a UK outcode (e.g. LS26) → postcodes.io /outcodes
        3. Otherwise → Open-Meteo geocoding API

        Returns a list of result dicts::

            [{"name": str, "lat": float, "lon": float, "display": str}, ...]
        """
        q = query.strip()

        if UK_FULL_POSTCODE_RE.match(q):
            result = self._postcodes_io_full(q.replace(" ", "").upper(), timeout)
            if result:
                return [result]

        elif UK_OUTCODE_RE.match(q):
            result = self._postcodes_io_out(q.upper(), timeout)
            if result:
                return [result]

        return self._geocode_by_name(q, timeout)

    def _postcodes_io_full(self, postcode: str, timeout: int):
        """Look up a full UK postcode via postcodes.io."""
        url = f"{self.POSTCODES_URL}/postcodes/{urllib.parse.quote(postcode)}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                d = json.loads(resp.read())
            if d.get("status") == 200:
                r = d["result"]
                district = r.get("admin_district") or "UK"
                return {
                    "name":    postcode,
                    "lat":     r["latitude"],
                    "lon":     r["longitude"],
                    "display": f"{postcode}, {district}, UK",
                }
        except Exception:
            pass
        return None

    def _postcodes_io_out(self, outcode: str, timeout: int):
        """Look up a UK outward code (e.g. LS26) via postcodes.io."""
        url = f"{self.POSTCODES_URL}/outcodes/{urllib.parse.quote(outcode)}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                d = json.loads(resp.read())
            if d.get("status") == 200:
                r = d["result"]
                districts = r.get("admin_district") or []
                district = districts[0] if districts else "UK"
                return {
                    "name":    outcode,
                    "lat":     r["latitude"],
                    "lon":     r["longitude"],
                    "display": f"{outcode}, {district}, UK",
                }
        except Exception:
            pass
        return None

    def _geocode_by_name(self, query: str, timeout: int) -> list:
        """Fall back to Open-Meteo place-name geocoding."""
        url = (
            f"{self.GEOCODE_URL}"
            f"?name={urllib.parse.quote(query)}&count=5&language=en&format=json"
        )
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                d = json.loads(resp.read())
            return [
                {
                    "name":    r.get("name", ""),
                    "lat":     r["latitude"],
                    "lon":     r["longitude"],
                    "display": ", ".join(
                        filter(None, [r.get("name"), r.get("admin1"), r.get("country")])
                    ),
                }
                for r in d.get("results", [])
            ]
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # Current weather
    # ------------------------------------------------------------------ #

    def get_current_weather(self, lat: float, lon: float, timeout: int = 8) -> dict:
        """
        Fetch current weather conditions from Open-Meteo.

        Returns::

            {
                "temp": float,          # °C
                "humidity": int,        # %
                "feels_like": float,    # °C
                "wind_speed": float,    # mph
                "weather_code": int,    # WMO code
                "uv_index": float,
                "source": "Open-Meteo",
            }

        Raises ``urllib.error.URLError`` or ``KeyError`` on failure —
        callers should wrap in try/except.
        """
        url = (
            f"{self.FORECAST_URL}"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
            f"weather_code,wind_speed_10m,uv_index"
            f"&wind_speed_unit=mph&temperature_unit=celsius"
        )
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            d = json.loads(resp.read())
        c = d["current"]
        return {
            "temp":         c.get("temperature_2m"),
            "humidity":     c.get("relative_humidity_2m"),
            "feels_like":   c.get("apparent_temperature"),
            "wind_speed":   c.get("wind_speed_10m"),
            "weather_code": c.get("weather_code"),
            "uv_index":     c.get("uv_index"),
            "source":       "Open-Meteo",
        }
