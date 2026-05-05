"""Sensor abstract base class.

A Sensor knows how to detect itself on the I2C bus, declares which channels
it reports, and produces a reading dict. The .healthy() method returns False
after 3 consecutive bad reads — used by the safety loop to fire a
sensor_degraded event upstream.
"""
from abc import ABC, abstractmethod

_BAD_READS_THRESHOLD = 3


class Sensor(ABC):
    def __init__(self) -> None:
        self._bad_reads = 0

    @classmethod
    @abstractmethod
    def detect(cls, i2c_bus) -> "Sensor | None":
        """Probe the I2C bus; return an instance if hardware present, None otherwise."""

    @abstractmethod
    def channels(self) -> list[str]:
        """The Channel string-values this sensor reports (e.g. ['soil_moisture', 'soil_temp_c'])."""

    @abstractmethod
    def read(self) -> dict[str, float]:
        """Return the current reading. Keys must be channels declared in .channels()."""

    def healthy(self) -> bool:
        return self._bad_reads < _BAD_READS_THRESHOLD

    def record_bad_read(self) -> None:
        self._bad_reads += 1

    def record_good_read(self) -> None:
        self._bad_reads = 0
