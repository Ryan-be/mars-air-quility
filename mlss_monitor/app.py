import asyncio
import logging
import math
import os
import ssl
import time
from datetime import datetime
from threading import Thread

import board
import busio
from adafruit_ahtx0 import AHTx0
from adafruit_sgp30 import Adafruit_SGP30
from authlib.integrations.flask_client import OAuth
from flask import Flask, redirect, request, session, url_for

from config import config
from database.db_logger import (
    cleanup_old_weather, get_fan_settings, get_location,
    log_sensor_data, log_weather,
)
from database.init_db import create_db
from external_api_interfaces.kasa_smart_plug import KasaSmartPlug
from external_api_interfaces.open_meteo import OpenMeteoClient
from mlss_monitor import state
from mlss_monitor.fan_controller import SensorReading, build_default_controller
from mlss_monitor.routes import register_routes
from sensor_interfaces.aht20 import read_aht20
from sensor_interfaces.sgp30 import read_sgp30

log = logging.getLogger(__name__)


def _vpd_kpa(temp_c: float, rh: float) -> float:
    if temp_c is None or rh is None or rh <= 0:
        return None
    svp = 0.6108 * math.exp(17.27 * temp_c / (temp_c + 237.3))
    return round(svp * (1 - rh / 100), 4)


# ── Flask app ─────────────────────────────────────────────────────────────────

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates"),
    static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static"),
)

# ── Config ────────────────────────────────────────────────────────────────────

LOG_INTERVAL = int(config.get("LOG_INTERVAL", "10"))
FAN_KASA_SMART_PLUG_IP = config.get("FAN_KASA_SMART_PLUG_IP", "192.168.1.63")
SECRET_KEY = config.get("SECRET_KEY", "mlss-dev-key-change-me-in-production")
app.secret_key = SECRET_KEY

# ── HTTPS / TLS ──────────────────────────────────────────────────────────────
HTTPS_ENABLED = str(config.get("HTTPS_ENABLED", "true")).lower() == "true"
SSL_CERT_FILE = config.get("SSL_CERT_FILE", "certs/cert.pem")
SSL_KEY_FILE = config.get("SSL_KEY_FILE", "certs/key.pem")

# Populate shared state with auth config
state.GITHUB_CLIENT_ID     = config.get("GITHUB_CLIENT_ID", None)
state.GITHUB_CLIENT_SECRET = config.get("GITHUB_CLIENT_SECRET", None)
state.ALLOWED_GITHUB_USER  = config.get("ALLOWED_GITHUB_USER", None)
state.service_start_time   = datetime.utcnow()

# ── GitHub OAuth ──────────────────────────────────────────────────────────────

_oauth = OAuth(app)
if state.GITHUB_CLIENT_ID and state.GITHUB_CLIENT_SECRET:
    state.github_oauth = _oauth.register(
        name="github",
        client_id=state.GITHUB_CLIENT_ID,
        client_secret=state.GITHUB_CLIENT_SECRET,
        access_token_url="https://github.com/login/oauth/access_token",
        authorize_url="https://github.com/login/oauth/authorize",
        api_base_url="https://api.github.com/",
        client_kwargs={"scope": "read:user"},
    )

# ── Fan controller ────────────────────────────────────────────────────────────

fan_controller = build_default_controller()

# ── API clients ───────────────────────────────────────────────────────────────

state.open_meteo = OpenMeteoClient()

# ── Auth middleware ────────────────────────────────────────────────────────────

_PUBLIC_ENDPOINTS = {"auth.login", "auth.logout", "auth.github_login",
                     "auth.github_callback", "static"}


def _auth_configured():
    return bool(state.github_oauth)


@app.before_request
def check_auth():
    if (
        _auth_configured()
        and request.endpoint not in _PUBLIC_ENDPOINTS
        and not session.get("logged_in")
    ):
        return redirect(url_for("auth.login"))
    return None


@app.context_processor
def inject_auth_state():
    return {
        "auth_enabled":         _auth_configured(),
        "github_oauth_enabled": bool(state.github_oauth),
        "session_user":         session.get("user", ""),
        "session_role":         session.get("user_role", ""),
    }


# ── Register route blueprints ────────────────────────────────────────────────

register_routes(app)

# ── Hardware init ─────────────────────────────────────────────────────────────

i2c = busio.I2C(board.SCL, board.SDA)

try:
    aht20 = AHTx0(i2c)
    state.aht20 = aht20
except (OSError, ValueError) as e:
    log.error("Failed to initialize AHT20 sensor: %s", e)
    aht20 = None
except Exception as e:
    log.error("Unexpected error initializing AHT20 sensor: %s", e)
    aht20 = None

try:
    sgp30 = Adafruit_SGP30(i2c)
    state.sgp30 = sgp30
except (OSError, ValueError) as e:
    log.error("Failed to initialize SGP30 sensor: %s", e)
    sgp30 = None
except Exception as e:
    log.error("Unexpected error initializing SGP30 sensor: %s", e)
    sgp30 = None

# ── Smart plug & async event loop ────────────────────────────────────────────

state.fan_smart_plug = KasaSmartPlug(FAN_KASA_SMART_PLUG_IP)

thread_loop = asyncio.new_event_loop()
state.thread_loop = thread_loop


def _start_thread_event_loop():
    asyncio.set_event_loop(thread_loop)
    thread_loop.run_forever()


Thread(target=_start_thread_event_loop, daemon=True).start()


# ── Sensor reading ────────────────────────────────────────────────────────────

