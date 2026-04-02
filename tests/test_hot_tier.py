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
