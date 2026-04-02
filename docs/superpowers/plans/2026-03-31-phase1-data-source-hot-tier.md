# Phase 1: Data Source Abstraction + Hot Tier — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a `DataSource` abstraction layer and a 1-second in-memory hot tier alongside the existing read loop — zero breaking changes to inference, fan control, DB schema, or UI.

**Architecture:** New `mlss_monitor/data_sources/` module defines a `DataSource` ABC and `NormalisedReading` dataclass. Each sensor gets a thin wrapper class. A new `HotTier` ring buffer (deque, 3600 entries) is populated by a new 1s background thread in `app.py`. The existing `_background_log()` loop and `log_sensor_data()` DB writes are untouched — they keep running in parallel. Nothing downstream changes.

**Tech Stack:** Python 3.11, `collections.deque`, `dataclasses`, `abc`, `pytest`, `unittest.mock`

**Spec:** `docs/superpowers/specs/2026-03-31-smart-inference-engine-design.md` — Layers 1 & 2

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `mlss_monitor/data_sources/__init__.py` | Package exports |
| Create | `mlss_monitor/data_sources/base.py` | `DataSource` ABC + `NormalisedReading` dataclass |
| Create | `mlss_monitor/data_sources/sgp30_source.py` | Wraps `sensor_interfaces/sgp30.py` |
| Create | `mlss_monitor/data_sources/aht20_source.py` | Wraps `sensor_interfaces/aht20.py` |
| Create | `mlss_monitor/data_sources/pm_source.py` | Wraps `sensor_interfaces/sb_components_pm_sensor.py` |
| Create | `mlss_monitor/data_sources/mics6814_source.py` | Wraps MICS6814 sensor interface |
| Create | `mlss_monitor/data_sources/weather_source.py` | Wraps `external_api_interfaces/open_meteo.py` |
| Create | `mlss_monitor/hot_tier.py` | Thread-safe deque ring buffer |
| Modify | `mlss_monitor/app.py` | Add 1s sensor read thread + HotTier; existing loop untouched |
| Modify | `pyproject.toml` | No new runtime deps; confirm pytest config |
| Create | `tests/test_data_sources.py` | Unit tests for all DataSource implementations |
| Create | `tests/test_hot_tier.py` | Unit tests for HotTier |

---

## Task 1: `NormalisedReading` dataclass and `DataSource` ABC

**Files:**
- Create: `mlss_monitor/data_sources/base.py`
- Create: `mlss_monitor/data_sources/__init__.py`
- Create: `tests/test_data_sources.py` (stub)

- [ ] **Step 1.1: Write the failing import test**

```python
# tests/test_data_sources.py
from mlss_monitor.data_sources.base import NormalisedReading, DataSource
from datetime import datetime


def test_normalised_reading_all_none():
    r = NormalisedReading(timestamp=datetime.utcnow(), source="test")
    assert r.tvoc_ppb is None
    assert r.eco2_ppm is None
    assert r.temperature_c is None
    assert r.humidity_pct is None
    assert r.pm25_ug_m3 is None
    assert r.co_ppb is None
    assert r.no2_ppb is None
    assert r.nh3_ppb is None


def test_normalised_reading_with_values():
    r = NormalisedReading(
        timestamp=datetime.utcnow(),
        source="sgp30",
        eco2_ppm=850.0,
        tvoc_ppb=120.0,
    )
    assert r.eco2_ppm == 850.0
    assert r.tvoc_ppb == 120.0
    assert r.temperature_c is None


def test_data_source_is_abstract():
    import pytest
    with pytest.raises(TypeError):
        DataSource()  # Cannot instantiate abstract class
```

- [ ] **Step 1.2: Run tests to confirm they fail**

```bash
cd /path/to/repo
pytest tests/test_data_sources.py -v
```
Expected: `ModuleNotFoundError` or `ImportError`

- [ ] **Step 1.3: Create `mlss_monitor/data_sources/base.py`**

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class NormalisedReading:
    timestamp: datetime
    source: str
    tvoc_ppb:      float | None = None
    eco2_ppm:      float | None = None
    temperature_c: float | None = None
    humidity_pct:  float | None = None
    pm25_ug_m3:    float | None = None
    co_ppb:        float | None = None
    no2_ppb:       float | None = None
    nh3_ppb:       float | None = None


