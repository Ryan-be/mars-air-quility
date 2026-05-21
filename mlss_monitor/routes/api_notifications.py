"""Notifications API: VAPID key, subscriptions, preferences, history.

Exposes eight endpoints under ``/api/notifications/*`` that back the
mobile push-notification UX:

  GET    /vapid-key                  — public key for browser subscribe()
  GET    /subscriptions              — list current user's devices
  POST   /subscriptions              — add or refresh a device sub
  DELETE /subscriptions/<id>         — remove a device sub
  GET    /preferences                — per-category severity floors
  PATCH  /preferences                — update severity floors
  GET    /history?days=N             — recent rendered notifications
  POST   /history/mark-read          — clear unread badges

All endpoints require an authenticated session (viewer or higher).
Per-user resources are scoped via ``session['user_id']`` so a viewer can
never list/modify/delete another user's subscriptions, preferences or
history rows.

The sister modules in ``mlss_monitor.notifications`` produce the data
this API exposes:
  - ``vapid.get_public_key`` — VAPID keypair lookup/generation
  - ``dispatcher`` — writes ``notification_history`` rows + delivers push
  - ``push_client`` — actual WebPush call
"""

import logging
import sqlite3
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, session

from config import config
from mlss_monitor import state
from mlss_monitor.notifications import vapid
from mlss_monitor.rbac import require_role

log = logging.getLogger(__name__)

api_notifications_bp = Blueprint("api_notifications", __name__)


# Severities accepted by PATCH /preferences. "off" disables the category
# entirely; the dispatcher checks the user's floor before delivering.
_VALID_SEVERITIES = {"off", "info", "warning", "critical"}

# Categories that have a notify_<cat> column on users. Adding a new one
# requires both an ALTER TABLE migration in database/init_db.py AND a
# new entry here.
_VALID_CATEGORIES = {
    "air_quality", "grow_units", "system_health", "backup_pipeline",
}

# Hard ceiling on the ?days lookback to prevent a viewer from triggering
# an expensive full-table scan on a Pi with months of accumulated history.
_MAX_HISTORY_DAYS = 90


def _db_file() -> str:
    """Resolve the active SQLite path each call.

    Re-read from config so tests that swap ``MLSS_DB_FILE`` + reload()
    take effect regardless of import order.
    """
    return config.get("DB_FILE", "data/sensor_data.db")


def _ensure_bootstrap_admin_row(username: str) -> int | None:
    """Lazy-create a users row for the bootstrap-admin env-var login.

    The bootstrap admin (MLSS_ALLOWED_GITHUB_USER) intentionally bypasses
    the users table during auth — see mlss_monitor/routes/auth.py and
    database/user_db.py. Per-user features added in MLSS-Mobile (push
    subscriptions, severity preferences, notification history) need a
    stable users.id to scope rows by, so we lazy-insert on first use.

    Idempotent: a second call after the row exists returns the same id.
    Only auto-inserts if the session username matches ALLOWED_GITHUB_USER —
    never creates rows for arbitrary session values.
    """
    bootstrap = getattr(state, "ALLOWED_GITHUB_USER", None)
    if not bootstrap or username.lower() != bootstrap.lower():
        return None
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(_db_file())
    try:
        try:
            cur = conn.execute(
                "INSERT INTO users "
                "(github_username, display_name, role, created_at, is_active) "
                "VALUES (lower(?), ?, 'admin', ?, 1)",
                (username, username, now),
            )
            conn.commit()
            log.info(
                "Lazy-created users row for bootstrap admin %s (id=%s)",
                username, cur.lastrowid,
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            # Someone (another request, an admin via /admin) added them
            # between our SELECT and INSERT. Re-read.
            row = conn.execute(
                "SELECT id FROM users WHERE lower(github_username) = lower(?)",
                (username,),
            ).fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _current_user_id() -> int | None:
    """Return the logged-in user's row id, or None if the session is
    anonymous / pre-1.0 (no user_id field).

    Sessions created by the OAuth flow include ``user_id`` directly; older
    sessions (pre-MLSS-mobile) only have the github_username under ``user``
    — fall back to a lookup so existing browser cookies don't 401 the
    user out of the new endpoints.

    If the user is the bootstrap admin (env-var login, no DB row by
    design), lazy-create a users row so per-user features have a stable
    id to scope by.
    """
    uid = session.get("user_id")
    if uid is not None:
        return int(uid)
    username = session.get("user")
    if not username:
        return None
    conn = sqlite3.connect(_db_file())
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE lower(github_username) = lower(?)",
            (username,),
        ).fetchone()
    finally:
        conn.close()
    if row is not None:
        return row[0]
    # Last-chance fallback: bootstrap admin (env-var) doesn't have a DB
    # row by design. Create one on demand so notifications work for them
    # without an operator having to add themselves via Settings → Users.
    return _ensure_bootstrap_admin_row(username)


