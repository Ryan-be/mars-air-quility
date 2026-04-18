"""
Shared mutable state and hardware references.

Initialised once by app.py at startup; imported by route blueprints.
"""
import threading
from collections import deque

# Fan control
fan_mode = "auto"
fan_state = "off"

# Last auto-evaluation results (list of RuleResult dicts) for UI display
last_auto_evaluation: list[dict] | None = None
last_auto_action: str | None = None

# Lock guarding the four fan_* fields above. The background log loop writes
# all four together every LOG_INTERVAL and HTTP handlers read/write them
# concurrently; without this lock a reader can observe a torn
# last_auto_evaluation list (H3 in threading-audit). Use get_fan_snapshot()
# or update_auto_snapshot() for composite operations; single-field writes
# should also take the lock via the helpers below.
_fan_lock = threading.Lock()


def update_auto_snapshot(action, evaluation, fan_state_value) -> None:
    """Atomically update last_auto_action, last_auto_evaluation, fan_state.

    Used by the background log loop after a fan_controller.evaluate() call
    so that HTTP readers see the three fields change together.
    """
    global last_auto_action, last_auto_evaluation, fan_state
    with _fan_lock:
        last_auto_action = action
        last_auto_evaluation = evaluation
        fan_state = fan_state_value


def get_fan_snapshot() -> dict:
    """Return an atomic copy of fan_mode, fan_state, last_auto_* under the lock."""
    with _fan_lock:
        evaluation = (
            list(last_auto_evaluation) if last_auto_evaluation is not None else None
        )
        return {
            "fan_mode": fan_mode,
            "fan_state": fan_state,
            "last_auto_action": last_auto_action,
            "last_auto_evaluation": evaluation,
        }


def set_fan_mode(mode: str) -> None:
    """Assign fan_mode under the lock."""
    global fan_mode
    with _fan_lock:
        fan_mode = mode


def set_fan_state(value: str) -> None:
    """Assign fan_state under the lock."""
    global fan_state
    with _fan_lock:
        fan_state = value

# Hardware references (set by app.py after init)
fan_smart_plug = None
thread_loop = None
aht20 = None
sgp30 = None
pm_sensor = None
mics6814 = None
hot_tier = None
feature_vector = None

# API clients
open_meteo = None

# Config values (set by app.py)
service_start_time = None

# Auth (GitHub OAuth)
GITHUB_CLIENT_ID = None
GITHUB_CLIENT_SECRET = None
ALLOWED_GITHUB_USER = None
github_oauth = None

# Event bus (SSE push)
event_bus = None

# Detection / attribution engine (set by app.py after init)
detection_engine = None

shadow_log: deque = deque(maxlen=50)  # recent shadow-mode detection events

# Data source enabled/disabled flags (in-memory; reset to True on restart)
# Keys are DataSource.name strings, values are bool.
data_source_enabled: dict[str, bool] = {}

# Live DataSource instances (set by app.py); used by API to read last_reading_at.
data_sources: list = []
