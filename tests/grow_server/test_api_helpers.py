"""Unit tests for mlss_monitor.grow.api_helpers.

Two helpers under test:

  * serialise_validation_errors(errors): defensive transform that
    stringifies any Exception instance found at ``ctx.error`` so the
    list returned by pydantic ValidationError.errors() is JSON-safe.

  * RANGE_TO_HOURS: shared range-string → hour-cutoff vocabulary used
    by both /history and /photos. Pinning the keys here keeps the
    frontend's range-button enum and the server's accepted values from
    silently drifting.
"""
import json

import pytest


def test_serialise_validation_errors_passes_through_clean_errors():
    """Errors with no ctx.error Exception are returned unchanged."""
    from mlss_monitor.grow.api_helpers import serialise_validation_errors
    err = {
        "type": "missing",
        "loc": ("dry_raw",),
        "msg": "Field required",
        "input": {},
    }
    out = serialise_validation_errors([err])
    assert out == [err]


def test_serialise_validation_errors_stringifies_exception_in_ctx_error():
    """Pydantic v2 puts a ValueError under ctx.error when a model_validator
    raises. The helper must replace it with the str() form so jsonify
    doesn't trip over a non-JSON-serialisable Exception instance."""
    from mlss_monitor.grow.api_helpers import serialise_validation_errors
    err = {
        "type": "value_error",
        "loc": (),
        "msg": "Value error, dry_raw must be < wet_raw",
        "input": {"dry_raw": 800, "wet_raw": 200},
        "ctx": {"error": ValueError("dry_raw must be < wet_raw")},
    }
    out = serialise_validation_errors([err])
    # The ctx.error becomes a string …
    assert out[0]["ctx"]["error"] == "dry_raw must be < wet_raw"
    # … and the result is JSON-serialisable end-to-end (the whole point
    # of the helper — Flask's jsonify must succeed).
    json.dumps(out)


def test_serialise_validation_errors_handles_nested_dict_ctx():
    """Defensive: a ctx with both an Exception under .error AND other
    keys should preserve the other keys verbatim."""
    from mlss_monitor.grow.api_helpers import serialise_validation_errors
    err = {
        "type": "value_error",
        "loc": (),
        "msg": "bad",
        "ctx": {
            "error": RuntimeError("boom"),
            "extra_field": "kept",
            "limit": 42,
        },
    }
    out = serialise_validation_errors([err])
    ctx = out[0]["ctx"]
    assert ctx["error"] == "boom"
    assert ctx["extra_field"] == "kept"
    assert ctx["limit"] == 42


def test_serialise_validation_errors_returns_new_list_does_not_mutate_input():
    """Helper should be pure — caller's list is not mutated."""
    from mlss_monitor.grow.api_helpers import serialise_validation_errors
    err = {"ctx": {"error": ValueError("x")}}
    original = [err]
    out = serialise_validation_errors(original)
    # Caller's list reference still has the Exception instance
    assert isinstance(original[0]["ctx"]["error"], Exception)
    # Returned list has the stringified form
    assert out[0]["ctx"]["error"] == "x"


def test_range_to_hours_includes_all_5_ranges():
    """Sanity: the canonical 5 range buckets the frontend uses must
    all be present. Drift between this map and the frontend buttons
    is a 400-on-valid-click bug."""
    from mlss_monitor.grow.api_helpers import RANGE_TO_HOURS
    assert set(RANGE_TO_HOURS.keys()) == {"24h", "7d", "30d", "90d", "all"}


def test_range_to_hours_all_maps_to_none():
    """``all`` is the no-cutoff sentinel — routes branch on ``None`` to
    skip the WHERE timestamp_utc >= ? clause."""
    from mlss_monitor.grow.api_helpers import RANGE_TO_HOURS
    assert RANGE_TO_HOURS["all"] is None


@pytest.mark.parametrize("key,expected_hours", [
    ("24h", 24),
    ("7d", 168),
    ("30d", 720),
    ("90d", 2160),
])
def test_range_to_hours_concrete_values(key, expected_hours):
    """Pin the concrete hour values — accidental drift here would
    silently shorten or extend every chart's lookback."""
    from mlss_monitor.grow.api_helpers import RANGE_TO_HOURS
    assert RANGE_TO_HOURS[key] == expected_hours
