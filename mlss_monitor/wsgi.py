"""Gunicorn WSGI entry point for MLSS Monitor.

Imports the Flask app so gunicorn can preload it before forking. Background
services (sensor loop, weather loop, inference engine) are NOT started here
— they must run in the worker process (which serves HTTP), not the master.
The worker starts them via the `post_fork` hook in `gunicorn.conf.py`.

History: an earlier revision called `_start_background_services()` at import
time. With `preload_app = True` that ran in the master — after fork only the
calling thread survives, so the worker served HTTP with an empty hot tier and
a silent event bus. Moving the call into `post_fork` ensures the background
threads run in the same process that handles HTTP requests.

Do NOT call app.run() here — gunicorn handles serving.
"""
from database.init_db import create_db
from mlss_monitor.app import app

# Idempotent schema creation (CREATE TABLE IF NOT EXISTS + ALTER TABLE) — safe
# to run in the master before fork so the worker inherits an up-to-date schema.
create_db()

application = app
