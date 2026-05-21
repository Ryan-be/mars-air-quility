"""Tests for grow_unit.host_resolver — types + resolution chain."""

import os
from pathlib import Path

import pytest

from mlss_grow.host_resolver import Candidate, HostUnreachable, Source
from mlss_grow.host_resolver import _read_validated, _write_atomically
from mlss_grow.host_resolver import make_host_step
from mlss_grow.host_resolver import make_cache_step
from mlss_grow.host_resolver import make_mdns_step


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


def test_write_atomically_creates_file_with_content(tmp_path):
    target = tmp_path / "host"
    _write_atomically(target, "192.0.2.10", mode=0o664)
    assert target.read_text(encoding="utf-8").rstrip("\n") == "192.0.2.10"
    # On Windows chmod is largely a no-op; the production target is Linux.
    if os.name == "posix":
        assert (target.stat().st_mode & 0o777) == 0o664


def test_write_atomically_uses_tmp_plus_rename(tmp_path, monkeypatch):
    # Inject a replace function that records its call args, then forwards.
    calls = []
    real_replace = os.replace
    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        real_replace(src, dst)
    monkeypatch.setattr("os.replace", spy_replace)
    target = tmp_path / "host"
    _write_atomically(target, "192.0.2.10", mode=0o664)
    assert len(calls) == 1
    src, dst = calls[0]
    assert src.endswith(".tmp")
    assert dst == str(target)


@pytest.mark.skipif(
    os.name != "posix",
    reason="symlink creation requires elevated privilege on Windows; the "
           "production target is Linux where the symlink-refuse guard runs. "
           "The mock-based test below exercises the same code path on all "
           "platforms.",
)
def test_write_atomically_refuses_symlinks(tmp_path):
    real = tmp_path / "real-file"
    real.write_text("original-content", encoding="utf-8")
    symlink = tmp_path / "host"
    symlink.symlink_to(real)
    with pytest.raises(PermissionError, match="symlink"):
        _write_atomically(symlink, "192.0.2.99", mode=0o664)
    # Target file unchanged
    assert real.read_text(encoding="utf-8") == "original-content"


def test_write_atomically_refuses_symlinks_via_mock(tmp_path, monkeypatch):
    """Cross-platform variant of the symlink-refuse guard: stubs
    Path.is_symlink so we exercise the security check on Windows CI too,
    where real symlinks require elevated privilege.
    """
    real = tmp_path / "real-file"
    real.write_text("original-content", encoding="utf-8")
    monkeypatch.setattr(Path, "is_symlink", lambda self: True)
    with pytest.raises(PermissionError, match="symlink"):
        _write_atomically(real, "192.0.2.99", mode=0o664)
    # Target file unchanged (the write never happened)
    assert real.read_text(encoding="utf-8") == "original-content"


def test_read_validated_returns_none_when_missing(tmp_path):
    assert _read_validated(tmp_path / "absent") is None


def test_read_validated_accepts_ip_literal(tmp_path):
    f = tmp_path / "host"
    f.write_text("192.0.2.10\n", encoding="utf-8")
    assert _read_validated(f) == "192.0.2.10"


def test_read_validated_accepts_hostname(tmp_path):
    f = tmp_path / "host"
    f.write_text("mlss.local\n", encoding="utf-8")
    assert _read_validated(f) == "mlss.local"


def test_read_validated_rejects_multiline(tmp_path):
    f = tmp_path / "host"
    f.write_text("mlss.local\n192.0.2.10\n", encoding="utf-8")
    assert _read_validated(f) is None


def test_read_validated_rejects_long_string(tmp_path):
    f = tmp_path / "host"
    f.write_text("a" * 254 + "\n", encoding="utf-8")
    assert _read_validated(f) is None


def test_read_validated_rejects_bad_charset(tmp_path):
    f = tmp_path / "host"
    f.write_text("mlss.local; rm -rf /\n", encoding="utf-8")
    assert _read_validated(f) is None