# ── VAPID public key ─────────────────────────────────────────────────────

@api_notifications_bp.route("/api/notifications/vapid-key", methods=["GET"])
@require_role("viewer", "controller", "admin")
def vapid_key():
    """Return the VAPID public key the browser passes to ``subscribe()``."""
    return jsonify({"public_key": vapid.get_public_key()})


# ── Subscriptions ────────────────────────────────────────────────────────

@api_notifications_bp.route("/api/notifications/subscriptions", methods=["GET"])
@require_role("viewer", "controller", "admin")
def list_subscriptions():
    """List the current user's push subscriptions.

    Sensitive crypto material (endpoint URL, p256dh, auth) is deliberately
    excluded — only id + label + timestamps are returned so the settings
    UI can render a "Remove" button per device without leaking material
    that would let another tab/extension impersonate the device.
    """
    user_id = _current_user_id()
    if user_id is None:
        return jsonify({"error": "user not found"}), 401
    conn = sqlite3.connect(_db_file())
    try:
        rows = conn.execute(
            "SELECT id, device_label, created_at, last_used_at "
            "FROM push_subscriptions WHERE user_id = ? "
            "ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return jsonify([
        {"id": r[0], "device_label": r[1],
         "created_at": r[2], "last_used_at": r[3]}
        for r in rows
    ])


@api_notifications_bp.route("/api/notifications/subscriptions", methods=["POST"])
@require_role("viewer", "controller", "admin")
def add_subscription():
    """Register a new push subscription for the current user.

    The browser ``subscribe()`` call returns the endpoint + keys. An
    ``ON CONFLICT(endpoint)`` upsert handles re-subscriptions: the same
    device re-installing the PWA or rotating keys keeps a single row
    rather than accumulating dead duplicates.
    """
    user_id = _current_user_id()
    if user_id is None:
        return jsonify({"error": "user not found"}), 401
    body = request.get_json(silent=True) or {}
    endpoint = body.get("endpoint")
    p256dh   = body.get("p256dh")
    auth     = body.get("auth")
    if not (endpoint and p256dh and auth):
        return jsonify({"error": "endpoint, p256dh, auth required"}), 400
    device_label = body.get("device_label", "")
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(_db_file())
    try:
        cur = conn.execute(
            "INSERT INTO push_subscriptions "
            "(user_id, endpoint, p256dh, auth, device_label, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(endpoint) DO UPDATE SET "
            "  p256dh = excluded.p256dh, "
            "  auth = excluded.auth, "
            "  device_label = excluded.device_label, "
            "  last_used_at = CURRENT_TIMESTAMP",
            (user_id, endpoint, p256dh, auth, device_label, now),
        )
        # lastrowid is populated for fresh INSERTs but reads as 0 when
        # the upsert took the UPDATE branch — fall back to a lookup so
        # the client always gets a usable id.
        sub_id = cur.lastrowid
        if not sub_id:
            sub_id = conn.execute(
                "SELECT id FROM push_subscriptions WHERE endpoint = ?",
                (endpoint,),
            ).fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    return jsonify({"message": "Subscribed", "id": sub_id})


@api_notifications_bp.route(
    "/api/notifications/subscriptions/<int:sub_id>", methods=["DELETE"]
)
@require_role("viewer", "controller", "admin")
def remove_subscription(sub_id):
    """Delete one of the current user's subscriptions.

    The WHERE clause scopes by ``user_id`` so passing another user's
    sub_id returns 404 — preventing cross-user deletion without leaking
    which IDs exist (404 either way).
    """
    user_id = _current_user_id()
    if user_id is None:
        return jsonify({"error": "user not found"}), 401
    conn = sqlite3.connect(_db_file())
    try:
        cur = conn.execute(
            "DELETE FROM push_subscriptions WHERE id = ? AND user_id = ?",
            (sub_id, user_id),
        )
        deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    if deleted == 0:
        return jsonify({"error": "Subscription not found"}), 404
    return jsonify({"message": "Unsubscribed"})


