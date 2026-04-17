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

    `preload_app = True` imports the app in the master before forking. Only
    the calling thread survives `fork()`, so threads started at module import
    (the asyncio `thread_loop` in app.py used for smart-plug I/O) and threads
    started by `_start_background_services()` are all left behind in the
    master process — without this hook, the worker serves HTTP with an empty
    hot tier, a silent event bus, and a dead asyncio loop (every
    `run_coroutine_threadsafe` call times out, which is why `get_power` was
    failing with no message).

    This hook rebuilds both in the worker:
    - a fresh `asyncio.new_event_loop()` + its driver thread, so the Kasa
      smart-plug coroutines have a running loop to dispatch to;
    - the background services (sensor loop, weather loop, detection engine,
      anomaly bootstrap Timer) via `_start_background_services()` after
      clearing the idempotency guard inherited from the master.
    """
    try:
        import asyncio
        from threading import Thread
        from mlss_monitor import app as _app_mod
        # Fresh asyncio loop for this process (the master's loop object was
        # inherited by fork but its driver thread is gone — submitting work
        # to it just hangs).
        _app_mod.thread_loop = asyncio.new_event_loop()
        _app_mod.state.thread_loop = _app_mod.thread_loop
        Thread(target=_app_mod._start_thread_event_loop, daemon=True).start()
        # Reset the idempotency Event (set in master via preload) so services
        # actually start here.
        _app_mod._services_started.clear()
        _app_mod._start_background_services()
        server.log.info(
            "post_fork: thread_loop + background services restarted in worker pid=%d",
            worker.pid,
        )
    except Exception as exc:  # pragma: no cover — best-effort recovery
        server.log.exception("post_fork: failed to restart background services: %s", exc)