def test_read_validated_strips_blank_lines(tmp_path):
    f = tmp_path / "host"
    f.write_text("\n\n   mlss.local   \n\n", encoding="utf-8")
    assert _read_validated(f) == "mlss.local"


def test_host_step_ip_literal_yields_without_dns(tmp_path):
    f = tmp_path / "host"
    f.write_text("192.0.2.10\n", encoding="utf-8")
    calls = []
    def fake_dns(_):
        calls.append(_)
        return []
    step = make_host_step(host_file=f, dns_resolver=fake_dns)
    candidates = list(step())
    assert candidates == [Candidate("192.0.2.10", Source.HOST)]
    assert calls == []           # IP literal: DNS never called


def test_host_step_hostname_calls_dns_resolver(tmp_path):
    f = tmp_path / "host"
    f.write_text("mlss.local\n", encoding="utf-8")
    def fake_dns(name):
        assert name == "mlss.local"
        return ["192.0.2.10"]
    step = make_host_step(host_file=f, dns_resolver=fake_dns)
    candidates = list(step())
    assert candidates == [Candidate("192.0.2.10", Source.HOST)]


def test_host_step_multiple_dns_results_all_yielded(tmp_path):
    f = tmp_path / "host"
    f.write_text("mlss.local\n", encoding="utf-8")
    def fake_dns(_):
        return ["192.0.2.10", "192.0.2.11"]
    step = make_host_step(host_file=f, dns_resolver=fake_dns)
    candidates = list(step())
    assert candidates == [
        Candidate("192.0.2.10", Source.HOST),
        Candidate("192.0.2.11", Source.HOST),
    ]


def test_host_step_dns_failure_yields_nothing(tmp_path):
    f = tmp_path / "host"
    f.write_text("mlss.local\n", encoding="utf-8")
    def fake_dns(_):
        import socket
        raise socket.gaierror("name not known")
    step = make_host_step(host_file=f, dns_resolver=fake_dns)
    assert list(step()) == []      # no raise, just empty


def test_host_step_missing_file_yields_nothing(tmp_path):
    step = make_host_step(
        host_file=tmp_path / "absent",
        dns_resolver=lambda _: ["should-never-be-called"],
    )
    assert list(step()) == []


def test_host_step_malformed_file_yields_nothing(tmp_path):
    f = tmp_path / "host"
    f.write_text("mlss.local\n; rm -rf /\n", encoding="utf-8")
    step = make_host_step(
        host_file=f,
        dns_resolver=lambda _: ["should-never-be-called"],
    )
    assert list(step()) == []


def test_cache_step_yields_ip(tmp_path):
    f = tmp_path / "host-cache"
    f.write_text("192.0.2.10\n", encoding="utf-8")
    step = make_cache_step(cache_file=f)
    assert list(step()) == [Candidate("192.0.2.10", Source.CACHE)]


def test_cache_step_missing_file_yields_nothing(tmp_path):
    step = make_cache_step(cache_file=tmp_path / "absent")
    assert list(step()) == []


def test_cache_step_rejects_hostname_in_cache(tmp_path):
    # Cache is for last-known-good IPs only - a hostname there means
    # something is wrong (corrupt write, manual edit). Skip silently.
    f = tmp_path / "host-cache"
    f.write_text("mlss.local\n", encoding="utf-8")
    step = make_cache_step(cache_file=f)
    assert list(step()) == []


def test_cache_step_rejects_garbage(tmp_path):
    f = tmp_path / "host-cache"
    f.write_bytes(b"\x00\x01garbage\xff")
    step = make_cache_step(cache_file=f)
    assert list(step()) == []


