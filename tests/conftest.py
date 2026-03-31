import sys
from unittest.mock import MagicMock

# Stub hardware libs before any app code is imported
_hw_mocks = [
    "board", "busio",
    "adafruit_ahtx0", "adafruit_sgp30",
    "pimoroni_mics6814",
    "authlib", "authlib.integrations", "authlib.integrations.flask_client",
]
for _mod in _hw_mocks:
    sys.modules[_mod] = MagicMock()

import pytest
import database.init_db as dbi
import database.db_logger as dbl
import database.user_db as udb


def fake_sensors(temp=22.0, hum=50.0, eco2=400, tvoc=100):
    """Return a read_sensors tuple with no PM or gas data (all None/False)."""
    return (temp, hum, eco2, tvoc, None, None, None, False, False, None, None, None, None)


def _patch_db(path: str):
    dbi.DB_FILE = path
    dbl.DB_FILE = path
    udb.DB_FILE = path


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
        # Default to admin session so existing tests are unaffected by RBAC guards
        with client.session_transaction() as sess:  # pylint: disable=contextmanager-generator-missing-cleanup
            sess["logged_in"] = True
            sess["user"] = "test-admin"
            sess["user_role"] = "admin"
            sess["user_id"] = None
        yield client, mock_plug
