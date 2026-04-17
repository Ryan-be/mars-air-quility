"""Gunicorn WSGI entry point for MLSS Monitor.

Starts background services (sensor loop, log loop, weather loop) once
at import time, before gunicorn forks workers. The Flask app is then
served by gunicorn instead of Werkzeug's dev server.

Do NOT call app.run() here — gunicorn handles serving.
"""
from mlss_monitor.app import app, _start_background_services

_start_background_services()  # idempotent — safe to call multiple times

application = app
