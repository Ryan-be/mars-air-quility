"""Gunicorn configuration for MLSS Monitor (Raspberry Pi production)."""
import os

bind = "0.0.0.0:5000"

# gthread: bounded thread pool — prevents unlimited thread creation.
# Unlike Werkzeug threaded=True, this caps concurrent connections.
worker_class = "gthread"
workers = 1
threads = 8          # max 8 concurrent requests (incl. SSE connections)
timeout = 0  # gthread workers: disable timeout — SSE heartbeat (10s) keeps connections alive;
             # per-connection lifetime (600s) is enforced by generate() in api_stream.py
keepalive = 5

# SSL — use the same config keys as app.py so certs/cert.pem + certs/key.pem
# (relative to WorkingDirectory) are picked up automatically.
try:
    from config import config as _cfg
    _cert = _cfg.get("SSL_CERT_FILE", "certs/cert.pem")
    _key  = _cfg.get("SSL_KEY_FILE",  "certs/key.pem")
    certfile = _cert if _cert and os.path.isfile(_cert) else None
    keyfile  = _key  if _key  and os.path.isfile(_key)  else None
except Exception:
    certfile = None
    keyfile  = None

# Logging
accesslog = "-"
errorlog  = "-"
loglevel  = "info"

# Preload app ONCE before forking workers (background threads start once)
preload_app = True
