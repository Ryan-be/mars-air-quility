from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

SENSOR_FIELDS: tuple[str, ...] = (
    "tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
    "pm1_ug_m3", "pm25_ug_m3", "pm10_ug_m3", "co_ppb", "no2_ppb", "nh3_ppb",
)


@dataclass
class NormalisedReading:
    timestamp: datetime
    source: str
    tvoc_ppb:      float | None = None
    eco2_ppm:      float | None = None
    temperature_c: float | None = None
    humidity_pct:  float | None = None
    pm1_ug_m3:     float | None = None
    pm25_ug_m3:    float | None = None
    pm10_ug_m3:    float | None = None
    co_ppb:        float | None = None
    no2_ppb:       float | None = None
    nh3_ppb:       float | None = None


class DataSource(ABC):
    def __init__(self) -> None:
        self.last_reading_at: datetime | None = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this source, e.g. 'sgp30'."""

    @abstractmethod
    def get_latest(self) -> NormalisedReading:
        """Read the most recent values. Return None for unavailable fields."""


def merge_readings(readings: list[NormalisedReading]) -> NormalisedReading:
    """Merge multiple NormalisedReadings into one.
    First non-None value wins per field. Timestamp is utcnow().
    """
    merged: dict = {f: None for f in SENSOR_FIELDS}
    for reading in readings:
        for field_name in SENSOR_FIELDS:
            if merged[field_name] is None:
                merged[field_name] = getattr(reading, field_name)
    return NormalisedReading(
        timestamp=datetime.now(timezone.utc),
        source="merged",
        **merged,
    )
