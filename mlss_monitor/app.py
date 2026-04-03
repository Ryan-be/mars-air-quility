import asyncio
import logging
import math
import mimetypes
import os
import ssl
import time
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread

import psutil

import board
import busio
from adafruit_ahtx0 import AHTx0
from adafruit_sgp30 import Adafruit_SGP30
from authlib.integrations.flask_client import OAuth
from flask import Flask, redirect, request, session, url_for

from config import config
from database.db_logger import (
    DB_FILE,
    cleanup_old_weather, get_24h_baselines, get_fan_settings, get_location,
    log_sensor_data, log_weather,
)
from database.init_db import create_db
from external_api_interfaces.kasa_smart_plug import KasaSmartPlug
from external_api_interfaces.open_meteo import OpenMeteoClient
from mlss_monitor import state
from mlss_monitor.event_bus import EventBus
from mlss_monitor.fan_controller import SensorReading, build_default_controller
from mlss_monitor.routes import register_routes
from sensor_interfaces.aht20 import read_aht20
from sensor_interfaces.mics6814 import init_mics6814, read_mics6814
from sensor_interfaces.sb_components_pm_sensor import init_pm_sensor, read_pm
from sensor_interfaces.sgp30 import read_sgp30
from mlss_monitor.hot_tier import HotTier
from mlss_monitor.feature_extractor import FeatureExtractor
from mlss_monitor.detection_engine import DetectionEngine
from mlss_monitor.data_sources import (
    SGP30Source,
    AHT20Source,
    ParticulateSource,
    MICS6814Source,
    merge_readings,
)

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

# Ensure MIME types are correct — browsers enforce strict checking over HTTPS
# and will refuse to apply stylesheets served with the wrong Content-Type.
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/json", ".json")

if HTTPS_ENABLED:
    app.config["PREFERRED_URL_SCHEME"] = "https"
    app.config["SESSION_COOKIE_SECURE"] = True

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

# ── Event bus (SSE push) ──────────────────────────────────────────────────────

state.event_bus = EventBus(max_history=50)

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

# PM sensor (UART — no I2C conflict)
pm_sensor = init_pm_sensor()
if pm_sensor:
    state.pm_sensor = pm_sensor

# MICS6814 gas sensor (I2C — CO, NO2, NH3)
mics6814_sensor = init_mics6814()
if mics6814_sensor:
    state.mics6814 = mics6814_sensor

# --- Hot tier and data source abstraction (parallel addition) ---
# Initialised without DB here so importing app.py never touches the database.
# main() reinitialises with db_file=DB_FILE after create_db() so that the
# hot_tier table exists before _load_from_db() is called.
hot_tier = HotTier(maxlen=3600)
state.hot_tier = hot_tier

_data_sources = [
    SGP30Source(),
    AHT20Source(),
    ParticulateSource(),    # uses module-level read_pm() — no arg needed
    MICS6814Source(),
]

_feature_extractor = FeatureExtractor()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_detection_engine = DetectionEngine(
    rules_path=_PROJECT_ROOT / "config" / "rules.yaml",
    anomaly_config_path=_PROJECT_ROOT / "config" / "anomaly.yaml",
    model_dir=_PROJECT_ROOT / "data" / "anomaly_models",
    fingerprints_path=_PROJECT_ROOT / "config" / "fingerprints.yaml",
    dry_run=True,  # Shadow mode. Set to False once parity confirmed.
)
state.detection_engine = _detection_engine

# ── Smart plug & async event loop ────────────────────────────────────────────

state.fan_smart_plug = KasaSmartPlug(FAN_KASA_SMART_PLUG_IP)

thread_loop = asyncio.new_event_loop()
state.thread_loop = thread_loop


def _start_thread_event_loop():
    asyncio.set_event_loop(thread_loop)
    thread_loop.run_forever()


Thread(target=_start_thread_event_loop, daemon=True).start()


# ── Sensor reading ────────────────────────────────────────────────────────────

