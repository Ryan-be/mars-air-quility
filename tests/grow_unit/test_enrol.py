"""enroll_unit: POST /api/grow/enroll, returns (unit_id, token)."""
from unittest.mock import MagicMock
from mlss_grow.enrol import enroll_unit, EnrollmentError
from mlss_grow.config import FirstbootConfig
import pytest


def _cfg():
    return FirstbootConfig(
        mlss_host="mlss.local", enrollment_key="key123",
        plant_name="Tomato", plant_type="tomato", medium="soil",
    )


def test_enroll_success(monkeypatch):
    fake_response = MagicMock()
    fake_response.status_code = 201
    fake_response.json = lambda: {"unit_id": 7, "token": "t-secret"}

    fake_post = MagicMock(return_value=fake_response)
    monkeypatch.setattr("mlss_grow.enrol.requests.post", fake_post)

    unit_id, token = enroll_unit(_cfg(), hardware_serial="hw-1")
    assert unit_id == 7
    assert token == "t-secret"

    call = fake_post.call_args
    assert call.kwargs["json"]["enrollment_key"] == "key123"
    assert call.kwargs["json"]["hardware_serial"] == "hw-1"
    assert call.kwargs["json"]["plant"]["name"] == "Tomato"
    assert call.kwargs["json"]["plant"]["type"] == "tomato"
    # url uses https + standard MLSS port
    assert "https://mlss.local:5000/api/grow/enroll" in call.args[0]


def test_enroll_401_raises(monkeypatch):
    fake_response = MagicMock(status_code=401, text="invalid_enrollment_key")
    monkeypatch.setattr("mlss_grow.enrol.requests.post",
                        MagicMock(return_value=fake_response))
    with pytest.raises(EnrollmentError, match="401"):
        enroll_unit(_cfg(), hardware_serial="hw-1")


def test_enroll_network_error_raises(monkeypatch):
    import requests
    monkeypatch.setattr("mlss_grow.enrol.requests.post",
                        MagicMock(side_effect=requests.ConnectionError("no network")))
    with pytest.raises(EnrollmentError, match="network"):
        enroll_unit(_cfg(), hardware_serial="hw-1")


def test_get_hardware_serial_reads_proc_cpuinfo(monkeypatch, tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text(
        "processor\t: 0\nmodel\t: ARMv6\n"
        "Serial\t\t: 100000000c0a8014b\n"
        "Model\t\t: Raspberry Pi Zero W\n"
    )
    monkeypatch.setattr("mlss_grow.enrol._CPUINFO_PATH", str(cpuinfo))
    from mlss_grow.enrol import get_hardware_serial
    assert get_hardware_serial() == "100000000c0a8014b"
