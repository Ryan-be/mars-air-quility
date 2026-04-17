"""Gunicorn configuration for MLSS Monitor (Raspberry Pi production)."""
import os

bind = "0.0.0.0:5000"

# gthread: bounded thread pool — prevents unlimited thread creation.
# Unlike Werkzeug threaded=True, this caps concurrent connections.
worker_class = "gthread"
workers = 1
threads = 8          # max 8 concurrent requests (incl. SSE connections)
timeout = 120
keepalive = 5

# SSL — read paths from environment, same as app.py
certfile = os.environ.get("MLSS_SSL_CERT_FILE", "") or None
keyfile  = os.environ.get("MLSS_SSL_KEY_FILE", "") or None

# Logging
accesslog = "-"
errorlog  = "-"
loglevel  = "info"

# Preload app ONCE before forking workers (background threads start once)
preload_app = True
