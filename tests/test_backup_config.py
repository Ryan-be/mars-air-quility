"""Backup config module — load/save with password masking.

The ``db_path`` fixture is provided by ``tests/conftest.py``.
"""
import sqlite3


def test_load_returns_defaults_when_unset(db_path):
    from mlss_monitor.backup import config
    cfg = config.load()
    assert cfg["enabled"] is False
    assert cfg["paused"] is False
    assert cfg["db"]["enabled"] is False
    assert cfg["db"]["host"] == ""
    assert cfg["db"]["port"] == 5432
    assert cfg["db"]["password_set"] is False
    assert "password" not in cfg["db"]
    assert cfg["files"]["enabled"] is False
    assert cfg["files"]["region"] == "auto"
    assert cfg["files"]["bucket_prefix"] == "mlss-"
    assert cfg["files"]["secret_key_set"] is False
    assert "secret_key" not in cfg["files"]
    assert cfg["advanced"]["outbox_cap_mb"] == 500
    assert cfg["advanced"]["connection_timeout_s"] == 10


def test_save_then_load_roundtrip(db_path):
    from mlss_monitor.backup import config
    config.save({
        "enabled": True,
        "paused": False,
        "db": {
            "enabled": True, "host": "server.local", "port": 5432,
            "database": "mlss", "user": "mlss", "password": "secret123",
        },
        "files": {
            "enabled": True, "endpoint": "https://server.local:9000",
            "region": "auto", "access_key_id": "AK",
            "secret_key": "SK", "bucket_prefix": "mlss-",
        },
        "advanced": {"outbox_cap_mb": 500, "connection_timeout_s": 10},
    })
    cfg = config.load()
    assert cfg["enabled"] is True
    assert cfg["db"]["host"] == "server.local"
    assert cfg["db"]["password_set"] is True
    assert "password" not in cfg["db"]
    assert cfg["files"]["secret_key_set"] is True
    assert "secret_key" not in cfg["files"]
    assert cfg["files"]["access_key_id"] == "AK"


def test_save_partial_merges_with_existing(db_path):
    """save() with only some fields should leave the rest alone."""
    from mlss_monitor.backup import config
    config.save({
        "db": {"host": "first.local", "user": "alice"},
    })
    config.save({
        "db": {"host": "second.local"},  # only host
    })
    cfg = config.load()
    assert cfg["db"]["host"] == "second.local"
    assert cfg["db"]["user"] == "alice"  # preserved


def test_save_empty_password_preserves_existing(db_path):
    """Empty-string password is "leave existing alone" — used by UI when
    operator doesn't want to change the password."""
    from mlss_monitor.backup import config
    config.save({"db": {"password": "first"}})
    config.save({"db": {"password": ""}})
    assert config._get_password_for_tests("db") == "first"


def test_save_non_empty_password_overwrites(db_path):
    from mlss_monitor.backup import config
    config.save({"db": {"password": "first"}})
    config.save({"db": {"password": "second"}})
    assert config._get_password_for_tests("db") == "second"


def test_save_empty_secret_key_preserves_existing(db_path):
    """Same empty-vs-non-empty semantics for files.secret_key."""
    from mlss_monitor.backup import config
    config.save({"files": {"secret_key": "SK1"}})
    config.save({"files": {"secret_key": ""}})
    assert config.get_secret("files", "secret_key") == "SK1"


def test_get_secret_returns_none_when_unset(db_path):
    from mlss_monitor.backup import config
    assert config.get_secret("db", "password") is None
    assert config.get_secret("files", "secret_key") is None


def test_get_secret_returns_value_when_set(db_path):
    from mlss_monitor.backup import config
    config.save({"db": {"password": "topsecret"}})
    assert config.get_secret("db", "password") == "topsecret"


def test_boolean_round_trip(db_path):
    """Booleans should round-trip via str storage cleanly."""
    from mlss_monitor.backup import config
    config.save({"enabled": True, "db": {"enabled": True}})
    cfg = config.load()
    assert cfg["enabled"] is True
    assert cfg["db"]["enabled"] is True
    config.save({"enabled": False, "db": {"enabled": False}})
    cfg = config.load()
    assert cfg["enabled"] is False
    assert cfg["db"]["enabled"] is False


def test_integer_round_trip(db_path):
    from mlss_monitor.backup import config
    config.save({
        "db": {"port": 5433},
        "advanced": {"outbox_cap_mb": 1000, "connection_timeout_s": 30},
    })
    cfg = config.load()
    assert cfg["db"]["port"] == 5433
    assert cfg["advanced"]["outbox_cap_mb"] == 1000
    assert cfg["advanced"]["connection_timeout_s"] == 30


def test_storage_uses_app_settings_table_with_backup_prefix(db_path):
    """Verify the storage layout — directly inspect app_settings."""
    from mlss_monitor.backup import config
    config.save({"db": {"host": "x.example.com", "port": 5432}})
    conn = sqlite3.connect(db_path)
    try:
        rows = dict(conn.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE 'backup.%'"
        ).fetchall())
    finally:
        conn.close()
    assert rows.get("backup.db.host") == "x.example.com"
    assert rows.get("backup.db.port") == "5432"
