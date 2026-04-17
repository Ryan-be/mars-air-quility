"""Gunicorn configuration for MLSS Monitor (Raspberry Pi production)."""
import os

bind = "0.0.0.0:5000"

# gthread: bounded thread pool — prevents unlimited thread creation.
# Unlike Werkzeug threaded=True, this caps concurrent connections.
worker_class = "gthread"
workers = 1
# Each SSE connection (one per open browser tab) parks a worker thread for up
# to MAX_LIFETIME_SECONDS (600s, enforced in api_stream.py). Each open browser
# tab can also park up to 6 HTTP/1.1 keep-alive threads. 8 threads was trivial
# to exhaust (a single user with 2 open tabs could starve the pool, leaving
# static-file fetches waiting tens of seconds). 32 is cheap with gthread and
# gives ample headroom for static assets + API calls alongside SSE + keep-alive.
threads = 32
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

# Preload app ONCE before forking workers (keeps socket binding before fork,
# and shares parsed modules/ML model state via copy-on-write).
preload_app = True


def post_fork(server, worker):
    """Restart background services INSIDE the worker after fork.

    `preload_app = True` imports wsgi.py in the master, which calls
    `_start_background_services()` there. Only the calling thread survives
    fork(), so the sensor-read loop, weather loop, inference engine and
    anomaly bootstrap Timer are left behind in the master — the worker
    serves HTTP with an empty hot tier and no event bus publishing.

    This hook resets the idempotency guard set in the master and re-starts
    the background threads in the worker, which is where they need to run
    for `/api/data` / `/api/stream` / inference pipelines to see live data.
    """
    try:
        from mlss_monitor import app as _app_mod
        _app_mod._services_started.clear()
        _app_mod._start_background_services()
        server.log.info("post_fork: background services restarted in worker pid=%d", worker.pid)
    except Exception as exc:  # pragma: no cover — best-effort recovery
        server.log.exception("post_fork: failed to restart background services: %s", exc)
