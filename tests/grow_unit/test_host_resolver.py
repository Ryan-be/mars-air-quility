"""Tests for grow_unit.host_resolver — types + resolution chain."""

import os
from pathlib import Path

import pytest

from mlss_grow.host_resolver import Candidate, HostUnreachable, Source
from mlss_grow.host_resolver import _read_validated, _write_atomically


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
