"""Service entrypoint orchestrates: load config → enrol if needed → boot WS + safety loop."""
import json
from unittest.mock import MagicMock, patch
from mlss_grow.service import bootstrap_unit_state, BootstrappedState
from mlss_grow.config import FirstbootConfig
import pytest


def test_bootstrap_uses_existing_token_if_present(tmp_path, monkeypatch):
    token_path = str(tmp_path / "grow.token")
    boot_path = str(tmp_path / "mlss-grow.yaml")
    # Pre-existing token
    from mlss_grow.config import save_token
    save_token(token_path, unit_id=42, token="existing-token")

    # Boot YAML present (would normally trigger enroll) but token already exists
    open(boot_path, "w").write(
        "mlss_host: mlss.local\nenrollment_key: x\nplant:\n  name: X\n"
    )

    state = bootstrap_unit_state(
        firstboot_path=boot_path,
        token_path=token_path,
        enroll_fn=MagicMock(side_effect=AssertionError("should not enroll")),
        get_serial_fn=MagicMock(return_value="hw-1"),
    )
    assert state.unit_id == 42
    assert state.token == "existing-token"
    assert state.mlss_host == "mlss.local"


def test_bootstrap_enrolls_when_no_token(tmp_path, monkeypatch):
    token_path = str(tmp_path / "grow.token")
    boot_path = str(tmp_path / "mlss-grow.yaml")
    open(boot_path, "w").write(
        "mlss_host: mlss.local\nenrollment_key: ek\nplant:\n  name: Test\n"
    )

    state = bootstrap_unit_state(
        firstboot_path=boot_path,
        token_path=token_path,
        enroll_fn=MagicMock(return_value=(99, "freshly-minted")),
        get_serial_fn=MagicMock(return_value="hw-1"),
    )
    assert state.unit_id == 99
    assert state.token == "freshly-minted"

    # Token persisted
    from mlss_grow.config import load_token
    assert load_token(token_path) == (99, "freshly-minted")

    # YAML file removed (don't leave enrollment key on SD card)
    import os
    assert not os.path.exists(boot_path)


def test_bootstrap_raises_when_no_token_and_no_firstboot(tmp_path):
    with pytest.raises(RuntimeError, match="no firstboot config"):
        bootstrap_unit_state(
            firstboot_path=str(tmp_path / "absent.yaml"),
            token_path=str(tmp_path / "absent.token"),
            enroll_fn=MagicMock(),
            get_serial_fn=MagicMock(return_value="hw-1"),
        )
