"""Tests for mlss_monitor.grow.storage_check.get_storage_status.

Disk-usage checks are best-effort: any failure must return None rather
than raising into the page render. Tests patch `shutil.disk_usage` (a
single statvfs syscall under the hood) at the module-import site so we
don't need a real filesystem at the threshold. The threshold itself
comes from `app_settings.grow_disk_warn_pct` (default 90).
"""
import os
import sqlite3
import tempfile
from collections import namedtuple

import pytest


_DiskUsage = namedtuple("_DiskUsage", ["total", "used", "free"])


@pytest.fixture
def db(monkeypatch):
    """Seed an isolated DB so the storage_check module reads our values
    rather than the dev/prod sensor_data.db. We monkeypatch DB_FILE in
    BOTH the init_db module (for create_db()) AND the storage_check
    module (which captured DB_FILE at import time)."""
    # pylint: disable=R1732  # delete=False + close() pattern: we only want the path
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check.DB_FILE", tmp.name
    )
    init_db.create_db()
    return tmp.name


def _set_threshold(db_path, value):
    """Overwrite the seeded grow_disk_warn_pct with a test value."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES "
            "('grow_disk_warn_pct', ?)",
            (str(value),),
        )
        conn.commit()


def _delete_threshold(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM app_settings WHERE key='grow_disk_warn_pct'"
        )
        conn.commit()


def test_get_storage_status_returns_dict_with_expected_keys(monkeypatch, db, tmp_path):
    """Happy path: dict has every key the template / TypedDict promises."""
    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check._resolve_images_dir",
        lambda: str(tmp_path),
    )
    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check.shutil.disk_usage",
        lambda p: _DiskUsage(total=100_000_000_000, used=50_000_000_000, free=50_000_000_000),
    )
    from mlss_monitor.grow.storage_check import get_storage_status
    status = get_storage_status()
    assert status is not None
    assert set(status.keys()) == {
        "images_dir", "used_bytes", "total_bytes",
        "used_pct", "threshold_pct", "is_warning",
    }
    assert status["used_bytes"] == 50_000_000_000
    assert status["total_bytes"] == 100_000_000_000


def test_get_storage_status_uses_default_threshold_when_setting_missing(
    monkeypatch, db, tmp_path,
):
    """If the seeded row is somehow gone (older DB), default to 90."""
    _delete_threshold(db)
    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check._resolve_images_dir",
        lambda: str(tmp_path),
    )
    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check.shutil.disk_usage",
        lambda p: _DiskUsage(total=100, used=50, free=50),
    )
    from mlss_monitor.grow.storage_check import get_storage_status
    status = get_storage_status()
    assert status is not None
    assert status["threshold_pct"] == 90.0


def test_get_storage_status_reads_threshold_from_app_settings(
    monkeypatch, db, tmp_path,
):
    """Operator override: a custom threshold in app_settings is honoured."""
    _set_threshold(db, "80")
    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check._resolve_images_dir",
        lambda: str(tmp_path),
    )
    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check.shutil.disk_usage",
        lambda p: _DiskUsage(total=100, used=50, free=50),
    )
    from mlss_monitor.grow.storage_check import get_storage_status
    status = get_storage_status()
    assert status is not None
    assert status["threshold_pct"] == 80.0


def test_get_storage_status_is_warning_true_when_over_threshold(
    monkeypatch, db, tmp_path,
):
    """At 95% used with default threshold of 90 → warn."""
    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check._resolve_images_dir",
        lambda: str(tmp_path),
    )
    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check.shutil.disk_usage",
        lambda p: _DiskUsage(total=1000, used=950, free=50),
    )
    from mlss_monitor.grow.storage_check import get_storage_status
    status = get_storage_status()
    assert status is not None
    assert status["is_warning"] is True
    assert status["used_pct"] == pytest.approx(95.0)


def test_get_storage_status_is_warning_false_when_under(
    monkeypatch, db, tmp_path,
):
    """50% used vs 90% threshold → no warn."""
    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check._resolve_images_dir",
        lambda: str(tmp_path),
    )
    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check.shutil.disk_usage",
        lambda p: _DiskUsage(total=1000, used=500, free=500),
    )
    from mlss_monitor.grow.storage_check import get_storage_status
    status = get_storage_status()
    assert status is not None
    assert status["is_warning"] is False


def test_get_storage_status_returns_none_on_disk_usage_failure(
    monkeypatch, db, tmp_path,
):
    """Best-effort contract: any exception → None, page renders without
    the banner rather than crashing.
    """
    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check._resolve_images_dir",
        lambda: str(tmp_path),
    )

    def _raise(_p):
        raise OSError("simulated disk_usage failure")

    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check.shutil.disk_usage", _raise
    )
    from mlss_monitor.grow.storage_check import get_storage_status
    assert get_storage_status() is None


def test_get_storage_status_returns_none_when_no_path_exists(
    monkeypatch, db,
):
    """If neither the images dir nor any of its parents exist, give up
    rather than feeding an invalid path to shutil.disk_usage.
    """
    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check._resolve_images_dir",
        lambda: "/nonexistent-root-xxxxxx/zzz/yyy",
    )
    monkeypatch.setattr("os.path.isdir", lambda p: False)
    monkeypatch.setattr("os.path.exists", lambda p: False)
    from mlss_monitor.grow.storage_check import get_storage_status
    assert get_storage_status() is None


def test_get_storage_status_walks_up_to_existing_parent_when_dir_missing(
    monkeypatch, db, tmp_path,
):
    """First-boot case: the grow_images directory hasn't been created
    yet (zero photos so far). The check walks up the path until it
    finds an existing parent so disk_usage can still report the mount.
    """
    fake_images = str(tmp_path / "not_yet_created")
    assert not os.path.isdir(fake_images)

    captured_path = {}

    def _disk_usage(p):
        captured_path["path"] = p
        return _DiskUsage(total=1000, used=500, free=500)

    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check._resolve_images_dir",
        lambda: fake_images,
    )
    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check.shutil.disk_usage", _disk_usage
    )
    from mlss_monitor.grow.storage_check import get_storage_status
    status = get_storage_status()
    assert status is not None
    # disk_usage was called against an existing parent, not the missing
    # images_dir itself
    assert captured_path["path"] != fake_images
    assert os.path.exists(captured_path["path"])
    # The reported images_dir field still echoes the originally-requested
    # path so the operator sees where photos *would* live.
    assert status["images_dir"] == fake_images


def test_get_storage_status_handles_invalid_threshold_string(
    monkeypatch, db, tmp_path,
):
    """A typo'd threshold value (non-numeric) falls back to 90 rather
    than raising. The page must render even with corrupt settings.
    """
    _set_threshold(db, "not-a-number")
    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check._resolve_images_dir",
        lambda: str(tmp_path),
    )
    monkeypatch.setattr(
        "mlss_monitor.grow.storage_check.shutil.disk_usage",
        lambda p: _DiskUsage(total=1000, used=500, free=500),
    )
    from mlss_monitor.grow.storage_check import get_storage_status
    status = get_storage_status()
    assert status is not None
    assert status["threshold_pct"] == 90.0
