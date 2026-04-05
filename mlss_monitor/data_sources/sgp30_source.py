from __future__ import annotations

from datetime import datetime, timezone

from sensor_interfaces.sgp30 import read_sgp30
from .base import DataSource, NormalisedReading


class SGP30Source(DataSource):
    @property
    def name(self) -> str:
        return "sgp30"

    def get_latest(self) -> NormalisedReading:
        eco2, tvoc = read_sgp30()
        return NormalisedReading(
            timestamp=datetime.now(timezone.utc),
            source=self.name,
            eco2_ppm=float(eco2) if eco2 is not None else None,
            tvoc_ppb=float(tvoc) if tvoc is not None else None,
        )
