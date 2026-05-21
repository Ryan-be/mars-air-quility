"""Tests for grow_unit.host_resolver — types + resolution chain."""

import pytest

from mlss_grow.host_resolver import Candidate, HostUnreachable, Source


def test_source_values_are_lowercase_strings():
    assert Source.HOST.value  == "host"
    assert Source.CACHE.value == "cache"
    assert Source.MDNS.value  == "mdns"


def test_source_is_str_subclass_for_log_formatting():
    # source: str | Source — log line formatters use %s, which calls str().
    # Source(str, Enum) makes Source.HOST format as "Source.HOST" by default;
    # we want plain "host" so logs read naturally. Subclassing str
    # guarantees Source.HOST == "host" in equality + concatenation.
    assert Source.HOST == "host"
    assert f"{Source.HOST}" == "Source.HOST"  # repr in f-strings
    # but explicit .value or str.format(source) yields the value:
    assert str(Source.HOST.value) == "host"


def test_candidate_is_frozen_dataclass():
    c = Candidate(ip="192.0.2.10", source=Source.HOST)
    with pytest.raises(Exception):  # FrozenInstanceError
        c.ip = "192.0.2.99"


def test_candidate_is_authoritative_defaults_false():
    c = Candidate(ip="192.0.2.10", source=Source.HOST)
    assert c.is_authoritative is False


def test_candidate_mdns_is_authoritative_when_constructed_so():
    c = Candidate(ip="192.0.2.11", source=Source.MDNS, is_authoritative=True)
    assert c.is_authoritative is True


def test_host_unreachable_is_distinct_exception():
    assert issubclass(HostUnreachable, Exception)
    # Carries a message
    exc = HostUnreachable("no candidates resolvable")
    assert "no candidates" in str(exc)
