from __future__ import annotations

from datetime import datetime, timezone

from sensor_interfaces.bmp280 import read_bmp280
from .base import DataSource, NormalisedReading


class BMP280Source(DataSource):
    @property
    def name(self) -> str:
        return "bmp280"

    def get_latest(self) -> NormalisedReading:
        pressure = read_bmp280()
        return NormalisedReading(
            timestamp=datetime.now(timezone.utc),
            source=self.name,
            pressure_hpa=float(pressure) if pressure is not None else None,
        )