def read_sensors():
    temperature, humidity, eco2, tvoc = 0, 0, 0, 0

    if aht20:
        try:
            temperature, humidity = read_aht20()
        except Exception as e:
            log.error("Error reading AHT20 sensor: %s", e)

    if sgp30 and humidity > 0:
        try:
            sgp30.set_iaq_relative_humidity(celcius=temperature, relative_humidity=humidity)
        except Exception as e:
            log.error("Error setting SGP30 humidity compensation: %s", e)

    if sgp30:
        try:
            eco2, tvoc = read_sgp30()
        except Exception as e:
            log.error("Error reading SGP30 sensor: %s", e)

    return temperature, humidity, eco2, tvoc


# ── Background logging ────────────────────────────────────────────────────────

def log_data():
    temp, hum, eco2, tvoc = read_sensors()

    fan_power_w = None
    try:
        power_future = asyncio.run_coroutine_threadsafe(
            state.fan_smart_plug.get_power(), thread_loop
        )
        power_data = power_future.result(timeout=5)
        fan_power_w = power_data.get("power_w")
    except Exception as exc:
        log.error("[log_data] get_power failed: %s", exc)

    vpd = _vpd_kpa(temp, hum)
    log_sensor_data(temp, hum, eco2, tvoc, fan_power_w=fan_power_w, vpd_kpa=vpd)

    settings = get_fan_settings()
    if settings["enabled"] and state.fan_mode == "auto":
        reading = SensorReading(
            temperature=temp, humidity=hum, eco2=eco2, tvoc=tvoc, vpd_kpa=vpd,
        )
        try:
            action, results = fan_controller.evaluate(reading, settings)
            state.last_auto_action = action
            state.last_auto_evaluation = [
                {"rule": r.rule_name, "action": r.action.value, "reason": r.reason}
                for r in results
            ]
            state.fan_state = action
            asyncio.run_coroutine_threadsafe(
                state.fan_smart_plug.switch(action == "on"), thread_loop
            )
        except Exception as e:
            log.error("Error controlling smart plug fan: %s", e)


_log_cycle = 0
_CYCLE_60S  = max(1, 60 // LOG_INTERVAL)          # short-term analysis
_CYCLE_1H   = max(1, 3600 // LOG_INTERVAL)         # hourly analysis
_CYCLE_24H  = max(1, 86400 // LOG_INTERVAL)         # daily analysis


def _background_log():
    global _log_cycle
    asyncio.set_event_loop(thread_loop)
    while True:
        try:
            log_data()
        except Exception as e:
            log.error("Error in background log loop: %s", e)

        _log_cycle += 1

        # Short-term detectors every ~60s
        if _log_cycle % _CYCLE_60S == 0:
            try:
                from mlss_monitor.inference_engine import run_analysis
                run_analysis()
            except Exception as e:
                log.error("Inference engine error: %s", e)

        # Hourly detectors every ~1h
        if _log_cycle % _CYCLE_1H == 0:
            try:
                from mlss_monitor.inference_engine import run_hourly_analysis
                run_hourly_analysis()
            except Exception as e:
                log.error("Hourly inference error: %s", e)

        # Daily detectors every ~24h
        if _log_cycle % _CYCLE_24H == 0:
            try:
                from mlss_monitor.inference_engine import run_daily_analysis
                run_daily_analysis()
            except Exception as e:
                log.error("Daily inference error: %s", e)

        time.sleep(LOG_INTERVAL)


def _weather_log_loop():
    time.sleep(30)
    while True:
        try:
            loc = get_location()
            if loc and loc.get("lat") is not None:
                w = state.open_meteo.get_current_weather(loc["lat"], loc["lon"])
                log_weather(w["temp"], w["humidity"], w["feels_like"],
                            w["wind_speed"], w["weather_code"], w["uv_index"])
                cleanup_old_weather(days=7)
                log.info("Weather logged: %.1f°C, %d%%RH", w["temp"], w["humidity"])
        except Exception as e:
            log.error("Weather log error: %s", e)
        time.sleep(3600)


# ── HTTPS helpers ────────────────────────────────────────────────────────────

def _build_ssl_context():
    if not HTTPS_ENABLED:
        return None

    cert = os.path.abspath(SSL_CERT_FILE)
    key = os.path.abspath(SSL_KEY_FILE)

    if not os.path.isfile(cert) or not os.path.isfile(key):
        log.warning("SSL cert/key not found (%s, %s) — falling back to HTTP. "
                    "Run 'python scripts/generate_certs.py' to create a self-signed certificate.", cert, key)
        return None

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=cert, keyfile=key)
    log.info("TLS enabled — cert=%s key=%s", cert, key)
    return ctx


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    create_db()
    if state.github_oauth:
        log.info("🔒 Auth ENABLED — GitHub OAuth")
        if state.ALLOWED_GITHUB_USER:
            log.info("   Bootstrap admin: %s (via MLSS_ALLOWED_GITHUB_USER)",
                     state.ALLOWED_GITHUB_USER)
    else:
        log.warning("⚠️  Auth DISABLED — set MLSS_GITHUB_CLIENT_ID / "
                    "MLSS_GITHUB_CLIENT_SECRET in .env")
    # Sync fan_mode from persisted settings
    _fan_settings = get_fan_settings()
    state.fan_mode = "auto" if _fan_settings["enabled"] else "manual"

    # Backfill any missing long-term inferences from historical data
    try:
        from mlss_monitor.inference_engine import run_startup_analysis
        run_startup_analysis()
    except Exception as e:
        log.error("Startup analysis failed: %s", e)

    Thread(target=_background_log, daemon=True).start()
    Thread(target=_weather_log_loop, daemon=True).start()

    ssl_ctx = _build_ssl_context()
    port = 5000
    protocol = "https" if ssl_ctx else "http"
    log.info("Starting server on %s://0.0.0.0:%d", protocol, port)
    app.run(host="0.0.0.0", port=port, ssl_context=ssl_ctx)


if __name__ == "__main__":
    main()
