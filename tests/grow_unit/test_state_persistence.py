"""Phase 3 Task 7: persist PID state across firmware service restarts.

The persistence module is intentionally tiny — load + save + a small
PersistedState dataclass. These tests pin three properties:

  1. load_state never raises — missing file, corrupt JSON, wrong shape
     all return defaults (we'd rather boot with a fresh integral than
     wedge the firmware on a parse error).
  2. save_state is best-effort — write failures (read-only target,
     missing parent directory) log a warning and return without raising.
     The PID loop must keep ticking even if /var/lib/mlss-grow/ is
     unwritable.
  3. The on-disk format is stable enough for round-trip — what we save
     today, we can load tomorrow.
"""
import json
import logging
import os
from unittest.mock import patch

import pytest

from mlss_grow.state_persistence import PersistedState, load_state, save_state


def test_load_returns_default_when_file_missing(tmp_path):
    """Missing file is the boot-from-blank case. Must not raise; must
    return a PersistedState with all defaults so the firmware boots
    with a clean integral."""
    nonexistent = tmp_path / "no_such_file.json"
    state = load_state(str(nonexistent))
    assert state == PersistedState()
    assert state.error_integral == 0.0
    assert state.last_error == 0.0
    assert state.last_pulse_at_iso is None


def test_save_then_load_round_trip(tmp_path):
    """The on-disk format must round-trip — what we save today, we
    can load tomorrow. Pins the JSON schema so a future field rename
    breaks loudly here."""
    target = tmp_path / "state.json"
    original = PersistedState(
        error_integral=42.5,
        last_error=-3.25,
        last_pulse_at_iso="2026-05-06T12:34:56",
    )
    save_state(original, str(target))
    loaded = load_state(str(target))
    assert loaded == original


def test_load_returns_default_when_file_is_corrupt_json(tmp_path):
    """Corrupt JSON (truncated write, SD-card bit flip) must not crash
    the firmware boot path. Log a warning, return defaults."""
    target = tmp_path / "state.json"
    target.write_text("{not json at all")
    state = load_state(str(target))
    assert state == PersistedState()


def test_load_returns_default_when_file_has_wrong_shape(tmp_path):
    """A parseable JSON file with the wrong structure (e.g. someone
    overwrote it manually with `{}` or a list) must still boot cleanly.
    Missing keys fall back to defaults via .get()."""
    target = tmp_path / "state.json"
    target.write_text(json.dumps({"foo": "bar"}))
    state = load_state(str(target))
    assert state == PersistedState()


def test_load_returns_default_when_file_is_a_json_list(tmp_path):
    """A JSON list (not a dict) is unparseable for our shape. Must not
    raise — we explicitly check isinstance(data, dict)."""
    target = tmp_path / "state.json"
    target.write_text(json.dumps([1, 2, 3]))
    state = load_state(str(target))
    assert state == PersistedState()


def test_save_creates_parent_directory_if_missing(tmp_path):
    """First boot on a fresh Pi: /var/lib/mlss-grow/ doesn't exist yet.
    save_state must create the parent dir rather than fail."""
    nested = tmp_path / "deep" / "down" / "state.json"
    assert not nested.parent.exists()
    save_state(PersistedState(error_integral=1.0), str(nested))
    assert nested.exists()
    loaded = load_state(str(nested))
    assert loaded.error_integral == 1.0


def test_save_atomic_via_tmp_file_rename(tmp_path):
    """The write is atomic: we write to a .tmp file then os.replace it
    onto the target. A power cut mid-write can't corrupt the target —
    worst case the .tmp is left orphaned."""
    target = tmp_path / "state.json"
    with patch(
        "mlss_grow.state_persistence.os.replace", wraps=os.replace,
    ) as replace_spy:
        save_state(PersistedState(error_integral=7.0), str(target))
    assert replace_spy.called
    src, dst = replace_spy.call_args[0]
    assert src == str(target) + ".tmp"
    assert dst == str(target)


def test_save_failure_does_not_raise(tmp_path, caplog):
    """Best-effort persistence: if the write fails (read-only volume,
    permission denied, disk full), save_state must NOT propagate the
    exception into the PID loop. The loop has to keep running even
    when the filesystem is angry."""
    # Pre-create the target as a directory — open() for write will fail.
    target = tmp_path / "state.json"
    target.mkdir()
    with caplog.at_level(logging.WARNING, logger="mlss_grow.state_persistence"):
        save_state(PersistedState(error_integral=1.0), str(target))
    # Did not raise → if we got here, the contract held.


def test_save_failure_logs_warning(tmp_path, caplog):
    """The write-failure path must surface a warning to the log so the
    operator can diagnose the problem (full SD card, wrong perms).
    Silent failure would hide the bug."""
    target = tmp_path / "state.json"
    target.mkdir()  # makes open() for write fail with IsADirectoryError
    with caplog.at_level(logging.WARNING, logger="mlss_grow.state_persistence"):
        save_state(PersistedState(error_integral=1.0), str(target))
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "expected a warning when save fails"
    msg = " ".join(r.getMessage() for r in warnings)
    assert "failed to persist" in msg.lower()
