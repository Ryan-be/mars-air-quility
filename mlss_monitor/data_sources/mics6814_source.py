from __future__ import annotations

from datetime import datetime, timezone

from sensor_interfaces.mics6814 import read_mics6814
from .base import DataSource, NormalisedReading


class MICS6814Source(DataSource):
    @property
    def name(self) -> str:
        return "mics6814"

    def get_latest(self) -> NormalisedReading:
        try:
            co, no2, nh3 = read_mics6814()
        except Exception:
            co, no2, nh3 = None, None, None
        return NormalisedReading(
            timestamp=datetime.now(timezone.utc),
            source=self.name,
            co_ppb=float(co) if co is not None else None,
            no2_ppb=float(no2) if no2 is not None else None,
            nh3_ppb=float(nh3) if nh3 is not None else None,
        )