# ── Preferences ──────────────────────────────────────────────────────────

@api_notifications_bp.route("/api/notifications/preferences", methods=["GET"])
@require_role("viewer", "controller", "admin")
def get_preferences():
    """Return the four per-category severity floors for the current user."""
    user_id = _current_user_id()
    if user_id is None:
        return jsonify({"error": "user not found"}), 401
    conn = sqlite3.connect(_db_file())
    try:
        row = conn.execute(
            "SELECT notify_air_quality, notify_grow_units, "
            "       notify_system_health, notify_backup_pipeline "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return jsonify({"error": "user not found"}), 404
    return jsonify({
        "air_quality":     row[0],
        "grow_units":      row[1],
        "system_health":   row[2],
        "backup_pipeline": row[3],
    })


@api_notifications_bp.route("/api/notifications/preferences", methods=["PATCH"])
@require_role("viewer", "controller", "admin")
def patch_preferences():
    """Update one or more severity floors atomically.

    Validation runs over every entry BEFORE any UPDATE is issued so a
    malformed value mid-batch can't leave partial state. Unknown
    categories are rejected too — a typo'd "air_quality_index" must
    surface as a 400, not silently no-op.
    """
    user_id = _current_user_id()
    if user_id is None:
        return jsonify({"error": "user not found"}), 401
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or not body:
        return jsonify({
            "error": "Expected JSON object with at least one key",
        }), 400

    # Validate ALL keys + values first; reject the whole patch on any error.
    for cat, sev in body.items():
        if cat not in _VALID_CATEGORIES:
            return jsonify({"error": f"Unknown category: {cat}"}), 400
        if sev not in _VALID_SEVERITIES:
            return jsonify({
                "error": (
                    f"Invalid severity for {cat}: {sev}. "
                    f"Must be one of {sorted(_VALID_SEVERITIES)}"
                )
            }), 400

    # Apply. Column names are interpolated from a validated allow-list
    # above (_VALID_CATEGORIES), not from raw user input — safe.
    conn = sqlite3.connect(_db_file())
    try:
        for cat, sev in body.items():
            conn.execute(
                f"UPDATE users SET notify_{cat} = ? WHERE id = ?",
                (sev, user_id),
            )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"message": "Preferences saved"})


# ── History ──────────────────────────────────────────────────────────────

@api_notifications_bp.route("/api/notifications/history", methods=["GET"])
@require_role("viewer", "controller", "admin")
def get_history():
    """Return the user's recent notification rows, newest first.

    ?days is clamped to [1, _MAX_HISTORY_DAYS] so a viewer can't trigger
    an unbounded scan. Invalid ?days values silently fall back to the
    30-day default rather than erroring — the inbox should always render.
    """
    user_id = _current_user_id()
    if user_id is None:
        return jsonify({"error": "user not found"}), 401
    try:
        days = int(request.args.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(days, _MAX_HISTORY_DAYS))
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(_db_file())
    try:
        rows = conn.execute(
            "SELECT id, category, severity, title, body, deep_link, "
            "       event_count, delivered_count, failed_count, "
            "       created_at, read_at "
            "FROM notification_history "
            "WHERE user_id = ? AND created_at >= ? "
            "ORDER BY created_at DESC",
            (user_id, cutoff),
        ).fetchall()
    finally:
        conn.close()
    return jsonify([
        {"id": r[0], "category": r[1], "severity": r[2],
         "title": r[3], "body": r[4], "deep_link": r[5],
         "event_count": r[6], "delivered_count": r[7], "failed_count": r[8],
         "created_at": r[9], "read_at": r[10]}
        for r in rows
    ])


@api_notifications_bp.route(
    "/api/notifications/history/mark-read", methods=["POST"]
)
@require_role("viewer", "controller", "admin")
def mark_history_read():
    """Mark every unread row for the current user as read.

    Returns the row count so the UI can show "Marked N as read" feedback
    and immediately decrement its unread badge without a refetch.
    """
    user_id = _current_user_id()
    if user_id is None:
        return jsonify({"error": "user not found"}), 401
    conn = sqlite3.connect(_db_file())
    try:
        cur = conn.execute(
            "UPDATE notification_history "
            "SET read_at = CURRENT_TIMESTAMP "
            "WHERE user_id = ? AND read_at IS NULL",
            (user_id,),
        )
        count = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return jsonify({"message": f"Marked {count} as read", "count": count})
