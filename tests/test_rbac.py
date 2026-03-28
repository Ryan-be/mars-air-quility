"""Tests for RBAC: user DB operations, login log, role enforcement on API routes."""
import pytest

import database.user_db as udb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _session_for(client, role, user_id=None, username="testuser"):
    """Open a Flask test session with a given role."""
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user"] = username
        sess["user_role"] = role
        sess["user_id"] = user_id


# ---------------------------------------------------------------------------
# User DB — add / retrieve
# ---------------------------------------------------------------------------

class TestAddUser:
    def test_add_and_retrieve(self, db):
        user = udb.add_user("octocat", "viewer")
        assert user["github_username"] == "octocat"
        assert user["role"] == "viewer"
        assert user["is_active"] is True

    def test_username_stored_lowercase(self, db):
        udb.add_user("OctoCat", "admin")
        user = udb.get_user_by_github("octocat")
        assert user is not None

    def test_duplicate_raises(self, db):
        udb.add_user("octocat", "viewer")
        with pytest.raises(ValueError, match="already registered"):
            udb.add_user("octocat", "admin")

    def test_invalid_role_raises(self, db):
        with pytest.raises(ValueError, match="Invalid role"):
            udb.add_user("octocat", "superuser")

    def test_empty_username_raises(self, db):
        with pytest.raises(ValueError):
            udb.add_user("", "viewer")


class TestGetUser:
    def test_not_found_returns_none(self, db):
        assert udb.get_user_by_github("nobody") is None

    def test_inactive_not_returned(self, db):
        udb.add_user("octocat", "viewer")
        user = udb.get_user_by_github("octocat")
        udb.deactivate_user(user["id"])
        assert udb.get_user_by_github("octocat") is None

    def test_get_by_id(self, db):
        udb.add_user("octocat", "controller")
        user = udb.get_user_by_github("octocat")
        by_id = udb.get_user_by_id(user["id"])
        assert by_id["github_username"] == "octocat"


# ---------------------------------------------------------------------------
# User DB — mutations
# ---------------------------------------------------------------------------

class TestUpdateRole:
    def test_change_role(self, db):
        udb.add_user("octocat", "viewer")
        user = udb.get_user_by_github("octocat")
        udb.update_user_role(user["id"], "admin")
        assert udb.get_user_by_github("octocat")["role"] == "admin"

    def test_invalid_role_raises(self, db):
        udb.add_user("octocat", "viewer")
        user = udb.get_user_by_github("octocat")
        with pytest.raises(ValueError):
            udb.update_user_role(user["id"], "god")

    def test_returns_false_for_unknown_id(self, db):
        assert udb.update_user_role(9999, "admin") is False


class TestDeactivate:
    def test_deactivates_user(self, db):
        udb.add_user("octocat", "viewer")
        user = udb.get_user_by_github("octocat")
        udb.deactivate_user(user["id"])
        assert udb.get_user_by_github("octocat") is None

    def test_shown_as_inactive_in_list(self, db):
        # list_users() shows all users (active + inactive) for the admin UI
        udb.add_user("octocat", "viewer")
        user = udb.get_user_by_github("octocat")
        udb.deactivate_user(user["id"])
        match = next((u for u in udb.list_users() if u["github_username"] == "octocat"), None)
        assert match is not None
        assert match["is_active"] is False


class TestAdminCount:
    def test_zero_when_empty(self, db):
        assert udb.admin_count() == 0

    def test_counts_only_admins(self, db):
        udb.add_user("alice", "admin")
        udb.add_user("bob", "viewer")
        assert udb.admin_count() == 1

    def test_deactivated_not_counted(self, db):
        udb.add_user("alice", "admin")
        user = udb.get_user_by_github("alice")
        udb.deactivate_user(user["id"])
        assert udb.admin_count() == 0


# ---------------------------------------------------------------------------
# Login log
# ---------------------------------------------------------------------------

class TestLoginLog:
    def test_record_and_retrieve(self, db):
        udb.add_user("octocat", "viewer")
        udb.record_login("octocat")
        logs = udb.get_login_log("octocat")
        assert len(logs) == 1
        assert "logged_in_at" in logs[0]

    def test_multiple_logins(self, db):
        udb.add_user("octocat", "viewer")
        udb.record_login("octocat")
        udb.record_login("octocat")
        udb.record_login("octocat")
        assert len(udb.get_login_log("octocat")) == 3

    def test_no_logins_returns_empty(self, db):
        udb.add_user("octocat", "viewer")
        assert udb.get_login_log("octocat") == []

    def test_updates_last_login(self, db):
        udb.add_user("octocat", "viewer")
        udb.record_login("octocat")
        user = udb.get_user_by_github("octocat")
        assert user["last_login"] is not None

    def test_limit_respected(self, db):
        udb.add_user("octocat", "viewer")
        for _ in range(25):
            udb.record_login("octocat")
        assert len(udb.get_login_log("octocat", limit=10)) == 10

    def test_case_insensitive(self, db):
        udb.add_user("octocat", "viewer")
        udb.record_login("OctoCat")
        assert len(udb.get_login_log("octocat")) == 1


# ---------------------------------------------------------------------------
# API — role enforcement on write endpoints
# ---------------------------------------------------------------------------

