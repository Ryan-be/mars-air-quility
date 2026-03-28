"""Authentication routes: GitHub OAuth only.

Login flow
----------
1. User clicks "Sign in with GitHub" → /auth/github → GitHub OAuth server.
2. GitHub redirects back to /auth/callback with a code.
3. We exchange the code for a token and fetch the user's GitHub username.
4. Look the username up in the users table to determine their role.
5. If not in the DB but matches MLSS_ALLOWED_GITHUB_USER env var, grant admin
   (bootstrap / recovery path).
6. Otherwise deny with an error.

Session keys set on success
----------------------------
  logged_in  bool   Always True
  user       str    GitHub username
  user_role  str    'admin' | 'controller' | 'viewer'
  user_id    int|None  DB row id, or None for the env-var bootstrap admin
"""

import logging

from flask import Blueprint, redirect, render_template, session, url_for

from database.user_db import get_user_by_github, record_login
from mlss_monitor import state

log = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login")
def login():
    # Nothing to POST; just show the GitHub OAuth button
    return render_template("login.html", error=None)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/auth/github")
def github_login():
    if not state.github_oauth:
        return render_template("login.html",
                               error="GitHub OAuth is not configured on this server.")
    redirect_uri = url_for("auth.github_callback", _external=True)
    return state.github_oauth.authorize_redirect(redirect_uri)


@auth_bp.route("/auth/callback")
def github_callback():
    if not state.github_oauth:
        return redirect(url_for("auth.login"))

    try:
        token    = state.github_oauth.authorize_access_token()
        userinfo = state.github_oauth.get("user", token=token).json()
        username = userinfo.get("login", "").strip()
    except Exception:
        log.exception("GitHub OAuth callback error")
        return render_template("login.html",
                               error="GitHub authentication failed. Please try again.")

    if not username:
        return render_template("login.html",
                               error="Could not retrieve GitHub username.")

    # 1. Check the users table first
    db_user = get_user_by_github(username)
    if db_user:
        record_login(username)
        _set_session(username, db_user["role"], db_user["id"])
        log.info("Login: %s (role=%s, db_id=%s)", username, db_user["role"], db_user["id"])
        return redirect(url_for("pages.dashboard"))

    # 2. Fall back to the bootstrap env-var admin
    if state.ALLOWED_GITHUB_USER and username.lower() == state.ALLOWED_GITHUB_USER.lower():
        _set_session(username, "admin", None)
        log.info("Login: %s (bootstrap admin via ALLOWED_GITHUB_USER)", username)
        return redirect(url_for("pages.dashboard"))

    # 3. Not authorised
    log.warning("Rejected login attempt from GitHub user '%s'", username)
    return render_template(
        "login.html",
        error=(
            f"GitHub user '{username}' is not authorised. "
            "Ask an admin to add your account under Settings → Users."
        ),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _set_session(username: str, role: str, user_id):
    session["logged_in"] = True
    session["user"]      = username
    session["user_role"] = role
    session["user_id"]   = user_id
