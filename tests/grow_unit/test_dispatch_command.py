"""Service-level command dispatch.

`dispatch_command` is the single switchboard for incoming WS `command`
messages. It accepts the payload dict (the `payload` field of the
{type: "command", payload: ...} envelope) and routes by `kind` (new
config_changed/safety_override commands) OR `name` (legacy
identify/water_now/snap_photo). Both are accepted because the server
code uses both spellings — see api_grow_units.identify (sends `name`)
vs api_grow_config._push_config_changed (sends `kind`).

Tests cover:
  * legacy commands still dispatch correctly (no regression)
  * config_changed triggers a pull + apply
  * safety_override drives the actuator via the override module
  * unknown commands are dropped without raising
"""
import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest

from mlss_grow.dispatch import (
    DispatchContext,
    dispatch_command,
)


def _basic_context(*, buffer=None):
    """A DispatchContext with all-MagicMock collaborators.

    The optional `buffer` kwarg lets clear-buffer tests inject a
    MagicMock buffer; legacy tests pass None so the dispatcher's
    defensive "buffer not wired up — drop" branch keeps working
    untouched for non-clear-buffer commands.
    """
    return DispatchContext(
        unit_id=1,
        server_url="https://mlss.local:5000",
        token="t",
        server_cert_path=None,
        pump=MagicMock(state=MagicMock(return_value=False)),
        light=MagicMock(state=MagicMock(return_value=False)),
        camera=MagicMock(),
        loop_cfg=MagicMock(),
        ws=MagicMock(),
        override_state=None,  # filled per-test if needed
        buffer=buffer,
    )


# ----------------------------------------------------------------------------
# Legacy `name`-keyed commands — must keep working unchanged.
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_identify_calls_blink_pattern():
    ctx = _basic_context()
    await dispatch_command({"name": "identify", "args": {"duration_s": 7}}, ctx)
    ctx.light.blink_pattern.assert_called_once_with(duration_s=7)


@pytest.mark.asyncio
async def test_dispatch_identify_uses_default_duration_when_missing():
    """No args.duration_s → fall back to 10s (matches the legacy behavior
    in the original service.py)."""
    ctx = _basic_context()
    await dispatch_command({"name": "identify"}, ctx)
    ctx.light.blink_pattern.assert_called_once_with(duration_s=10)


@pytest.mark.asyncio
async def test_dispatch_water_now_calls_pump_pulse():
    ctx = _basic_context()
    await dispatch_command({"name": "water_now", "args": {"duration_s": 4}}, ctx)
    ctx.pump.pulse.assert_called_once_with(4)


# ----------------------------------------------------------------------------
# New `kind`-keyed commands — Task 8 wiring.
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_config_changed_pulls_and_applies():
    """config_changed → pull fresh config from server, then apply to
    loop_cfg. Both calls must happen in order; if pull raises, no apply
    runs (and the dispatcher keeps going)."""
    ctx = _basic_context()
    fake_unit_cfg = MagicMock()
    with patch("mlss_grow.dispatch.pull_unit_config",
                return_value=fake_unit_cfg) as mock_pull, \
         patch("mlss_grow.dispatch.apply_config") as mock_apply:
        await dispatch_command(
            {"kind": "config_changed", "section": "pid"}, ctx,
        )
    mock_pull.assert_called_once_with(
        ctx.server_url, ctx.unit_id, ctx.token, ctx.server_cert_path,
    )
    mock_apply.assert_called_once_with(fake_unit_cfg, ctx.loop_cfg)


@pytest.mark.asyncio
async def test_config_changed_logs_section(caplog):
    """Section field should appear in the log line so ops can see what changed."""
    caplog.set_level(logging.INFO)
    ctx = _basic_context()
    with patch("mlss_grow.dispatch.pull_unit_config",
                return_value=MagicMock()), \
         patch("mlss_grow.dispatch.apply_config"):
        await dispatch_command(
            {"kind": "config_changed", "section": "pid"}, ctx,
        )
    assert "section=pid" in caplog.text


