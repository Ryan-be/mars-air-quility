import sys
from unittest.mock import MagicMock

# Stub hardware libs before any app code is imported
_hw_mocks = [
    "board", "busio",
    "adafruit_ahtx0", "adafruit_sgp30", "adafruit_bmp280",
    "mics6814",
    "authlib", "authlib.integrations", "authlib.integrations.flask_client",
]

# Stub river (online ML lib) on platforms where its compiled DLL fails to load
# (Windows path-too-long is the recurring offender). The anomaly_detector module
# pulls it in transitively via the routes package, breaking unrelated tests.
try:  # pragma: no cover - environmental
    import river.anomaly  # noqa: F401  pylint: disable=unused-import
except Exception:  # pylint: disable=broad-except
    _hw_mocks.append("river")
    _hw_mocks.append("river.anomaly")
for _mod in _hw_mocks:
    sys.modules[_mod] = MagicMock()

import pytest  # noqa: E402
import database.init_db as dbi  # noqa: E402
import database.db_logger as dbl  # noqa: E402
import database.user_db as udb  # noqa: E402


# ── CSRF: default same-origin Origin on every Flask test client ──────────────
#
# `mlss_monitor.app.check_csrf` rejects state-changing requests (POST/PUT/
# PATCH/DELETE) without a same-origin Origin or Referer. Flask's test client
# does not set Origin automatically, so without intervention every existing
# state-changing test would 403.
#
# Many test files build their own `app.test_client()` rather than using the
# `app_client` fixture below, so a fixture-only fix wouldn't reach them.
# Patch the Werkzeug FlaskClient class once at conftest import time: every
# instance constructed during the test run inherits a default Origin in
# environ_base. Tests can still override per-request via
# `client.post(..., headers={"Origin": "http://evil.com"})` to exercise the
# rejection path. Flask honours `PREFERRED_URL_SCHEME` when building request
# URLs in test mode, so the matching Origin is `<scheme>://localhost`.
def _install_default_test_client_origin():
    from flask.testing import FlaskClient

    _orig_init = FlaskClient.__init__

    def _patched_init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        try:
            scheme = self.application.config.get(
                "PREFERRED_URL_SCHEME", "http",
            )
        except Exception:  # pylint: disable=broad-except
            scheme = "http"
        # Don't clobber an already-set Origin, in case a fixture/test set one
        # before constructing further state.
        self.environ_base.setdefault("HTTP_ORIGIN", f"{scheme}://localhost")

    FlaskClient.__init__ = _patched_init


_install_default_test_client_origin()


def fake_sensors(temp=22.0, hum=50.0, eco2=400, tvoc=100):
    """Return a read_sensors tuple with no PM or gas data (all None/False)."""
    return (temp, hum, eco2, tvoc, None, None, None, False, False, None, None, None, None)


def _patch_db(path: str):
    import mlss_monitor.hot_tier as ht_mod
    dbi.DB_FILE = path
    dbl.DB_FILE = path
    udb.DB_FILE = path
    ht_mod.DB_FILE = path
    # The @tee_to_outbox decorator (mlss_monitor.backup.outbox) opens its
    # own connection at call-time by reading database.db_logger.DB_FILE —
    # patched above — so decorated writers (log_sensor_data, save_inference,
    # log_weather, add/remove/edit_annotation, add_inference_tag) now hit
    # the test DB rather than the production data/sensor_data.db.


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    _patch_db(db_path)
    dbi.create_db()
    yield db_path
    _patch_db("data/sensor_data.db")  # restore after test


@pytest.fixture
def app_client(db, monkeypatch):  # pylint: disable=redefined-outer-name
    """Flask test client with hardware and smart plug stubbed out."""
    import mlss_monitor.app as app_module
    import mlss_monitor.state as app_state

    # Prevent the background logging thread from starting
    monkeypatch.setattr(app_module, "LOG_INTERVAL", 99999)

    # Stub the smart plug so no real network calls happen
    mock_plug = MagicMock()
    monkeypatch.setattr(app_state, "fan_smart_plug", mock_plug)

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        # Note: a default same-origin Origin header is already installed on
        # every FlaskClient by `_install_default_test_client_origin` above
        # (see module top), so state-changing requests reach handlers
        # instead of being 403'd by the CSRF middleware.
        # Default to admin session so existing tests are unaffected by RBAC guards
        with client.session_transaction() as sess:  # pylint: disable=contextmanager-generator-missing-cleanup
            sess["logged_in"] = True
            sess["user"] = "test-admin"
            sess["user_role"] = "admin"
            sess["user_id"] = None
        yield client, mock_plug
