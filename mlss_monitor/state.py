"""
Shared mutable state and hardware references.

Initialised once by app.py at startup; imported by route blueprints.
"""

# Fan control
fan_mode = "auto"
fan_state = "off"

# Last auto-evaluation results (list of RuleResult dicts) for UI display
last_auto_evaluation: list[dict] | None = None
last_auto_action: str | None = None

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
