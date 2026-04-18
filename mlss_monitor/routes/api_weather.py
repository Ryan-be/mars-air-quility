"""Weather API routes: current, forecast (hourly/daily via ?resolution), history, geocode."""

from flask import Blueprint, jsonify, request

from database.db_logger import (
    cleanup_old_weather, get_latest_weather, get_location,
    get_weather_history, log_weather,
)
from mlss_monitor import state
from mlss_monitor.routes.api_data import _parse_range

api_weather_bp = Blueprint("api_weather", __name__)


@api_weather_bp.route("/api/weather")
def weather():
    loc = get_location()
    if not loc or loc.get("lat") is None:
        return jsonify({"error": "Location not configured"}), 404

    cached = get_latest_weather(max_age_minutes=90)
    if cached:
        cached["location"] = loc["name"]
        cached["source"] = "Open-Meteo (cached)"
        return jsonify(cached)

    try:
        w = state.open_meteo.get_current_weather(loc["lat"], loc["lon"])
        log_weather(w["temp"], w["humidity"], w["feels_like"],
                    w["wind_speed"], w["weather_code"], w["uv_index"])
        cleanup_old_weather(days=7)
        w["location"] = loc["name"]
        return jsonify(w)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_weather_bp.route("/api/weather/forecast")
def forecast():
    """Weather forecast via Open-Meteo.

    `?resolution=hourly` (default) returns the short-term hourly forecast.
    `?resolution=daily` returns a 14-day daily forecast.
    """
    resolution = request.args.get("resolution", "hourly").lower()
    if resolution not in ("hourly", "daily"):
        return jsonify({"error": "'resolution' must be 'hourly' or 'daily'."}), 400
    loc = get_location()
    if not loc or loc.get("lat") is None:
        return jsonify({"error": "Location not configured"}), 404
    try:
        if resolution == "daily":
            return jsonify(state.open_meteo.get_daily_forecast(loc["lat"], loc["lon"], days=14))
        return jsonify(state.open_meteo.get_forecast(loc["lat"], loc["lon"]))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_weather_bp.route("/api/weather/history")
def weather_history():
    range_param = request.args.get("range", "24h")
    since, _now = _parse_range(range_param)
    return jsonify(get_weather_history(since.isoformat()))


@api_weather_bp.route("/api/geocode")
def geocode():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    try:
        return jsonify(state.open_meteo.geocode(q))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
