from __future__ import annotations

from datetime import datetime, timezone

from sensor_interfaces.sb_components_pm_sensor import read_pm
from .base import DataSource, NormalisedReading


class ParticulateSource(DataSource):
    """Wraps the module-level read_pm() from sensor_interfaces/sb_components_pm_sensor.py."""

    @property
    def name(self) -> str:
        return "pm_sensor"

    def get_latest(self) -> NormalisedReading:
        try:
            data = read_pm()
            pm25 = float(data["pm2_5"]) if data and "pm2_5" in data else None
        except Exception:
            pm25 = None
        return NormalisedReading(
            timestamp=datetime.now(timezone.utc),
            source=self.name,
            pm25_ug_m3=pm25,
        )