class TestFanControlRBAC:
    """POST /api/fan requires controller or admin."""

    def test_viewer_gets_403(self, app_client, db):
        client, _ = app_client
        _session_for(client, "viewer")
        resp = client.post("/api/fan?state=on")
        assert resp.status_code == 403

    def test_controller_allowed(self, app_client, db):
        client, _ = app_client
        _session_for(client, "controller")
        # Role check passes; plug call may fail (no real hardware) → 200 or 500, never 403
        resp = client.post("/api/fan?state=auto")
        assert resp.status_code != 403

    def test_admin_allowed(self, app_client, db):
        client, _ = app_client
        _session_for(client, "admin")
        resp = client.post("/api/fan?state=auto")
        assert resp.status_code != 403


class TestFanSettingsRBAC:
    """POST /api/fan/settings requires admin only."""

    def test_viewer_gets_403(self, app_client, db):
        client, _ = app_client
        _session_for(client, "viewer")
        resp = client.post("/api/fan/settings",
                           json={"tvoc_min": 0, "tvoc_max": 500,
                                 "temp_min": 0.0, "temp_max": 25.0, "enabled": False})
        assert resp.status_code == 403

    def test_controller_gets_403(self, app_client, db):
        client, _ = app_client
        _session_for(client, "controller")
        resp = client.post("/api/fan/settings",
                           json={"tvoc_min": 0, "tvoc_max": 500,
                                 "temp_min": 0.0, "temp_max": 25.0, "enabled": False})
        assert resp.status_code == 403

    def test_admin_allowed(self, app_client, db):
        client, _ = app_client
        _session_for(client, "admin")
        resp = client.post("/api/fan/settings",
                           json={"tvoc_min": 0, "tvoc_max": 500,
                                 "temp_min": 0.0, "temp_max": 25.0, "enabled": False})
        assert resp.status_code == 200


class TestSettingsRBAC:
    """POST /api/settings/* requires admin."""

    def test_viewer_cannot_save_location(self, app_client, db):
        client, _ = app_client
        _session_for(client, "viewer")
        resp = client.post("/api/settings/location",
                           json={"lat": 51.5, "lon": -0.1, "name": "London"})
        assert resp.status_code == 403

    def test_controller_cannot_save_energy(self, app_client, db):
        client, _ = app_client
        _session_for(client, "controller")
        resp = client.post("/api/settings/energy",
                           json={"unit_rate_pence": 28.5})
        assert resp.status_code == 403

    def test_admin_can_save_location(self, app_client, db):
        client, _ = app_client
        _session_for(client, "admin")
        resp = client.post("/api/settings/location",
                           json={"lat": 51.5, "lon": -0.1, "name": "London"})
        assert resp.status_code == 200


class TestUserManagementRBAC:
    """All /api/users/* require admin."""

    def test_viewer_cannot_list_users(self, app_client, db):
        client, _ = app_client
        _session_for(client, "viewer")
        assert client.get("/api/users").status_code == 403

    def test_controller_cannot_list_users(self, app_client, db):
        client, _ = app_client
        _session_for(client, "controller")
        assert client.get("/api/users").status_code == 403

    def test_admin_can_list_users(self, app_client, db):
        client, _ = app_client
        _session_for(client, "admin")
        assert client.get("/api/users").status_code == 200

    def test_admin_can_add_user(self, app_client, db):
        client, _ = app_client
        _session_for(client, "admin")
        resp = client.post("/api/users",
                           json={"github_username": "newuser", "role": "viewer"})
        assert resp.status_code == 201
        assert resp.get_json()["github_username"] == "newuser"

    def test_cannot_add_duplicate_user(self, app_client, db):
        client, _ = app_client
        _session_for(client, "admin")
        client.post("/api/users", json={"github_username": "newuser", "role": "viewer"})
        resp = client.post("/api/users", json={"github_username": "newuser", "role": "admin"})
        assert resp.status_code == 400

    def test_cannot_delete_last_admin(self, app_client, db):
        client, _ = app_client
        _session_for(client, "admin", user_id=None)
        udb.add_user("onlyadmin", "admin")
        user = udb.get_user_by_github("onlyadmin")
        resp = client.delete(f"/api/users/{user['id']}")
        assert resp.status_code == 400

    def test_admin_can_get_user_logins(self, app_client, db):
        client, _ = app_client
        _session_for(client, "admin")
        udb.add_user("loguser", "viewer")
        user = udb.get_user_by_github("loguser")
        udb.record_login("loguser")
        resp = client.get(f"/api/users/{user['id']}/logins")
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_viewer_cannot_get_user_logins(self, app_client, db):
        client, _ = app_client
        _session_for(client, "viewer")
        resp = client.get("/api/users/1/logins")
        assert resp.status_code == 403


class TestAdminPageRBAC:
    """GET /admin page requires admin role."""

    def test_viewer_redirected_from_admin(self, app_client, db):
        client, _ = app_client
        _session_for(client, "viewer")
        resp = client.get("/admin")
        assert resp.status_code == 302

    def test_controller_redirected_from_admin(self, app_client, db):
        client, _ = app_client
        _session_for(client, "controller")
        resp = client.get("/admin")
        assert resp.status_code == 302

    def test_admin_can_access_admin_page(self, app_client, db):
        client, _ = app_client
        _session_for(client, "admin")
        resp = client.get("/admin")
        assert resp.status_code == 200
