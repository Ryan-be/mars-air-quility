"""
Shared mutable state and hardware references.

Initialised once by app.py at startup; imported by route blueprints.
"""

# Fan control
fan_mode = "auto"
fan_state = "off"

# Hardware references (set by app.py after init)
fan_smart_plug = None
thread_loop = None
aht20 = None
sgp30 = None

# API clients
open_meteo = None

# Config values (set by app.py)
service_start_time = None

# Auth (GitHub OAuth)
GITHUB_CLIENT_ID = None
GITHUB_CLIENT_SECRET = None
ALLOWED_GITHUB_USER = None
github_oauth = None
