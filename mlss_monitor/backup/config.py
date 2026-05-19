"""Backup config module — load/save backup settings stored in app_settings.

Settings are namespaced under the ``backup.`` prefix in the existing
``app_settings`` table. Passwords are write-only via the public API:
``load()`` returns ``password_set: bool`` / ``secret_key_set: bool`` instead
of the cleartext value. The worker uses ``get_secret()`` server-side to
fetch the cleartext for connecting.

Storage layout — flat ``key TEXT PRIMARY KEY`` rows, one per field:

    backup.enabled                        → "true" / "false"
    backup.paused                         → "true" / "false"
    backup.db.enabled                     → "true" / "false"
    backup.db.host                        → "server.local"
    backup.db.port                        → "5432"
    backup.db.database                    → "mlss"
    backup.db.user                        → "mlss"
    backup.db.password                    → "secret123"  (cleartext)
    backup.files.enabled                  → "true"
    backup.files.endpoint                 → "https://server.local:9000"
    backup.files.region                   → "auto"
    backup.files.access_key_id            → "AK"
    backup.files.secret_key               → "SK"         (cleartext)
    backup.files.bucket_prefix            → "mlss-"
    backup.advanced.outbox_cap_mb         → "500"
    backup.advanced.connection_timeout_s  → "10"

Cleartext secrets are stored deliberately — OS-level disk encryption and
Pi-level access controls protect the SQLite DB at rest, matching how
``bearer_token_hash`` is handled for grow_units. The masking on ``load()``
only prevents the UI from echoing the password back to the operator's
browser.

Spec: docs/superpowers/specs/2026-05-18-mlss-backup-design.md
"""
import sqlite3
from contextlib import closing
from typing import Any

from database.init_db import DB_FILE


# Field schema per section. The pseudo-section "_top" carries the two
# top-level booleans (backup.enabled / backup.paused); every other key
# is a named section ("db" / "files" / "advanced"). Each field maps to
# (python_type, default_value).
_SCHEMA: dict[str, dict[str, tuple[type, Any]]] = {
    "_top": {
        "enabled": (bool, False),
        "paused":  (bool, False),
    },
    "db": {
        "enabled":  (bool, False),
        "host":     (str,  ""),
        "port":     (int,  5432),
        "database": (str,  "mlss"),
        "user":     (str,  "mlss"),
        # password: handled separately (write-only, see _SECRET_FIELDS)
    },
    "files": {
        "enabled":       (bool, False),
        "endpoint":      (str,  ""),
        "region":        (str,  "auto"),
        "access_key_id": (str,  ""),
        "bucket_prefix": (str,  "mlss-"),
        # secret_key: handled separately (write-only, see _SECRET_FIELDS)
    },
    "advanced": {
        "outbox_cap_mb":        (int, 500),
        "connection_timeout_s": (int, 10),
    },
}

# Which field name carries the cleartext secret for each section. The
# secret is never returned by load(); instead load() returns
# f"{secret}_set": bool flagging whether a non-empty value is stored.
_SECRET_FIELDS: dict[str, str] = {
    "db":    "password",
    "files": "secret_key",
}


def _coerce_from_str(value: str, target_type: type) -> Any:
    """Convert a stored string back to its Python type."""
    if target_type is bool:
        return value.lower() == "true"
    if target_type is int:
        return int(value)
    return value


def _coerce_to_str(value: Any) -> str:
    """Convert a Python value to its stored string form. bool must be
    checked before int because isinstance(True, int) is True."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def load() -> dict:
    """Return the current backup config as a nested dict.

    Cleartext secrets are NEVER included. For each section that owns a
    secret field (``db.password``, ``files.secret_key``), a boolean
    ``{field}_set`` is added indicating whether a non-empty value is
    stored. Missing rows fall back to schema defaults.
    """
    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
        rows = dict(conn.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE 'backup.%'"
        ).fetchall())

    result: dict[str, Any] = {}

    # Top-level fields (enabled / paused).
    for field, (target_type, default) in _SCHEMA["_top"].items():
        stored = rows.get(f"backup.{field}")
        result[field] = (
            _coerce_from_str(stored, target_type) if stored is not None
            else default
        )

    # Section fields.
    for section, fields in _SCHEMA.items():
        if section == "_top":
            continue
        section_dict: dict[str, Any] = {}
        for field, (target_type, default) in fields.items():
            stored = rows.get(f"backup.{section}.{field}")
            section_dict[field] = (
                _coerce_from_str(stored, target_type) if stored is not None
                else default
            )
        # Render the secret-field "_set" boolean.
        secret_field = _SECRET_FIELDS.get(section)
        if secret_field is not None:
            stored_secret = rows.get(f"backup.{section}.{secret_field}")
            section_dict[f"{secret_field}_set"] = bool(stored_secret)
        result[section] = section_dict

    return result


def save(partial: dict) -> None:
    """Merge ``partial`` into the stored config.

    Semantics:
      * Missing keys / sections leave the existing stored value alone
        (true partial merge — UI can submit only the fields it changed).
      * For secret fields (``db.password``, ``files.secret_key``):
          - empty string  → no-op (preserve existing — "leave password
            alone" gesture from the UI).
          - non-empty str → overwrite.
      * All non-secret fields are written verbatim when present in the
        partial dict, including empty strings (clearing a host is valid).
    """
    rows_to_write: list[tuple[str, str]] = []

    # Top-level fields.
    for field in _SCHEMA["_top"]:
        if field in partial:
            rows_to_write.append(
                (f"backup.{field}", _coerce_to_str(partial[field]))
            )

    # Section fields.
    for section, fields in _SCHEMA.items():
        if section == "_top" or section not in partial:
            continue
        section_partial = partial[section]
        for field in fields:
            if field in section_partial:
                rows_to_write.append((
                    f"backup.{section}.{field}",
                    _coerce_to_str(section_partial[field]),
                ))
        # Secret field — write only when non-empty; empty string is a
        # deliberate UI gesture meaning "preserve existing".
        secret_field = _SECRET_FIELDS.get(section)
        if secret_field is not None and secret_field in section_partial:
            value = section_partial[secret_field]
            if value:
                rows_to_write.append(
                    (f"backup.{section}.{secret_field}", value)
                )

    if not rows_to_write:
        return

    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
        with conn:  # transaction context — commit on success, rollback on exception
            for key, value in rows_to_write:
                conn.execute(
                    "INSERT OR REPLACE INTO app_settings (key, value) "
                    "VALUES (?, ?)",
                    (key, value),
                )


def get_secret(pipeline: str, key: str) -> str | None:
    """Return the cleartext secret for ``backup.{pipeline}.{key}``.

    Used by the worker (server-side only) to obtain the credentials
    needed to connect to Postgres / S3. Returns ``None`` when the row is
    absent or its value is empty.
    """
    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key=?",
            (f"backup.{pipeline}.{key}",),
        ).fetchone()
    return row[0] if row and row[0] else None


def _get_password_for_tests(pipeline: str) -> str | None:
    """Test-only convenience wrapper. Equivalent to
    ``get_secret(pipeline, _SECRET_FIELDS[pipeline])`` — exists so test
    intent is obvious at the call site."""
    return get_secret(pipeline, _SECRET_FIELDS[pipeline])
