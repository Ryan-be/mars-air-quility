"""Role-Based Access Control helpers.

Roles (in ascending privilege order):
  viewer     – read-only access to all sensor/weather data
  controller – viewer + can operate physical controls (fan on/off, annotate)
  admin      – full access including settings, user management
"""

from functools import wraps

from flask import jsonify, redirect, request, session, url_for

ROLES = ("viewer", "controller", "admin")

# Minimum role required for each privilege tier
_TIER_VIEWER     = frozenset({"viewer", "controller", "admin"})
_TIER_CONTROLLER = frozenset({"controller", "admin"})
_TIER_ADMIN      = frozenset({"admin"})


def current_role() -> str:
    """Return the role of the currently logged-in user, defaulting to viewer."""
    return session.get("user_role", "viewer")


def require_role(*roles: str):
    """Decorator: require the logged-in user to hold one of the given roles.

    API routes (path starts with /api/) receive JSON 401/403 responses.
    Page routes receive a redirect to dashboard (or login if not authenticated).
    """
    allowed = frozenset(roles)

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get("logged_in"):
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Unauthorised"}), 401
                return redirect(url_for("auth.login"))
            if current_role() not in allowed:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Forbidden: insufficient permissions"}), 403
                return redirect(url_for("pages.dashboard"))
            return f(*args, **kwargs)

        return decorated_function

    return decorator


# Convenience aliases
require_admin      = require_role("admin")
require_controller = require_role("controller", "admin")
