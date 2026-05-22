"""Tests for the /notifications page route."""

from pathlib import Path

import pytest
from flask import Flask

from mlss_monitor.routes.auth import auth_bp
from mlss_monitor.routes.pages import pages_bp


@pytest.fixture
def app():
    a = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parents[2] / "templates"),
        static_folder=str(Path(__file__).resolve().parents[2] / "static"),
    )
    a.config["TESTING"] = True
    a.secret_key = "test"
    # auth_bp must be registered so require_role's url_for("auth.login")
    # resolves on the unauthenticated-redirect path.
    a.register_blueprint(auth_bp)
    a.register_blueprint(pages_bp)
    return a


def test_notifications_route_requires_auth(app):
    with app.test_client() as c:
        r = c.get("/notifications")
        # Without session => redirect (302) or 401
        assert r.status_code in (302, 401)


def test_notifications_route_renders_for_viewer(app):
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user"] = "alice"
            sess["user_role"] = "viewer"
            sess["user_id"] = 1
        r = c.get("/notifications")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "inbox-mount" in body
        # confirm the inbox CSS is linked
        assert "notifications.css" in body