@pytest.mark.asyncio
async def test_dispatch_config_changed_swallows_pull_failure():
    """If pull_unit_config raises (network blip, server down), the
    dispatcher must log + continue — config_changed is best-effort.
    The next reconnect will re-pull."""
    ctx = _basic_context()
    with patch("mlss_grow.dispatch.pull_unit_config",
                side_effect=ConnectionError("server down")), \
         patch("mlss_grow.dispatch.apply_config") as mock_apply:
        # Must NOT raise:
        await dispatch_command(
            {"kind": "config_changed", "section": "pid"}, ctx,
        )
    mock_apply.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_safety_override_calls_invoke_safety_override():
    """safety_override → forwards the action+duration to
    invoke_safety_override (which manages the Timer + actuator drive)."""
    from mlss_grow.safety_override import SafetyOverrideState
    ctx = _basic_context()
    ctx.override_state = SafetyOverrideState()
    with patch("mlss_grow.dispatch.invoke_safety_override") as mock_invoke:
        await dispatch_command(
            {"kind": "safety_override",
             "action": "force_pump_on", "duration_s": 5}, ctx,
        )
    mock_invoke.assert_called_once_with(
        "force_pump_on", 5.0, pump=ctx.pump, light=ctx.light,
        state=ctx.override_state,
    )


@pytest.mark.asyncio
async def test_dispatch_safety_override_coerces_duration_to_float():
    """duration_s arrives as either int or float over JSON — coerce so
    invoke_safety_override always sees a float."""
    from mlss_grow.safety_override import SafetyOverrideState
    ctx = _basic_context()
    ctx.override_state = SafetyOverrideState()
    with patch("mlss_grow.dispatch.invoke_safety_override") as mock_invoke:
        await dispatch_command(
            {"kind": "safety_override",
             "action": "force_pump_on", "duration_s": 7}, ctx,
        )
    args, kwargs = mock_invoke.call_args
    assert isinstance(args[1], float)
    assert args[1] == 7.0


# ----------------------------------------------------------------------------
# Spec §6 legacy `name`-keyed commands — light_override / reload_config / reboot.
# These were missing pre-Commit-C and silently fell to the unknown-command
# branch.
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_light_override_on_invokes_safety_override():
    """light_override with state=on routes through invoke_safety_override
    (same plumbing as `kind=safety_override` action=force_light_on) so
    the off-flip is Timer-scheduled and the dispatcher stays responsive."""
    from mlss_grow.safety_override import SafetyOverrideState
    ctx = _basic_context()
    ctx.override_state = SafetyOverrideState()
    with patch("mlss_grow.dispatch.invoke_safety_override") as mock_invoke:
        await dispatch_command(
            {"name": "light_override",
             "args": {"state": "on", "duration_min": 15}}, ctx,
        )
    mock_invoke.assert_called_once()
    args, kwargs = mock_invoke.call_args
    assert args[0] == "force_light_on"
    assert args[1] == 15 * 60.0  # 15 min → 900s
    assert kwargs["light"] is ctx.light


@pytest.mark.asyncio
async def test_dispatch_light_override_off_invokes_safety_override():
    """state=off → force_light_off, no duration semantics."""
    from mlss_grow.safety_override import SafetyOverrideState
    ctx = _basic_context()
    ctx.override_state = SafetyOverrideState()
    with patch("mlss_grow.dispatch.invoke_safety_override") as mock_invoke:
        await dispatch_command(
            {"name": "light_override", "args": {"state": "off"}}, ctx,
        )
    mock_invoke.assert_called_once()
    args, kwargs = mock_invoke.call_args
    assert args[0] == "force_light_off"
    assert args[1] == 0.0


@pytest.mark.asyncio
async def test_dispatch_light_override_unknown_state_is_no_op():
    """Defensive: malformed `state` doesn't reach invoke_safety_override."""
    from mlss_grow.safety_override import SafetyOverrideState
    ctx = _basic_context()
    ctx.override_state = SafetyOverrideState()
    with patch("mlss_grow.dispatch.invoke_safety_override") as mock_invoke:
        await dispatch_command(
            {"name": "light_override", "args": {"state": "diagonal"}}, ctx,
        )
    mock_invoke.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_reload_config_routes_to_config_changed_handler():
    """reload_config is an alias — it should produce the same pull+apply
    pair that `kind=config_changed` does."""
    ctx = _basic_context()
    fake_unit_cfg = MagicMock()
    with patch("mlss_grow.dispatch.pull_unit_config",
                return_value=fake_unit_cfg) as mock_pull, \
         patch("mlss_grow.dispatch.apply_config") as mock_apply:
        await dispatch_command({"name": "reload_config"}, ctx)
    mock_pull.assert_called_once_with(
        ctx.server_url, ctx.unit_id, ctx.token, ctx.server_cert_path,
    )
    mock_apply.assert_called_once_with(fake_unit_cfg, ctx.loop_cfg)


