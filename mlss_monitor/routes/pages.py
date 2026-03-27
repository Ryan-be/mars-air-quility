"""Page routes: dashboard, history, controls, admin."""

from flask import Blueprint, render_template

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
def dashboard():
    return render_template("dashboard.html")


@pages_bp.route("/history")
def history_page():
    return render_template("history.html")


@pages_bp.route("/controls")
def controls_page():
    return render_template("controls.html")


@pages_bp.route("/admin")
def admin():
    return render_template("admin.html")
