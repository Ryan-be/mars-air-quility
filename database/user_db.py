"""User management database operations — GitHub OAuth users only.

All authentication goes through GitHub OAuth. This module stores which
GitHub usernames are authorised and what role each holds.

The MLSS_ALLOWED_GITHUB_USER env var remains a bootstrap / recovery admin that
does NOT need a DB entry — it always grants the admin role.
"""

import sqlite3
from datetime import datetime
from typing import Optional

from config import config

DB_FILE = config.get("DB_FILE", "data/sensor_data.db")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _conn():
    return sqlite3.connect(DB_FILE)


def _row_to_dict(row) -> dict:
    return {
        "id":              row[0],
        "github_username": row[1],
        "display_name":    row[2],
        "role":            row[3],
        "created_at":      row[4],
        "last_login":      row[5],
        "is_active":       bool(row[6]),
    }


_SELECT = (
    "SELECT id, github_username, display_name, role, created_at, last_login, is_active "
    "FROM users"
)


# ── Queries ───────────────────────────────────────────────────────────────────

def get_user_by_id(user_id: int) -> Optional[dict]:
    """Return user only if active."""
    with _conn() as conn:
        row = conn.execute(
            f"{_SELECT} WHERE id = ? AND is_active = 1", (user_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_user_by_id_any(user_id: int) -> Optional[dict]:
    """Return user regardless of active status."""
    with _conn() as conn:
        row = conn.execute(
            f"{_SELECT} WHERE id = ?", (user_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_user_by_github(github_username: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute(
            f"{_SELECT} WHERE lower(github_username) = lower(?) AND is_active = 1",
            (github_username,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_users() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            f"{_SELECT} ORDER BY "
            "CASE role WHEN 'admin' THEN 0 WHEN 'controller' THEN 1 ELSE 2 END, "
            "github_username"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def admin_count() -> int:
    with _conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1"
        ).fetchone()[0]


def has_any_user() -> bool:
    with _conn() as conn:
        return conn.execute(
            "SELECT 1 FROM users WHERE is_active = 1 LIMIT 1"
        ).fetchone() is not None


# ── Mutations ─────────────────────────────────────────────────────────────────

def add_user(github_username: str, role: str, display_name: str = "") -> dict:
    """Authorise a GitHub user with the given role. Raises ValueError on error."""
    _validate_role(role)
    github_username = github_username.strip()
    if not github_username:
        raise ValueError("github_username cannot be empty")

    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        try:
            conn.execute(
                "INSERT INTO users "
                "(github_username, display_name, role, created_at, is_active) "
                "VALUES (lower(?), ?, ?, ?, 1)",
                (github_username, display_name or github_username, role, now),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"GitHub user '{github_username}' is already registered"
            ) from exc

    return get_user_by_github(github_username)


def update_user_role(user_id: int, role: str) -> bool:
    """Update role regardless of active status (reactivation is a separate step)."""
    _validate_role(role)
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE users SET role = ? WHERE id = ?",
            (role, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


def deactivate_user(user_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE users SET is_active = 0 WHERE id = ?", (user_id,)
        )
        conn.commit()
        return cur.rowcount > 0


def reactivate_user(user_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE users SET is_active = 1 WHERE id = ?", (user_id,)
        )
        conn.commit()
        return cur.rowcount > 0


def hard_delete_user(user_id: int) -> bool:
    """Permanently remove a user row and their login_log entries."""
    with _conn() as conn:
        user = conn.execute(
            "SELECT github_username FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not user:
            return False
        conn.execute("DELETE FROM login_log WHERE lower(github_username) = lower(?)", (user[0],))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        return True


def record_login(github_username: str):
    """Update last_login on the user row and append a login_log entry."""
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        conn.execute(
            "UPDATE users SET last_login = ? WHERE lower(github_username) = lower(?)",
            (now, github_username),
        )
        conn.execute(
            "INSERT INTO login_log (github_username, logged_in_at) VALUES (lower(?), ?)",
            (github_username, now),
        )
        conn.commit()


def get_login_log(github_username: str, limit: int = 20) -> list[dict]:
    """Return the most recent login timestamps for a given GitHub username."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT logged_in_at FROM login_log "
            "WHERE lower(github_username) = lower(?) "
            "ORDER BY logged_in_at DESC LIMIT ?",
            (github_username, limit),
        ).fetchall()
    return [{"logged_in_at": r[0]} for r in rows]


# ── Private ───────────────────────────────────────────────────────────────────

def _validate_role(role: str):
    if role not in ("admin", "controller", "viewer"):
        raise ValueError(
            f"Invalid role '{role}'. Must be admin, controller, or viewer."
        )