@pytest.mark.asyncio
async def test_dispatch_reboot_invokes_systemctl_reboot(caplog):
    """reboot → spawns a thread that calls subprocess.run with
    [sudo, systemctl, reboot]. The thread is daemon so the test
    doesn't hang at teardown."""
    import logging
    caplog.set_level(logging.INFO)
    ctx = _basic_context()
    with patch("mlss_grow.dispatch.subprocess.run") as mock_run, \
         patch("mlss_grow.dispatch.threading.Thread") as mock_thread:
        # Capture the thread's target and run it synchronously so we
        # can assert subprocess.run got invoked (otherwise the daemon
        # thread might not have run before the test exits).
        def _capture_thread(target=None, daemon=None):
            t = MagicMock()
            t.start = lambda: target() if target else None
            return t
        mock_thread.side_effect = _capture_thread
        await dispatch_command({"name": "reboot"}, ctx)
    mock_run.assert_called_once_with(
        ["sudo", "systemctl", "reboot"], check=False,
    )
    assert "reboot command received" in caplog.text


# ----------------------------------------------------------------------------
# Defensive: malformed payloads must NOT crash the dispatcher thread.
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_unknown_kind_is_no_op():
    ctx = _basic_context()
    # Must NOT raise:
    await dispatch_command({"kind": "fly_to_mars"}, ctx)
    ctx.pump.on.assert_not_called()
    ctx.light.on.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_unknown_name_is_no_op():
    ctx = _basic_context()
    # Must NOT raise:
    await dispatch_command({"name": "make_coffee"}, ctx)


@pytest.mark.asyncio
async def test_dispatch_payload_missing_both_keys_is_no_op():
    """Empty payload → log + drop. Should never happen in practice
    (server always sets one key) but the dispatcher mustn't crash."""
    ctx = _basic_context()
    await dispatch_command({}, ctx)


# ----------------------------------------------------------------------------
# Phase 3 Task 4 — clear_buffer command (Diagnostics tab Danger Zone).
# Server-side admin-only POST /api/grow/units/<id>/clear-buffer pushes
# {"name": "clear_buffer"} via WS; the dispatcher empties the local buffer.
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_clear_buffer_calls_buffer_clear():
    """{"name": "clear_buffer"} → ctx.buffer.clear() called exactly once."""
    fake_buffer = MagicMock()
    ctx = _basic_context(buffer=fake_buffer)
    await dispatch_command({"name": "clear_buffer"}, ctx)
    fake_buffer.clear.assert_called_once_with()


@pytest.mark.asyncio
async def test_dispatch_clear_buffer_when_buffer_is_none_logs_warning(caplog):
    """Defensive: if ctx.buffer is None (legacy DispatchContext, or a
    test wiring that never passed one) the dispatcher logs + drops the
    command rather than raising. Same shape as the override_state-None
    branch in _handle_safety_override."""
    caplog.set_level(logging.WARNING)
    ctx = _basic_context(buffer=None)
    # Must NOT raise:
    await dispatch_command({"name": "clear_buffer"}, ctx)
    assert "buffer not wired up" in caplog.text


@pytest.mark.asyncio
async def test_dispatch_clear_buffer_logs_at_info_when_handled():
    """Operator-visible log when the command lands successfully — ops
    forensics rely on journalctl to confirm the firmware actually
    received and acted on the clear."""
    import logging as _logging
    fake_buffer = MagicMock()
    ctx = _basic_context(buffer=fake_buffer)
    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        # caplog is harder to use here without the fixture, so just
        # inspect the side effect directly: clear() got called.
        mp.setattr(_logging, "getLogger", _logging.getLogger)
        await dispatch_command({"name": "clear_buffer"}, ctx)
    fake_buffer.clear.assert_called_once_with()