def test_mdns_step_libnss_path_yields_authoritative():
    def fake_dns(name):
        assert name == "mlss.local"
        return ["192.0.2.10"]
    def fake_mdns(_name, _timeout):
        raise AssertionError("should not be called when libnss path works")
    step = make_mdns_step(
        mdns_name="mlss.local",
        dns_resolver=fake_dns,
        mdns_resolver=fake_mdns,
        timeout_s=3.0,
    )
    cs = list(step())
    assert cs == [Candidate("192.0.2.10", Source.MDNS, is_authoritative=True)]


def test_mdns_step_zeroconf_fallback_when_libnss_fails():
    def fake_dns(_):
        import socket
        raise socket.gaierror("no NSS")
    def fake_mdns(name, timeout):
        assert name == "mlss.local"
        return ["192.0.2.11"]
    step = make_mdns_step(
        mdns_name="mlss.local",
        dns_resolver=fake_dns,
        mdns_resolver=fake_mdns,
        timeout_s=3.0,
    )
    cs = list(step())
    assert cs == [Candidate("192.0.2.11", Source.MDNS, is_authoritative=True)]


def test_mdns_step_both_paths_fail_yields_nothing():
    def fake_dns(_):
        import socket
        raise socket.gaierror("no NSS")
    def fake_mdns(_name, _timeout):
        return []
    step = make_mdns_step(
        mdns_name="mlss.local",
        dns_resolver=fake_dns,
        mdns_resolver=fake_mdns,
        timeout_s=3.0,
    )
    assert list(step()) == []


def test_mdns_step_passes_timeout_to_zeroconf_resolver():
    captured = {}
    def fake_dns(_):
        import socket
        raise socket.gaierror("no NSS")
    def fake_mdns(name, timeout):
        captured["timeout"] = timeout
        return ["192.0.2.10"]
    step = make_mdns_step(
        mdns_name="mlss.local",
        dns_resolver=fake_dns,
        mdns_resolver=fake_mdns,
        timeout_s=1.5,
    )
    list(step())
    assert captured["timeout"] == 1.5


from mlss_grow.host_resolver import hub_candidates, DEFAULT_STEPS


def test_hub_candidates_yields_from_each_step_in_order():
    def step_a():
        yield Candidate("192.0.2.10", Source.HOST)
    def step_b():
        yield Candidate("192.0.2.11", Source.CACHE)
    cs = list(hub_candidates(steps=(step_a, step_b)))
    assert cs == [
        Candidate("192.0.2.10", Source.HOST),
        Candidate("192.0.2.11", Source.CACHE),
    ]


def test_hub_candidates_empty_when_no_step_yields():
    def step_a():
        return iter([])
    def step_b():
        return iter([])
    assert list(hub_candidates(steps=(step_a, step_b))) == []


def test_hub_candidates_never_raises_HostUnreachable():
    # Standard iterator contract - empty means empty, not raise.
    def step_a():
        return iter([])
    cs = []
    for c in hub_candidates(steps=(step_a,)):
        cs.append(c)
    assert cs == []     # no exception


def test_hub_candidates_swallows_step_exceptions_and_continues():
    def step_a():
        raise RuntimeError("kaboom")
    def step_b():
        yield Candidate("192.0.2.10", Source.MDNS, is_authoritative=True)
    cs = list(hub_candidates(steps=(step_a, step_b)))
    assert cs == [Candidate("192.0.2.10", Source.MDNS, is_authoritative=True)]


def test_default_steps_has_three_entries():
    # Adding a 4th step is one line in this tuple - keep the count
    # honest as a regression guard.
    assert len(DEFAULT_STEPS) == 3


def test_hub_candidates_never_writes_files(tmp_path):
    # Drive iterator to exhaustion - confirm no files are created.
    f_host  = tmp_path / "host"        # missing
    f_cache = tmp_path / "host-cache"  # missing
    steps = (
        make_host_step(host_file=f_host, dns_resolver=lambda _: []),
        make_cache_step(cache_file=f_cache),
    )
    list(hub_candidates(steps=steps))
    assert not f_host.exists()
    assert not f_cache.exists()