class DataSource(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this source, e.g. 'sgp30'."""

    @abstractmethod
    def get_latest(self) -> NormalisedReading:
        """Read the most recent values. Return None for unavailable fields."""
```

- [ ] **Step 1.4: Create `mlss_monitor/data_sources/__init__.py`**

```python
from .base import DataSource, NormalisedReading

__all__ = ["DataSource", "NormalisedReading"]
```

- [ ] **Step 1.5: Run tests to confirm they pass**

```bash
pytest tests/test_data_sources.py -v
```
Expected: 3 tests PASS

- [ ] **Step 1.6: Add `merge_readings` to `base.py`**

The hot tier stores one merged reading per second — all source fields combined into a single `NormalisedReading`. Add this function to `mlss_monitor/data_sources/base.py`:

```python
def merge_readings(readings: list[NormalisedReading]) -> NormalisedReading:
    """Merge multiple NormalisedReadings into one.
    First non-None value wins per field. Timestamp is utcnow().
    """
    _SENSOR_FIELDS = (
        "tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
        "pm25_ug_m3", "co_ppb", "no2_ppb", "nh3_ppb",
    )
    merged: dict = {f: None for f in _SENSOR_FIELDS}
    for reading in readings:
        for field_name in _SENSOR_FIELDS:
            if merged[field_name] is None:
                merged[field_name] = getattr(reading, field_name)
    return NormalisedReading(
        timestamp=datetime.now(timezone.utc),
        source="merged",
        **merged,
    )
```

Also add `timezone` to the datetime import at the top of `base.py`:

```python
from datetime import datetime, timezone
```

- [ ] **Step 1.7: Add merge test**

```python
# Add to tests/test_data_sources.py
from mlss_monitor.data_sources.base import merge_readings


def test_merge_readings_combines_fields():
    ts = datetime.utcnow()
    r1 = NormalisedReading(timestamp=ts, source="sgp30", eco2_ppm=850.0, tvoc_ppb=120.0)
    r2 = NormalisedReading(timestamp=ts, source="aht20", temperature_c=21.5, humidity_pct=55.0)
    merged = merge_readings([r1, r2])
    assert merged.eco2_ppm == 850.0
    assert merged.tvoc_ppb == 120.0
    assert merged.temperature_c == 21.5
    assert merged.humidity_pct == 55.0
    assert merged.source == "merged"


def test_merge_readings_first_non_none_wins():
    ts = datetime.utcnow()
    r1 = NormalisedReading(timestamp=ts, source="a", temperature_c=21.0)
    r2 = NormalisedReading(timestamp=ts, source="b", temperature_c=99.0)
    merged = merge_readings([r1, r2])
    assert merged.temperature_c == 21.0  # r1 wins


def test_merge_readings_empty_list():
    merged = merge_readings([])
    assert merged.tvoc_ppb is None
    assert merged.source == "merged"
```

- [ ] **Step 1.8: Run tests**

```bash
pytest tests/test_data_sources.py -v
```
Expected: all tests PASS

- [ ] **Step 1.9: Update `mlss_monitor/data_sources/__init__.py`**

```python
from .base import DataSource, NormalisedReading, merge_readings

__all__ = ["DataSource", "NormalisedReading", "merge_readings"]
```

- [ ] **Step 1.10: Commit**

```bash
git add mlss_monitor/data_sources/ tests/test_data_sources.py
git commit -m "feat: add DataSource ABC, NormalisedReading, and merge_readings"
```

---

## Task 2: `SGP30Source` and `AHT20Source`

**Files:**
- Create: `mlss_monitor/data_sources/sgp30_source.py`
- Create: `mlss_monitor/data_sources/aht20_source.py`
- Modify: `mlss_monitor/data_sources/__init__.py`
- Modify: `tests/test_data_sources.py`

- [ ] **Step 2.1: Write the failing tests**

```python
# Add to tests/test_data_sources.py
from unittest.mock import patch
from mlss_monitor.data_sources.sgp30_source import SGP30Source
from mlss_monitor.data_sources.aht20_source import AHT20Source


def test_sgp30_source_name():
    with patch("mlss_monitor.data_sources.sgp30_source.read_sgp30", return_value=(850, 120)):
        source = SGP30Source()
        assert source.name == "sgp30"


def test_sgp30_source_returns_reading():
    with patch("mlss_monitor.data_sources.sgp30_source.read_sgp30", return_value=(850, 120)):
        source = SGP30Source()
        reading = source.get_latest()
        assert reading.eco2_ppm == 850.0
        assert reading.tvoc_ppb == 120.0
        assert reading.source == "sgp30"
        assert reading.temperature_c is None


def test_sgp30_source_handles_none():
    with patch("mlss_monitor.data_sources.sgp30_source.read_sgp30", return_value=(None, None)):
        source = SGP30Source()
        reading = source.get_latest()
        assert reading.eco2_ppm is None
        assert reading.tvoc_ppb is None


def test_aht20_source_name():
    with patch("mlss_monitor.data_sources.aht20_source.read_aht20", return_value=(21.5, 55.0)):
        source = AHT20Source()
        assert source.name == "aht20"


def test_aht20_source_returns_reading():
    with patch("mlss_monitor.data_sources.aht20_source.read_aht20", return_value=(21.5, 55.0)):
        source = AHT20Source()
        reading = source.get_latest()
        assert reading.temperature_c == 21.5
        assert reading.humidity_pct == 55.0
        assert reading.source == "aht20"
        assert reading.tvoc_ppb is None
```

- [ ] **Step 2.2: Run tests to confirm they fail**

```bash
pytest tests/test_data_sources.py::test_sgp30_source_name \
       tests/test_data_sources.py::test_aht20_source_name -v
```
Expected: `ImportError`

- [ ] **Step 2.3: Create `mlss_monitor/data_sources/sgp30_source.py`**

```python
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
```

- [ ] **Step 2.4: Create `mlss_monitor/data_sources/aht20_source.py`**

```python
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
```

- [ ] **Step 2.5: Update `mlss_monitor/data_sources/__init__.py`**

```python
from .base import DataSource, NormalisedReading
from .sgp30_source import SGP30Source
from .aht20_source import AHT20Source

__all__ = ["DataSource", "NormalisedReading", "SGP30Source", "AHT20Source"]
```

- [ ] **Step 2.6: Run tests to confirm they pass**

```bash
pytest tests/test_data_sources.py -v
```
Expected: all tests PASS

- [ ] **Step 2.7: Commit**

```bash
git add mlss_monitor/data_sources/ tests/test_data_sources.py
git commit -m "feat: add SGP30Source and AHT20Source DataSource wrappers"
```

---

## Task 3: `ParticulateSource` and `MICS6814Source`

**Files:**
- Create: `mlss_monitor/data_sources/pm_source.py`
- Create: `mlss_monitor/data_sources/mics6814_source.py`
- Modify: `mlss_monitor/data_sources/__init__.py`
- Modify: `tests/test_data_sources.py`

> **Note on MICS6814 and PM sensor:** Both interfaces now exist in `sensor_interfaces/` and follow the same module-level pattern as `aht20.py` and `sgp30.py`. `ParticulateSource` uses the module-level `read_pm()` function — no constructor argument needed.

- [ ] **Step 3.1: Write the failing tests**

```python
# Add to tests/test_data_sources.py
from unittest.mock import patch
from mlss_monitor.data_sources.pm_source import ParticulateSource
from mlss_monitor.data_sources.mics6814_source import MICS6814Source


def test_particulate_source_name():
    source = ParticulateSource()
    assert source.name == "pm_sensor"


def test_particulate_source_returns_pm25():
    with patch(
        "mlss_monitor.data_sources.pm_source.read_pm",
        return_value={"pm1_0": 5, "pm2_5": 12, "pm10": 18},
    ):
        source = ParticulateSource()
        reading = source.get_latest()
        assert reading.pm25_ug_m3 == 12.0
        assert reading.source == "pm_sensor"
        assert reading.tvoc_ppb is None


def test_particulate_source_handles_none():
    with patch("mlss_monitor.data_sources.pm_source.read_pm", return_value=None):
        source = ParticulateSource()
        reading = source.get_latest()
        assert reading.pm25_ug_m3 is None


def test_particulate_source_handles_exception():
    with patch(
        "mlss_monitor.data_sources.pm_source.read_pm",
        side_effect=Exception("UART timeout"),
    ):
        source = ParticulateSource()
        reading = source.get_latest()
        assert reading.pm25_ug_m3 is None


def test_mics6814_source_name():
    source = MICS6814Source()
    assert source.name == "mics6814"


def test_mics6814_source_returns_gas_readings():
    with patch(
        "mlss_monitor.data_sources.mics6814_source.read_mics6814",
        return_value=(1.23, 0.05, 8.7),
    ):
        source = MICS6814Source()
        reading = source.get_latest()
        assert reading.co_ppb == 1.23
        assert reading.no2_ppb == 0.05
        assert reading.nh3_ppb == 8.7
        assert reading.source == "mics6814"


def test_mics6814_source_handles_none_tuple():
    with patch(
        "mlss_monitor.data_sources.mics6814_source.read_mics6814",
        return_value=(None, None, None),
    ):
        source = MICS6814Source()
        reading = source.get_latest()
        assert reading.co_ppb is None
        assert reading.no2_ppb is None
        assert reading.nh3_ppb is None
```

- [ ] **Step 3.2: Run tests to confirm they fail**

```bash
pytest tests/test_data_sources.py::test_particulate_source_name \
       tests/test_data_sources.py::test_mics6814_source_name -v
```
Expected: `ImportError`

- [ ] **Step 3.3: Create `mlss_monitor/data_sources/pm_source.py`**

`sensor_interfaces/sb_components_pm_sensor.py` exposes a module-level `read_pm()` function — same pattern as `aht20.py`. No constructor argument needed.

```python
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
```

- [ ] **Step 3.4: Create `mlss_monitor/data_sources/mics6814_source.py`**

`sensor_interfaces/mics6814.py` exists and exposes `read_mics6814()` returning `(co, no2, nh3)` — same pattern as `sgp30.py`.

```python
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
```

- [ ] **Step 3.5: Update `mlss_monitor/data_sources/__init__.py`**

```python
from .base import DataSource, NormalisedReading
from .sgp30_source import SGP30Source
from .aht20_source import AHT20Source
from .pm_source import ParticulateSource
from .mics6814_source import MICS6814Source

__all__ = [
    "DataSource", "NormalisedReading",
    "SGP30Source", "AHT20Source",
    "ParticulateSource", "MICS6814Source",
]
```

- [ ] **Step 3.6: Run all tests**

```bash
pytest tests/test_data_sources.py -v
```
Expected: all tests PASS

- [ ] **Step 3.7: Commit**

```bash
git add mlss_monitor/data_sources/ tests/test_data_sources.py
git commit -m "feat: add ParticulateSource and MICS6814Source DataSource wrappers"
```

---

## Task 4: `WeatherAPISource`

**Files:**
- Create: `mlss_monitor/data_sources/weather_source.py`
- Modify: `mlss_monitor/data_sources/__init__.py`
- Modify: `tests/test_data_sources.py`

- [ ] **Step 4.1: Write the failing tests**

```python
# Add to tests/test_data_sources.py
from unittest.mock import MagicMock
from mlss_monitor.data_sources.weather_source import WeatherAPISource


def test_weather_source_name():
    source = WeatherAPISource(client=MagicMock(), lat=51.5, lon=-0.1)
    assert source.name == "weather_api"


def test_weather_source_returns_temp_and_humidity():
    mock_client = MagicMock()
    mock_client.get_current_weather.return_value = {
        "temp": 12.3,
        "humidity": 78.0,
        "wind_speed": 5.2,
        "weather_code": 3,
        "uv_index": 1.0,
        "feels_like": 10.1,
    }
    source = WeatherAPISource(client=mock_client, lat=51.5, lon=-0.1)
    reading = source.get_latest()
    assert reading.temperature_c == 12.3
    assert reading.humidity_pct == 78.0
    assert reading.source == "weather_api"
    assert reading.tvoc_ppb is None


def test_weather_source_handles_api_failure():
    mock_client = MagicMock()
    mock_client.get_current_weather.side_effect = Exception("network error")
    source = WeatherAPISource(client=mock_client, lat=51.5, lon=-0.1)
    reading = source.get_latest()
    assert reading.temperature_c is None
    assert reading.humidity_pct is None
```

- [ ] **Step 4.2: Run tests to confirm they fail**

```bash
pytest tests/test_data_sources.py::test_weather_source_name -v
```
Expected: `ImportError`

- [ ] **Step 4.3: Create `mlss_monitor/data_sources/weather_source.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone

from .base import DataSource, NormalisedReading


class WeatherAPISource(DataSource):
    """Wraps OpenMeteoClient from external_api_interfaces/open_meteo.py."""

    def __init__(self, client, lat: float, lon: float) -> None:
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
        except Exception:
            temp, humidity = None, None
        return NormalisedReading(
            timestamp=datetime.now(timezone.utc),
            source=self.name,
            temperature_c=temp,
            humidity_pct=humidity,
        )
```

- [ ] **Step 4.4: Update `mlss_monitor/data_sources/__init__.py`**

```python
from .base import DataSource, NormalisedReading
from .sgp30_source import SGP30Source
from .aht20_source import AHT20Source
from .pm_source import ParticulateSource
from .mics6814_source import MICS6814Source
from .weather_source import WeatherAPISource

__all__ = [
    "DataSource", "NormalisedReading",
    "SGP30Source", "AHT20Source",
    "ParticulateSource", "MICS6814Source",
    "WeatherAPISource",
]
```

- [ ] **Step 4.5: Run all tests**

```bash
pytest tests/test_data_sources.py -v
```
Expected: all tests PASS

- [ ] **Step 4.6: Commit**

```bash
git add mlss_monitor/data_sources/ tests/test_data_sources.py
git commit -m "feat: add WeatherAPISource DataSource wrapper"
```

---

## Task 5: `HotTier` ring buffer

**Files:**
- Create: `mlss_monitor/hot_tier.py`
- Create: `tests/test_hot_tier.py`

- [ ] **Step 5.1: Write the failing tests**

```python
# tests/test_hot_tier.py
from datetime import datetime, timezone, timedelta
from mlss_monitor.hot_tier import HotTier
from mlss_monitor.data_sources.base import NormalisedReading


def _reading(tvoc: float, seconds_ago: int = 0) -> NormalisedReading:
    return NormalisedReading(
        timestamp=datetime.now(timezone.utc) - timedelta(seconds=seconds_ago),
        source="test",
        tvoc_ppb=tvoc,
    )


def test_hot_tier_starts_empty():
    tier = HotTier(maxlen=3600)
    assert tier.size() == 0


def test_hot_tier_push_and_size():
    tier = HotTier(maxlen=3600)
    tier.push(_reading(100.0))
    tier.push(_reading(110.0))
    assert tier.size() == 2


def test_hot_tier_respects_maxlen():
    tier = HotTier(maxlen=3)
    for i in range(5):
        tier.push(_reading(float(i)))
    assert tier.size() == 3


def test_hot_tier_latest_returns_most_recent():
    tier = HotTier(maxlen=3600)
    tier.push(_reading(100.0))
    tier.push(_reading(200.0))
    assert tier.latest().tvoc_ppb == 200.0


def test_hot_tier_latest_returns_none_when_empty():
    tier = HotTier(maxlen=3600)
    assert tier.latest() is None


def test_hot_tier_last_n_returns_n_most_recent():
    tier = HotTier(maxlen=3600)
    for i in range(10):
        tier.push(_reading(float(i)))
    result = tier.last_n(3)
    assert len(result) == 3
    assert result[-1].tvoc_ppb == 9.0


def test_hot_tier_last_n_clamps_to_available():
    tier = HotTier(maxlen=3600)
    tier.push(_reading(1.0))
    result = tier.last_n(100)
    assert len(result) == 1


def test_hot_tier_last_minutes_filters_by_time():
    tier = HotTier(maxlen=3600)
    tier.push(_reading(1.0, seconds_ago=120))  # 2 min ago
    tier.push(_reading(2.0, seconds_ago=30))   # 30 sec ago
    tier.push(_reading(3.0, seconds_ago=10))   # 10 sec ago
    result = tier.last_minutes(1)
    assert len(result) == 2
    assert all(r.tvoc_ppb in (2.0, 3.0) for r in result)
```

- [ ] **Step 5.2: Run tests to confirm they fail**

```bash
pytest tests/test_hot_tier.py -v
```
Expected: `ImportError`

- [ ] **Step 5.3: Create `mlss_monitor/hot_tier.py`**

```python
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mlss_monitor.data_sources.base import NormalisedReading


class HotTier:
    """In-memory ring buffer of NormalisedReading objects.

    Thread-safe for single-writer / multiple-reader usage under CPython's GIL.
    deque.append() and reads via list() are atomic in CPython.
    """

    def __init__(self, maxlen: int = 3600) -> None:
        self._buffer: deque[NormalisedReading] = deque(maxlen=maxlen)

    def push(self, reading: NormalisedReading) -> None:
        self._buffer.append(reading)

    def latest(self) -> NormalisedReading | None:
        return self._buffer[-1] if self._buffer else None

    def size(self) -> int:
        return len(self._buffer)

    def last_n(self, n: int) -> list[NormalisedReading]:
        """Return the n most recent readings, oldest first."""
        buf = list(self._buffer)
        return buf[-n:] if n <= len(buf) else buf

    def last_minutes(self, minutes: float) -> list[NormalisedReading]:
        """Return all readings from the last `minutes` minutes, oldest first."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        return [r for r in self._buffer if r.timestamp >= cutoff]

    def snapshot(self) -> list[NormalisedReading]:
        """Return a full copy of the buffer contents, oldest first."""
        return list(self._buffer)
```

- [ ] **Step 5.4: Run tests to confirm they pass**

```bash
pytest tests/test_hot_tier.py -v
```
Expected: all 9 tests PASS

- [ ] **Step 5.5: Commit**

```bash
git add mlss_monitor/hot_tier.py tests/test_hot_tier.py
git commit -m "feat: add HotTier in-memory ring buffer for 1s sensor readings"
```

---

## Task 6: Wire 1s read loop into `app.py`

This task adds a new `_sensor_read_loop()` thread and a `HotTier` instance to `app.py`. The **existing `_background_log()` function and all DB writes are left completely untouched** — this is a parallel addition only.

**Files:**
- Modify: `mlss_monitor/app.py`

> **Before starting:** Read `app.py` lines 1–60 (imports + globals), lines 120–160 (sensor init), and lines 228–263 (`_background_log`) to understand the existing structure.

- [ ] **Step 6.1: Add imports at the top of `app.py`**

Find the existing import block (around line 30). Add after the last existing import:

```python
from mlss_monitor.hot_tier import HotTier
from mlss_monitor.data_sources import (
    SGP30Source,
    AHT20Source,
    ParticulateSource,
    MICS6814Source,
    merge_readings,
)
```

- [ ] **Step 6.2: Initialise `HotTier` and `DataSource` instances after sensor init**

The sensor init block in `app.py` runs around lines 168–176 (`pm_sensor = init_pm_sensor()`, `mics6814_sensor = init_mics6814()`). Add immediately after it:

```python
# --- Hot tier and data source abstraction (parallel addition) ---
hot_tier = HotTier(maxlen=3600)

_data_sources = [
    SGP30Source(),
    AHT20Source(),
    ParticulateSource(),    # uses module-level read_pm() — no arg needed
    MICS6814Source(),
]
```

- [ ] **Step 6.3: Add `_sensor_read_loop()` function**

Add this function near `_background_log()`. Do **not** modify `_background_log`. The loop reads all sources, merges them into a single `NormalisedReading`, then pushes once — keeping hot tier at exactly 1 entry per second regardless of how many sensors exist:

```python
def _sensor_read_loop() -> None:
    """Reads all DataSources every second, merges into one NormalisedReading,
    and pushes to the hot tier. Does not write to DB.
    """
    import time

    while True:
        try:
            readings = []
            for source in _data_sources:
                try:
                    readings.append(source.get_latest())
                except Exception as exc:
                    app.logger.warning(
                        "DataSource %s read failed: %s", source.name, exc
                    )
            if readings:
                hot_tier.push(merge_readings(readings))
        except Exception as exc:
            app.logger.error("_sensor_read_loop unexpected error: %s", exc)
        time.sleep(1)
```

- [ ] **Step 6.4: Start `_sensor_read_loop` as a daemon thread in `main()`**

Find the `main()` function and the block where other daemon threads are started (around line 144–155). Add alongside them:

```python
sensor_thread = threading.Thread(target=_sensor_read_loop, daemon=True)
sensor_thread.start()
```

- [ ] **Step 6.5: Verify the app still starts without errors**

```bash
# On the Pi (or dev machine with sensors mocked):
python -m mlss_monitor.app
# Or however the app is normally started.
# Expected: app starts, no import errors, no crash.
# Check logs for any "DataSource X read failed" warnings (expected if hardware absent).
```

- [ ] **Step 6.6: Verify hot tier is being populated**

Add a temporary check (remove after confirming, do not commit):

```python
# In Python REPL or a quick script while app is running:
import time; time.sleep(5)
from mlss_monitor.app import hot_tier
print(hot_tier.size())       # Should be > 0 and growing
print(hot_tier.latest())     # Should show a NormalisedReading
```

- [ ] **Step 6.7: Commit**

```bash
git add mlss_monitor/app.py
git commit -m "feat: add 1s sensor read loop feeding HotTier alongside existing log loop"
```

---

## Task 7: Full test run and smoke check

- [ ] **Step 7.1: Run the full test suite**

```bash
pytest tests/ -v
```
Expected: all existing tests PASS, new tests PASS, no regressions

- [ ] **Step 7.2: Confirm existing behaviour is unchanged**

Check that:
- `_background_log()` still writes to SQLite every LOG_INTERVAL seconds
- Fan controller still evaluates and controls the smart plug
- Inference engine still fires hourly/daily summaries
- Dashboard UI loads normally

- [ ] **Step 7.3: Final commit if any cleanup needed**

```bash
git add -p   # stage only intended changes
git commit -m "chore: phase 1 cleanup and test fixes"
```

---

## Adding a New Sensor in Future (e.g. pressure, methane)

The architecture is designed so adding a new sensor is a 4-step process that never touches the inference or attribution layers:

**Step 1 — Add the field to `NormalisedReading`** (`mlss_monitor/data_sources/base.py`):
```python
pressure_hpa: float | None = None   # or ch4_ppm for methane
```

**Step 2 — Add the field to `merge_readings`** (same file, `_SENSOR_FIELDS` tuple):
```python
_SENSOR_FIELDS = (
    ...,
    "pressure_hpa",
)
```

**Step 3 — Write a `DataSource` wrapper** (e.g. `mlss_monitor/data_sources/pressure_source.py`):
```python
from sensor_interfaces.bmp280 import read_bmp280   # or whatever interface exists
from .base import DataSource, NormalisedReading

class PressureSource(DataSource):
    @property
    def name(self) -> str:
        return "bmp280"

    def get_latest(self) -> NormalisedReading:
        try:
            pressure = read_bmp280()
        except Exception:
            pressure = None
        return NormalisedReading(
            timestamp=datetime.now(timezone.utc),
            source=self.name,
            pressure_hpa=float(pressure) if pressure is not None else None,
        )
```

**Step 4 — Register in `app.py`**:
```python
_data_sources = [
    SGP30Source(),
    AHT20Source(),
    ParticulateSource(),
    MICS6814Source(),
    PressureSource(),      # ← add here
]
```

That is all. The hot tier, feature extractor, rule engine, and attribution layer all handle `None` fields gracefully — they ignore fields the sensor doesn't provide and use what it does. No other file changes.

**To use the new sensor in rules** (Phase 3): add an expression to `config/rules.yaml`:
```yaml
- id: pressure_drop
  expression: "pressure_slope_30m < -2.0"
  event_type: pressure_drop
  severity: warning
  title_template: "Rapid pressure drop ({pressure_hpa:.0f} hPa)"
  description_template: "Pressure has fallen {pressure_slope_30m:.1f} hPa/min over 30 minutes."
  action: "Possible storm approaching or significant weather change."
```

**To use the new sensor in attribution fingerprints** (Phase 4): reference it in `config/fingerprints.yaml`:
```yaml
- id: cooking
  sensors:
    tvoc:     elevated
    pm25:     elevated
    pressure: normal     # new field — scored if available, skipped if None
```

---

## Phase 1 Complete

After this phase:
- `mlss_monitor/data_sources/` module exists with 5 DataSource implementations
- `mlss_monitor/hot_tier.py` provides a thread-safe 1s ring buffer (one merged reading per second)
- A new daemon thread populates the hot tier every second without touching the existing log loop
- **Nothing else has changed** — inference engine, fan controller, DB schema, UI, and `_background_log()` are all untouched

**Next:** Phase 2 — Feature Extraction (`feature_extractor.py` reads from hot tier + cold tier baselines, produces `FeatureVector`). Plan written when ready to start.