# Cache for the last successful PM reading so the fan controller and UI can
# keep using a recent measurement when the UART read intermittently fails.
_last_pm = {"pm1_0": None, "pm2_5": None, "pm10": None, "timestamp": None}


def read_sensors():
    temperature, humidity, eco2, tvoc = 0, 0, 0, 0
    pm1_0, pm2_5, pm10 = None, None, None
    pm_fresh = False  # True when this cycle produced a brand-new reading
    gas_co, gas_no2, gas_nh3 = None, None, None

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

    if pm_sensor:
        try:
            pm_data = read_pm()
            if pm_data:
                pm1_0 = pm_data["pm1_0"]
                pm2_5 = pm_data["pm2_5"]
                pm10 = pm_data["pm10"]
                pm_fresh = True
                _last_pm.update(pm1_0=pm1_0, pm2_5=pm2_5, pm10=pm10,
                                timestamp=datetime.utcnow())
        except Exception as e:
            log.error("Error reading PM sensor: %s", e)

    if state.mics6814:
        try:
            gas_co, gas_no2, gas_nh3 = read_mics6814()
        except Exception as e:
            log.error("Error reading MICS6814 sensor: %s", e)

    # Fall back to the cached reading when the sensor didn't return data,
    # provided it is within the configurable staleness window.
    pm_stale = False
    pm_timestamp = _last_pm["timestamp"]
    if not pm_fresh and pm_timestamp is not None:
        stale_minutes = get_fan_settings().get("pm_stale_minutes", 10.0)
        age = (datetime.utcnow() - pm_timestamp).total_seconds()
        if age <= stale_minutes * 60:
            pm1_0 = _last_pm["pm1_0"]
            pm2_5 = _last_pm["pm2_5"]
            pm10 = _last_pm["pm10"]
            pm_stale = True
            log.debug("Using cached PM reading (%.0fs old)", age)
        else:
            log.debug("Cached PM reading expired (%.0fs > %.0fs limit)",
                      age, stale_minutes * 60)

    return (temperature, humidity, eco2, tvoc, pm1_0, pm2_5, pm10, pm_fresh, pm_stale, pm_timestamp,
            gas_co, gas_no2, gas_nh3)


# ── Background logging ────────────────────────────────────────────────────────

def _collect_health() -> dict:
    """Gather lightweight system health stats for SSE broadcast."""
    status = {
        "AHT20": "OK" if state.aht20 else "UNAVAILABLE",
        "SGP30": "OK" if state.sgp30 else "UNAVAILABLE",
        "PM_sensor": "OK" if state.pm_sensor else "UNAVAILABLE",
        "MICS6814": "OK" if state.mics6814 else "UNAVAILABLE",
    }
    cpu_percent = psutil.cpu_percent(interval=0)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    status["cpu_usage"] = f"{cpu_percent:.1f}%"
    status["memory_used"] = f"{memory.used // (1024 ** 2)} MB"
    status["memory_total"] = f"{memory.total // (1024 ** 2)} MB"
    status["memory_percent"] = f"{memory.percent:.1f}%"
    status["disk_used"] = f"{disk.used // (1024 ** 3):.1f} GB"
    status["disk_total"] = f"{disk.total // (1024 ** 3):.1f} GB"
    status["disk_percent"] = f"{disk.percent:.1f}%"
    db_path = config.get("DB_FILE", "data/sensor_data.db")
    try:
        db_bytes = os.path.getsize(db_path)
        if db_bytes >= 1024 ** 2:
            status["db_size"] = f"{db_bytes / (1024 ** 2):.1f} MB"
        else:
            status["db_size"] = f"{db_bytes / 1024:.1f} KB"
    except OSError:
        status["db_size"] = "Unknown"
    if state.service_start_time:
        service_uptime = datetime.utcnow() - state.service_start_time
        status["service_uptime"] = str(timedelta(seconds=int(service_uptime.total_seconds())))
    else:
        status["service_uptime"] = "Unknown"
    try:
        import subprocess
        uptime_seconds = float(subprocess.check_output(
            ["cat", "/proc/uptime"]).decode().split()[0])
        status["uptime"] = str(timedelta(seconds=int(uptime_seconds)))
    except Exception:
        status["uptime"] = "Unknown"
    try:
        future = asyncio.run_coroutine_threadsafe(
            state.fan_smart_plug.plug.update(), thread_loop
        )
        future.result(timeout=3)
        status["smart_plug"] = "OK"
    except Exception:
        status["smart_plug"] = "UNAVAILABLE"
    return status


