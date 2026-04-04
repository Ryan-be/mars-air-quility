"""Tests for atomic YAML write helper."""
from __future__ import annotations

import threading



def test_atomic_write_creates_file(tmp_path):
    """atomic_write creates the target file with correct YAML content."""
    from mlss_monitor.yaml_io import atomic_write, load_yaml

    target = tmp_path / "test.yaml"
    atomic_write(target, {"key": "value", "num": 42})

    assert target.exists()
    result = load_yaml(target)
    assert result == {"key": "value", "num": 42}


def test_atomic_write_overwrites(tmp_path):
    """atomic_write replaces existing file atomically."""
    from mlss_monitor.yaml_io import atomic_write, load_yaml

    target = tmp_path / "test.yaml"
    atomic_write(target, {"version": 1})
    atomic_write(target, {"version": 2})

    result = load_yaml(target)
    assert result["version"] == 2


def test_atomic_write_no_tmp_left_on_success(tmp_path):
    """No temp files are left behind after a successful write."""
    from mlss_monitor.yaml_io import atomic_write

    target = tmp_path / "test.yaml"
    atomic_write(target, {"x": 1})

    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == []


def test_load_yaml_missing_file_returns_empty(tmp_path):
    """load_yaml returns {} when the file does not exist."""
    from mlss_monitor.yaml_io import load_yaml

    result = load_yaml(tmp_path / "nonexistent.yaml")
    assert result == {}


def test_concurrent_writes_do_not_corrupt(tmp_path):
    """Concurrent atomic_write calls from multiple threads all succeed."""
    from mlss_monitor.yaml_io import atomic_write, load_yaml

    target = tmp_path / "concurrent.yaml"
    errors = []

    def writer(n):
        try:
            atomic_write(target, {"n": n})
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    # File must be valid YAML (not corrupted mid-write)
    result = load_yaml(target)
    assert "n" in result
