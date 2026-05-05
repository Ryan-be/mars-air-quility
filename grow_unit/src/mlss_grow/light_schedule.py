"""Light schedule evaluation.

Pure function over a datetime + a list of (start, end) windows. Handles
overnight windows (end < start) and multi-window-per-day setups.
"""
from datetime import datetime, time, timedelta
import re

_HH_MM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_window(start_hh_mm: str, end_hh_mm: str) -> tuple[time, time]:
    if not _HH_MM.match(start_hh_mm) or not _HH_MM.match(end_hh_mm):
        raise ValueError(f"invalid window: {start_hh_mm}-{end_hh_mm}")
    return (
        time(int(start_hh_mm[:2]), int(start_hh_mm[3:])),
        time(int(end_hh_mm[:2]), int(end_hh_mm[3:])),
    )


def is_light_on(now: datetime, windows: list[tuple[time, time]]) -> bool:
    """True if `now` falls inside at least one window. Windows are [start, end)."""
    t = now.time()
    for start, end in windows:
        if start <= end:
            # Same-day window: on if start <= t < end
            if start <= t < end:
                return True
        else:
            # Overnight: on if t >= start OR t < end
            if t >= start or t < end:
                return True
    return False
