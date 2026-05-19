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


# ── source_pi_id top-level field ──────────────────────────────────────


def test_source_pi_id_default_is_pi_1(db_path):
    """Default value when no row has been written. Matches the
    PostgresClient default historically used by every test in this
    repo, so existing call sites keep their semantics."""
    from mlss_monitor.backup import config
    cfg = config.load()
    assert cfg["source_pi_id"] == "pi-1"


def test_source_pi_id_roundtrip(db_path):
    """save({source_pi_id: ...}) → load() returns the new value."""
    from mlss_monitor.backup import config
    config.save({"source_pi_id": "pi-7"})
    cfg = config.load()
    assert cfg["source_pi_id"] == "pi-7"


def test_source_pi_id_persisted_under_app_settings_key(db_path):
    """Storage layout: backup.source_pi_id row in app_settings.
    Adding a new top-level field shouldn't accidentally land it under
    a section prefix (e.g. backup.db.source_pi_id)."""
    from mlss_monitor.backup import config
    import sqlite3
    config.save({"source_pi_id": "pi-2"})
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='backup.source_pi_id'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "pi-2"


def test_save_rejects_empty_source_pi_id(db_path):
    """Empty string is rejected at save time — PostgresClient.delete_scope
    would otherwise build WHERE source_pi_id = '' and cross-Pi-wipe.
    Validate at write time so a malformed config can never persist."""
    import pytest
    from mlss_monitor.backup import config
    with pytest.raises(ValueError, match="source_pi_id"):
        config.save({"source_pi_id": ""})


def test_save_rejects_whitespace_source_pi_id(db_path):
    """Whitespace-only is rejected for the same reason — the value
    is .strip()ed downstream by PostgresClient, so '   ' would behave
    identically to ''."""
    import pytest
    from mlss_monitor.backup import config
    with pytest.raises(ValueError, match="source_pi_id"):
        config.save({"source_pi_id": "   "})


def test_save_rejects_non_string_source_pi_id(db_path):
    """A misbehaving caller passing None / int still fails fast.
    isinstance check matches PostgresClient's contract (str-only)."""
    import pytest
    from mlss_monitor.backup import config
    with pytest.raises(ValueError, match="source_pi_id"):
        config.save({"source_pi_id": None})  # type: ignore[arg-type]


def test_save_rejection_does_not_partially_persist_other_fields(db_path):
    """A partial save that mixes a good db.host with a bad
    source_pi_id should reject atomically — the validation happens
    BEFORE any rows hit app_settings, so neither field lands."""
    import pytest
    from mlss_monitor.backup import config
    with pytest.raises(ValueError, match="source_pi_id"):
        config.save({
            "source_pi_id": "",  # rejected
            "db": {"host": "should-not-persist"},
        })
    cfg = config.load()
    # db.host stays empty (the default), proving the save rolled back.
    assert cfg["db"]["host"] == ""
