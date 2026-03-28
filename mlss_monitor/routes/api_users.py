"""User management API routes — admin only.

All users authenticate via GitHub OAuth. These endpoints manage which GitHub
accounts are authorised and what role each holds.

GET    /api/users                   List all registered users
POST   /api/users                   Add a GitHub user with a role
PATCH  /api/users/<id>/role         Change a user's role
DELETE /api/users/<id>              Deactivate a user
"""

from flask import Blueprint, jsonify, request, session

from database.user_db import (
    add_user,
    admin_count,
    deactivate_user,
    get_user_by_id,
    list_users,
    update_user_role,
)
from mlss_monitor.rbac import require_role

api_users_bp = Blueprint("api_users", __name__)


@api_users_bp.route("/api/users", methods=["GET"])
@require_role("admin")
def get_users():
    return jsonify(list_users())


@api_users_bp.route("/api/users", methods=["POST"])
@require_role("admin")
def create_user_route():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    github_username = (data.get("github_username") or "").strip()
    role            = (data.get("role") or "viewer").strip()
    display_name    = (data.get("display_name") or "").strip()

    if not github_username:
        return jsonify({"error": "github_username is required"}), 400

    try:
        user = add_user(github_username, role, display_name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(user), 201


@api_users_bp.route("/api/users/<int:user_id>/role", methods=["PATCH"])
@require_role("admin")
def update_role(user_id: int):
    data = request.get_json(silent=True) or {}
    role = (data.get("role") or "").strip()
    try:
        updated = update_user_role(user_id, role)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not updated:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"message": "Role updated"})


@api_users_bp.route("/api/users/<int:user_id>", methods=["DELETE"])
@require_role("admin")
def delete_user(user_id: int):
    # Prevent self-deletion via DB user_id
    if session.get("user_id") == user_id:
        return jsonify({"error": "Cannot remove your own account"}), 400

    user = get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Prevent removing the last admin (env-var admin is always available as fallback)
    if user["role"] == "admin" and admin_count() <= 1:
        return jsonify({"error": "Cannot remove the last admin account"}), 400

    deactivate_user(user_id)
    return jsonify({"message": "User removed"})
