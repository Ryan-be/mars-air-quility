from __future__ import annotations

from datetime import datetime, timezone

from sensor_interfaces.aht20 import read_aht20
from .base import DataSource, NormalisedReading


class AHT20Source(DataSource):
    @property
    def name(self) -> str:
        return "aht20"

    def get_latest(self) -> NormalisedReading:
        temp, humidity = read_aht20()
        return NormalisedReading(
            timestamp=datetime.now(timezone.utc),
            source=self.name,
            temperature_c=float(temp) if temp is not None else None,
            humidity_pct=float(humidity) if humidity is not None else None,
        )