def log_data():
    (temp, hum, eco2, tvoc, pm1_0, pm2_5, pm10, pm_fresh, pm_stale, pm_ts,
     gas_co, gas_no2, gas_nh3) = read_sensors()

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
    # Only log fresh PM readings to the database — stale/cached values
    # are already stored from the original read cycle.
    db_pm1  = pm1_0 if pm_fresh else None
    db_pm25 = pm2_5 if pm_fresh else None
    db_pm10 = pm10  if pm_fresh else None
    log_sensor_data(temp, hum, eco2, tvoc, fan_power_w=fan_power_w, vpd_kpa=vpd,
                    pm1_0=db_pm1, pm2_5=db_pm25, pm10=db_pm10,
                    gas_co=gas_co, gas_no2=gas_no2, gas_nh3=gas_nh3)

    # Broadcast sensor reading to SSE subscribers — include staleness
    # metadata so the dashboard can show when PM data is cached.
    if state.event_bus:
        state.event_bus.publish("sensor_update", {
            "temperature": temp, "humidity": hum,
            "eco2": eco2, "tvoc": tvoc,
            "fan_power_w": fan_power_w, "vpd_kpa": vpd,
            "pm1_0": pm1_0, "pm2_5": pm2_5, "pm10": pm10,
            "pm_stale": pm_stale,
            "pm_timestamp": pm_ts.isoformat() + "Z" if pm_ts else None,
            "gas_co": gas_co, "gas_no2": gas_no2, "gas_nh3": gas_nh3,
        })
        state.event_bus.publish("health_update", _collect_health())

    settings = get_fan_settings()
    if settings["enabled"] and state.fan_mode == "auto":
        reading = SensorReading(
            temperature=temp, humidity=hum, eco2=eco2, tvoc=tvoc,
            vpd_kpa=vpd, pm2_5=pm2_5,
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
            # Broadcast fan status change
            if state.event_bus:
                state.event_bus.publish("fan_status", {
                    "state": action, "mode": "auto",
                    "power_w": fan_power_w,
                })
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
        # The two try/except blocks (log_data and feature extraction) are intentionally
        # independent: FeatureVector updates proceed even if log_data fails, because the
        # hot tier is populated by a separate _sensor_read_loop thread.
        if _log_cycle % _CYCLE_60S == 0:
            try:
                baselines = get_24h_baselines()
                hot_snap = state.hot_tier.snapshot() if state.hot_tier else []
                state.feature_vector = _feature_extractor.extract(hot_snap, baselines)
            except Exception as exc:
                log.error("FeatureExtractor error: %s", exc)

        # Run inference engine every ~60s
        if _log_cycle % _CYCLE_60S == 0:
            try:
                from mlss_monitor.inference_engine import run_analysis
                run_analysis()
            except Exception as e:
                log.error("Inference engine error: %s", e)

        # Shadow mode: new DetectionEngine runs alongside run_analysis() for parallel
        # validation. dry_run=True means no DB writes. Compare log output to verify
        # parity, then flip dry_run=False once satisfied.
        if _log_cycle % _CYCLE_60S == 0:
            try:
                if state.feature_vector is not None:
                    fired = _detection_engine.run(state.feature_vector)
                    if fired:
                        log.debug("[shadow] DetectionEngine would fire: %s", fired)
            except Exception as exc:
                log.error("[shadow] DetectionEngine short-term error: %s", exc)

        # Prune hot_tier DB rows older than 60 minutes to cap table size.
        if _log_cycle % _CYCLE_60S == 0:
            try:
                hot_tier.prune_old()
            except Exception as exc:
                log.error("hot_tier.prune_old error: %s", exc)

        # Hourly detectors every ~1h
        if _log_cycle % _CYCLE_1H == 0:
            try:
                from mlss_monitor.inference_engine import run_hourly_analysis
                run_hourly_analysis()
            except Exception as e:
                log.error("Hourly inference error: %s", e)

        if _log_cycle % _CYCLE_1H == 0:
            try:
                if state.feature_vector is not None:
                    _detection_engine.run_hourly(state.feature_vector)
            except Exception as exc:
                log.error("[shadow] DetectionEngine hourly error: %s", exc)

        # Daily detectors every ~24h
        if _log_cycle % _CYCLE_24H == 0:
            try:
                from mlss_monitor.inference_engine import run_daily_analysis
                run_daily_analysis()
            except Exception as e:
                log.error("Daily inference error: %s", e)

        if _log_cycle % _CYCLE_24H == 0:
            try:
                if state.feature_vector is not None:
                    _detection_engine.run_daily(state.feature_vector)
            except Exception as exc:
                log.error("[shadow] DetectionEngine daily error: %s", exc)

        time.sleep(LOG_INTERVAL)


def _sensor_read_loop() -> None:
    """Reads all DataSources every second, merges into one NormalisedReading,
    and pushes to the hot tier. Does not write to DB.
    """
    while True:
        try:
            readings = []
            for source in _data_sources:
                try:
                    readings.append(source.get_latest())
                except Exception as exc:
                    log.warning(
                        "DataSource %s read failed: %s", source.name, exc
                    )
            if readings:
                hot_tier.push(merge_readings(readings))
        except Exception as exc:
            log.error("_sensor_read_loop unexpected error: %s", exc)
        time.sleep(1)


def _weather_log_once():
    """Fetch weather + forecasts and broadcast via SSE.  Extracted from
    the loop so it can be called from tests."""
    loc = get_location()
    if not loc or loc.get("lat") is None:
        return
    w = state.open_meteo.get_current_weather(loc["lat"], loc["lon"])
    log_weather(w["temp"], w["humidity"], w["feels_like"],
                w["wind_speed"], w["weather_code"], w["uv_index"])
    cleanup_old_weather(days=7)
    log.info("Weather logged: %.1f°C, %d%%RH", w["temp"], w["humidity"])
    if state.event_bus:
        state.event_bus.publish("weather_update", w)
        try:
            forecast = state.open_meteo.get_forecast(loc["lat"], loc["lon"])
            state.event_bus.publish("forecast_update", forecast)
        except Exception as e:
            log.error("Forecast fetch error: %s", e)
        try:
            daily = state.open_meteo.get_daily_forecast(loc["lat"], loc["lon"], days=14)
            state.event_bus.publish("daily_forecast_update", daily)
        except Exception as e:
            log.error("Daily forecast fetch error: %s", e)


def _weather_log_loop():
    time.sleep(30)
    while True:
        try:
            _weather_log_once()
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
    # Reinitialise hot_tier now that the DB table is guaranteed to exist.
    global hot_tier
    hot_tier = HotTier(maxlen=3600, db_file=DB_FILE)
    state.hot_tier = hot_tier
    try:
        _detection_engine.bootstrap_from_db(str(DB_FILE))
    except Exception as exc:
        log.warning("DetectionEngine.bootstrap_from_db failed: %s", exc)
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
    Thread(target=_sensor_read_loop, daemon=True).start()

    ssl_ctx = _build_ssl_context()
    port = 5000
    protocol = "https" if ssl_ctx else "http"
    log.info("Starting server on %s://0.0.0.0:%d", protocol, port)
    app.run(host="0.0.0.0", port=port, ssl_context=ssl_ctx)


if __name__ == "__main__":
    main()
