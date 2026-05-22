"""Tests for VAPID key generation and retrieval from app_settings."""

import sqlite3

import pytest

from database.init_db import create_db
from mlss_monitor.notifications import vapid


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("MLSS_DB_FILE", str(db))
    # config is read at import; reload to pick up the env var.
    from config import config as _config
    _config.reload()
    # init_db.DB_FILE was captured at import — repatch it so create_db()
    # writes into the test tmpdir rather than the production data path.
    monkeypatch.setattr("database.init_db.DB_FILE", str(db))
    create_db()
    yield db


def test_generates_keys_on_first_call(fresh_db):
    pub = vapid.get_public_key()
    priv = vapid.get_private_key()
    assert isinstance(pub, str) and len(pub) > 40  # base64url EC P-256
    assert isinstance(priv, str) and len(priv) > 40


def test_idempotent_across_calls(fresh_db):
    pub1 = vapid.get_public_key()
    pub2 = vapid.get_public_key()
    assert pub1 == pub2


def test_contact_email_default_empty(fresh_db):
    assert vapid.get_contact_email() == ""


def test_contact_email_round_trip(fresh_db):
    vapid.set_contact_email("admin@example.com")
    assert vapid.get_contact_email() == "admin@example.com"


def test_keys_persist_in_app_settings(fresh_db):
    vapid.get_public_key()  # triggers generation
    conn = sqlite3.connect(str(fresh_db))
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'vapid_public_key'"
    ).fetchone()
    conn.close()
    assert row is not None and len(row[0]) > 40
