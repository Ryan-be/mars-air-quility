"""Attribution package — source fingerprint scoring for inference enrichment."""

__all__ = ["AttributionEngine", "AttributionResult"]


def __getattr__(name):
    """Lazy load engine module to allow loader tests before engine exists."""
    if name == "AttributionEngine":
        from mlss_monitor.attribution.engine import AttributionEngine
        return AttributionEngine
    elif name == "AttributionResult":
        from mlss_monitor.attribution.engine import AttributionResult
        return AttributionResult
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
