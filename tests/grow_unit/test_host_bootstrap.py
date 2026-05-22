"""Tests for grow_unit.host_bootstrap - legacy yaml migration."""

from pathlib import Path

from mlss_grow.host_bootstrap import ensure_host_file


def test_ensure_host_file_migrates_from_yaml(tmp_path):
    host_file = tmp_path / "host"
    yaml_file = tmp_path / "mlss-grow.yaml"
    yaml_file.write_text(
        "mlss_host: 192.0.2.10\n"
        "enrollment_key: ignored-here\n",
        encoding="utf-8",
    )
    ensure_host_file(host_file=host_file, legacy_yaml_paths=(yaml_file,))
    assert host_file.read_text(encoding="utf-8").rstrip("\n") == "192.0.2.10"


def test_ensure_host_file_idempotent_when_host_file_exists(tmp_path):
    host_file = tmp_path / "host"
    host_file.write_text("192.0.2.10\n", encoding="utf-8")
    yaml_file = tmp_path / "mlss-grow.yaml"
    yaml_file.write_text("mlss_host: 192.0.2.99\n", encoding="utf-8")
    ensure_host_file(host_file=host_file, legacy_yaml_paths=(yaml_file,))
    # host_file untouched; yaml value ignored because file already there
    assert host_file.read_text(encoding="utf-8").rstrip("\n") == "192.0.2.10"


def test_ensure_host_file_no_op_when_no_yaml_and_no_host(tmp_path):
    host_file = tmp_path / "host"
    ensure_host_file(
        host_file=host_file,
        legacy_yaml_paths=(tmp_path / "nope.yaml",),
    )
    assert not host_file.exists()


def test_ensure_host_file_corrupt_yaml_does_not_crash(tmp_path):
    host_file = tmp_path / "host"
    yaml_file = tmp_path / "mlss-grow.yaml"
    yaml_file.write_text("not: valid: yaml: : :\n", encoding="utf-8")
    # Should log a WARN and leave host_file unwritten - not raise.
    ensure_host_file(host_file=host_file, legacy_yaml_paths=(yaml_file,))
    assert not host_file.exists()


def test_ensure_host_file_tries_multiple_yaml_paths(tmp_path):
    # /boot/mlss-grow.yaml AND /boot/firmware/mlss-grow.yaml - pick
    # whichever exists first.
    host_file  = tmp_path / "host"
    yaml_first  = tmp_path / "first.yaml"
    yaml_second = tmp_path / "second.yaml"
    yaml_second.write_text("mlss_host: 192.0.2.10\n", encoding="utf-8")
    ensure_host_file(
        host_file=host_file,
        legacy_yaml_paths=(yaml_first, yaml_second),
    )
    assert host_file.read_text(encoding="utf-8").rstrip("\n") == "192.0.2.10"
