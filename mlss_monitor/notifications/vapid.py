"""VAPID (Voluntary Application Server Identification) key management.

Keys are generated lazily on first call and stored in the existing
``app_settings`` SQLite table so they persist across deploys without
the operator having to manage .env files.

Web Push spec: https://datatracker.ietf.org/doc/html/rfc8292
"""

import base64
import logging
import sqlite3

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from config import config

log = logging.getLogger(__name__)

_KEY_PUBLIC  = "vapid_public_key"
_KEY_PRIVATE = "vapid_private_key"
_KEY_CONTACT = "vapid_contact_email"


def _db_file() -> str:
    """Re-read DB_FILE from config on every call so test fixtures that
    swap the env var + reload() take effect even if this module was
    imported before the swap."""
    return config.get("DB_FILE", "data/sensor_data.db")


def _get_setting(key: str) -> str | None:
    conn = sqlite3.connect(_db_file())
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _set_setting(key: str, value: str) -> None:
    conn = sqlite3.connect(_db_file())
    try:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def _generate_keypair() -> tuple[str, str]:
    """Generate a fresh EC P-256 keypair, base64url-encoded for VAPID use."""
    private = ec.generate_private_key(ec.SECP256R1(), default_backend())
    public = private.public_key()

    # VAPID public key: 65-byte uncompressed point (0x04 || X || Y), base64url.
    public_bytes = public.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_b64 = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode()

    # VAPID private key: 32-byte raw integer, base64url.
    private_int = private.private_numbers().private_value
    private_bytes = private_int.to_bytes(32, byteorder="big")
    private_b64 = base64.urlsafe_b64encode(private_bytes).rstrip(b"=").decode()

    return public_b64, private_b64


def _ensure_keys() -> None:
    if _get_setting(_KEY_PUBLIC) and _get_setting(_KEY_PRIVATE):
        return
    log.info("Generating new VAPID keypair (first-time setup)")
    pub, priv = _generate_keypair()
    _set_setting(_KEY_PUBLIC, pub)
    _set_setting(_KEY_PRIVATE, priv)


def get_public_key() -> str:
    _ensure_keys()
    return _get_setting(_KEY_PUBLIC)


def get_private_key() -> str:
    _ensure_keys()
    return _get_setting(_KEY_PRIVATE)


def get_contact_email() -> str:
    return _get_setting(_KEY_CONTACT) or ""


def set_contact_email(email: str) -> None:
    _set_setting(_KEY_CONTACT, email.strip())
