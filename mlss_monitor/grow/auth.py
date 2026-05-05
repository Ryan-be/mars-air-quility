"""Authentication for Plant Grow Units.

Two credentials:
- Household enrollment key — argon2-hashed in app_settings, used once at unit
  enrollment to mint the per-unit token
- Per-unit bearer token — argon2-hashed in grow_units.bearer_token_hash, used
  on every WS upgrade
"""
import secrets
import sqlite3
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from database.init_db import DB_FILE

_hasher = PasswordHasher()


class AuthError(Exception):
    """Raised when an auth precondition is missing (e.g. no enrollment key set)."""


def generate_token() -> str:
    """Return a 256-bit URL-safe random token."""
    return secrets.token_urlsafe(32)


def hash_secret(raw: str) -> str:
    """argon2-hash a secret. Includes salt + parameters in the output string."""
    return _hasher.hash(raw)


def verify_secret(raw: str, hashed: str) -> bool:
    """Constant-time check of raw against an argon2 hash."""
    try:
        return _hasher.verify(hashed, raw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def verify_enrollment_key(raw_key: str) -> bool:
    """Check a raw enrollment key against the household hash in app_settings.

    Raises AuthError if no key has been configured (fresh install state).
    """
    conn = sqlite3.connect(DB_FILE, timeout=5)
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key='grow_enrollment_key_hash'"
    ).fetchone()
    conn.close()
    if row is None or not row[0]:
        raise AuthError("Enrollment key not configured — run create_db() first")
    return verify_secret(raw_key, row[0])
