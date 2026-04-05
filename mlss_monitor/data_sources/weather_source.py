from __future__ import annotations

import logging
from datetime import datetime, timezone

from .base import DataSource, NormalisedReading

logger = logging.getLogger(__name__)


class WeatherAPISource(DataSource):
    def __init__(self, client, lat: float, lon: float) -> None:
        super().__init__()
        self._client = client
        self._lat = lat
        self._lon = lon

    @property
    def name(self) -> str:
        return "weather_api"

    def get_latest(self) -> NormalisedReading:
        try:
            data = self._client.get_current_weather(self._lat, self._lon)
            temp = float(data["temp"]) if data.get("temp") is not None else None
            humidity = float(data["humidity"]) if data.get("humidity") is not None else None
        except Exception as exc:
            logger.warning("WeatherAPISource: get_current_weather() failed: %s", exc)
            temp, humidity = None, None
        return NormalisedReading(
            timestamp=datetime.now(timezone.utc),
            source=self.name,
            temperature_c=temp,
            humidity_pct=humidity,
        )
