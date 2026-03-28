"""Register all route blueprints on the Flask app."""

from .auth import auth_bp
from .pages import pages_bp
from .api_data import api_data_bp
from .api_fan import api_fan_bp
from .api_weather import api_weather_bp
from .api_settings import api_settings_bp
from .api_users import api_users_bp
from .system import system_bp
from .api_inferences import api_inferences_bp
from .api_stream import api_stream_bp


def register_routes(app):
    app.register_blueprint(auth_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_data_bp)
    app.register_blueprint(api_fan_bp)
    app.register_blueprint(api_weather_bp)
    app.register_blueprint(api_settings_bp)
    app.register_blueprint(api_users_bp)
    app.register_blueprint(system_bp)
    app.register_blueprint(api_inferences_bp)
    app.register_blueprint(api_stream_bp)
