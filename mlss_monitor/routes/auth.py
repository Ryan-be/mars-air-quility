"""Authentication routes: login, logout, GitHub OAuth."""

from flask import Blueprint, redirect, render_template, request, session, url_for

from mlss_monitor import state

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST" and state.AUTH_USERNAME and state.AUTH_PASSWORD:
        if (request.form.get("username") == state.AUTH_USERNAME and
                request.form.get("password") == state.AUTH_PASSWORD):
            session["logged_in"] = True
            return redirect(url_for("pages.dashboard"))
        return render_template("login.html", error="Invalid username or password")
    return render_template("login.html", error=None)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/auth/github")
def github_login():
    if not state.github_oauth:
        return redirect(url_for("auth.login"))
    redirect_uri = url_for("auth.github_callback", _external=True)
    return state.github_oauth.authorize_redirect(redirect_uri)


@auth_bp.route("/auth/callback")
def github_callback():
    if not state.github_oauth:
        return redirect(url_for("auth.login"))
    try:
        token    = state.github_oauth.authorize_access_token()
        userinfo = state.github_oauth.get("user", token=token).json()
        username = userinfo.get("login", "")
    except Exception:
        return render_template("login.html", error="GitHub authentication failed. Please try again.")

    if state.ALLOWED_GITHUB_USER and username.lower() != state.ALLOWED_GITHUB_USER.lower():
        return render_template("login.html", error=f"GitHub user '{username}' is not authorised.")

    session["logged_in"] = True
    session["user"]      = username
    return redirect(url_for("pages.dashboard"))
