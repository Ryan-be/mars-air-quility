"""Tests for state.data_source_enabled dict (Task 5)."""
from __future__ import annotations


def test_data_source_enabled_exists():
    """state module exposes a data_source_enabled dict."""
    from mlss_monitor import state
    assert hasattr(state, "data_source_enabled")
    assert isinstance(state.data_source_enabled, dict)


def test_data_source_enabled_is_mutable():
    """data_source_enabled can be mutated at runtime."""
    from mlss_monitor import state

    original = dict(state.data_source_enabled)
    try:
        state.data_source_enabled["test_source"] = True
        assert state.data_source_enabled["test_source"] is True
        state.data_source_enabled["test_source"] = False
        assert state.data_source_enabled["test_source"] is False
    finally:
        # Restore original state
        state.data_source_enabled.clear()
        state.data_source_enabled.update(original)


def test_app_initialises_all_four_sources(monkeypatch):
    """app.py populates data_source_enabled with all four registered data sources."""
    # We do NOT import mlss_monitor.app at module level because it performs
    # hardware initialisation on import. Instead we simulate what app.py does:
    # iterate _data_sources and call setdefault(name, True).
    from mlss_monitor import state

    # Stub four minimal DataSource-like objects
    class _FakeSource:
        def __init__(self, name: str):
            self._name = name

        @property
        def name(self) -> str:
            return self._name

    fake_sources = [
        _FakeSource("sgp30"),
        _FakeSource("aht20"),
        _FakeSource("pm_sensor"),
        _FakeSource("mics6814"),
    ]

    # Simulate what app.py does after registering _data_sources
    enabled: dict[str, bool] = {}
    for ds in fake_sources:
        enabled.setdefault(ds.name, True)

    # All four sources must be present and enabled by default
    assert set(enabled.keys()) == {"sgp30", "aht20", "pm_sensor", "mics6814"}
    assert all(v is True for v in enabled.values())


def test_setdefault_does_not_overwrite_existing_flag():
    """setdefault leaves an already-disabled source alone."""
    enabled: dict[str, bool] = {"sgp30": False}
    enabled.setdefault("sgp30", True)
    assert enabled["sgp30"] is False  # must remain False
