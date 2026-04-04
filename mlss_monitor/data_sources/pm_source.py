from __future__ import annotations

import logging
from datetime import datetime, timezone

from sensor_interfaces.sb_components_pm_sensor import read_pm
from .base import DataSource, NormalisedReading

logger = logging.getLogger(__name__)


class ParticulateSource(DataSource):

    def __init__(self) -> None:
        self._last_pm1:  float | None = None
        self._last_pm25: float | None = None
        self._last_pm10: float | None = None

    @property
    def name(self) -> str:
        return "pm_sensor"

    def get_latest(self) -> NormalisedReading:
        try:
            data = read_pm()
            if data:
                if "pm1_0" in data:
                    self._last_pm1 = float(data["pm1_0"])
                if "pm2_5" in data:
                    self._last_pm25 = float(data["pm2_5"])
                if "pm10" in data:
                    self._last_pm10 = float(data["pm10"])
        except Exception as exc:
            logger.warning("ParticulateSource: read_pm() failed: %s", exc)
        return NormalisedReading(
            timestamp=datetime.now(timezone.utc),
            source=self.name,
            pm1_ug_m3=self._last_pm1,
            pm25_ug_m3=self._last_pm25,
            pm10_ug_m3=self._last_pm10,
        )
