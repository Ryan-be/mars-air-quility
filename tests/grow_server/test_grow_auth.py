"""Auth helpers: token generation, hashing, verification, enrollment-key check."""
import pytest
from mlss_monitor.grow.auth import (
    generate_token, hash_secret, verify_secret,
    verify_enrollment_key, AuthError,
)


def test_generate_token_is_url_safe_and_long():
    t = generate_token()
    assert len(t) >= 32
    # urlsafe alphabet — no '+' or '/'
    assert "+" not in t and "/" not in t


def test_generate_token_is_unique():
    assert generate_token() != generate_token()


def test_hash_and_verify_round_trip():
    raw = generate_token()
    hashed = hash_secret(raw)
    assert hashed != raw
    assert verify_secret(raw, hashed) is True


def test_verify_secret_rejects_wrong_token():
    raw = generate_token()
    hashed = hash_secret(raw)
    assert verify_secret("wrong-token", hashed) is False


def test_verify_enrollment_key_against_stored_hash(tmp_path, monkeypatch):
    """Verifies a given raw key against the stored argon2 hash in app_settings."""
    import sqlite3
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    raw_key = "test-enrollment-key-12345"
    conn.execute("INSERT INTO app_settings VALUES (?, ?)",
                 ("grow_enrollment_key_hash", hash_secret(raw_key)))
    conn.commit()
    conn.close()

    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", str(db_path))
    assert verify_enrollment_key(raw_key) is True
    assert verify_enrollment_key("wrong-key") is False


def test_verify_enrollment_key_raises_if_no_key_set(tmp_path, monkeypatch):
    import sqlite3
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()

    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", str(db_path))
    with pytest.raises(AuthError, match="not configured"):
        verify_enrollment_key("anything")
