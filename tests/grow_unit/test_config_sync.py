"""Pull-on-`config_changed`: firmware fetches the latest unit config from
the server's bearer-authenticated GET /api/grow/units/<id>/config endpoint
and parses it into a UnitConfig dataclass.

The server resolves null overrides against grow_plant_profiles before
responding, so the firmware sees concrete numbers across the board.

These tests cover the network layer only (URL, auth header, TLS verify
behaviour, error surfacing). The mutation-of-running-state half is
covered in test_config_sync_apply.py.
"""
import pytest
import requests
from unittest.mock import MagicMock, patch

from mlss_grow.config_sync import UnitConfig, pull_unit_config


def _ok_response(payload):
    """Build a MagicMock that quacks like a successful requests.Response."""
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status = MagicMock()
    r.json = MagicMock(return_value=payload)
    return r


def _err_response(status, text="error"):
    r = MagicMock()
    r.status_code = status
    err = requests.HTTPError(f"{status} error")
    err.response = r
    r.raise_for_status = MagicMock(side_effect=err)
    r.json = MagicMock(return_value={"error": text})
    return r


_FULL_PAYLOAD = {
    "overrides": {
        "watering_target": 55, "kp": 0.4, "ki": 0, "kd": 0,
        "soak_window_min": 30, "min_pulse_s": 2, "max_pulse_s": 8,
    },
    "calibration": {"dry_raw": 220, "wet_raw": 1600},
    "light_windows": {
        "vegetative": [{"start": "06:00", "end": "22:00"}],
    },
    "current_phase": "vegetative",
    "plant_type": "tomato",
}


def test_pull_unit_config_constructs_correct_url():
    """The URL is `<server_url>/api/grow/units/<unit_id>/config` —
    callers pass the host base URL, not a hand-rolled path."""
    with patch("mlss_grow.config_sync.requests.get") as mock_get:
        mock_get.return_value = _ok_response(_FULL_PAYLOAD)
        pull_unit_config(
            server_url="https://mlss.local:5000",
            unit_id=42,
            token="t",
            server_cert_path=None,
        )
        url = mock_get.call_args[0][0]
        assert url == "https://mlss.local:5000/api/grow/units/42/config"


def test_pull_unit_config_uses_bearer_token():
    with patch("mlss_grow.config_sync.requests.get") as mock_get:
        mock_get.return_value = _ok_response(_FULL_PAYLOAD)
        pull_unit_config(
            server_url="https://mlss.local:5000",
            unit_id=1,
            token="my-secret-token",
            server_cert_path=None,
        )
        headers = mock_get.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer my-secret-token"


def test_pull_unit_config_uses_pinned_cert_when_available(tmp_path):
    """When the cert file exists on disk, pass it as `verify=<path>` so
    requests does CA-pinned verification. Same posture as enrol.py."""
    cert = tmp_path / "server.crt"
    cert.write_text("FAKE CERT")
    with patch("mlss_grow.config_sync.requests.get") as mock_get:
        mock_get.return_value = _ok_response(_FULL_PAYLOAD)
        pull_unit_config(
            server_url="https://mlss.local:5000",
            unit_id=1,
            token="t",
            server_cert_path=str(cert),
        )
        verify = mock_get.call_args.kwargs["verify"]
        assert verify == str(cert)


def test_pull_unit_config_falls_back_to_no_verify_when_cert_missing():
    """Cert path provided but file doesn't exist (dev/test, pre-install) —
    fall back to verify=False rather than crashing. Mirrors the enrol.py
    posture so the dev workflow is consistent."""
    with patch("mlss_grow.config_sync.requests.get") as mock_get:
        mock_get.return_value = _ok_response(_FULL_PAYLOAD)
        pull_unit_config(
            server_url="https://mlss.local:5000",
            unit_id=1,
            token="t",
            server_cert_path="/nonexistent/path/server.crt",
        )
        verify = mock_get.call_args.kwargs["verify"]
        assert verify is False


def test_pull_unit_config_returns_unit_config_dataclass_with_all_fields():
    with patch("mlss_grow.config_sync.requests.get") as mock_get:
        mock_get.return_value = _ok_response(_FULL_PAYLOAD)
        cfg = pull_unit_config(
            server_url="https://mlss.local:5000",
            unit_id=1,
            token="t",
            server_cert_path=None,
        )
        assert isinstance(cfg, UnitConfig)
        assert cfg.overrides == _FULL_PAYLOAD["overrides"]
        assert cfg.calibration == {"dry_raw": 220, "wet_raw": 1600}
        assert cfg.light_windows == {"vegetative": [{"start": "06:00", "end": "22:00"}]}
        assert cfg.current_phase == "vegetative"
        assert cfg.plant_type == "tomato"


def test_pull_unit_config_raises_on_4xx():
    """A 4xx (e.g. 401 invalid token) bubbles up as an HTTPError so the
    caller can decide how to react — the dispatcher logs and continues
    without applying anything (config_changed is best-effort on the
    firmware side too)."""
    with patch("mlss_grow.config_sync.requests.get") as mock_get:
        mock_get.return_value = _err_response(401)
        with pytest.raises(requests.HTTPError):
            pull_unit_config(
                server_url="https://mlss.local:5000",
                unit_id=1,
                token="wrong",
                server_cert_path=None,
            )


def test_pull_unit_config_raises_on_5xx():
    """Same posture for 5xx — caller surfaces as an exception."""
    with patch("mlss_grow.config_sync.requests.get") as mock_get:
        mock_get.return_value = _err_response(500)
        with pytest.raises(requests.HTTPError):
            pull_unit_config(
                server_url="https://mlss.local:5000",
                unit_id=1,
                token="t",
                server_cert_path=None,
            )


def test_pull_unit_config_raises_on_malformed_response():
    """If a required key (current_phase / plant_type) is missing, raise
    KeyError rather than silently returning a UnitConfig with None fields."""
    with patch("mlss_grow.config_sync.requests.get") as mock_get:
        # Drop current_phase and plant_type — both required.
        bad_payload = {
            "overrides": {}, "calibration": {}, "light_windows": {},
        }
        mock_get.return_value = _ok_response(bad_payload)
        with pytest.raises((KeyError, ValueError)):
            pull_unit_config(
                server_url="https://mlss.local:5000",
                unit_id=1,
                token="t",
                server_cert_path=None,
            )


def test_pull_unit_config_passes_timeout():
    """Caller can configure timeout — defaults to a small number so a
    hung server doesn't wedge the dispatcher thread."""
    with patch("mlss_grow.config_sync.requests.get") as mock_get:
        mock_get.return_value = _ok_response(_FULL_PAYLOAD)
        pull_unit_config(
            server_url="https://mlss.local:5000",
            unit_id=1,
            token="t",
            server_cert_path=None,
            timeout=2.5,
        )
        assert mock_get.call_args.kwargs["timeout"] == 2.5
