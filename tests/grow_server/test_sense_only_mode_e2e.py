"""E2E stack test for sense-only mode (capability health field).

Phase 2 finisher Task 5. Proves the full data flow Task 1 wired up across
all 4 packages and the C1 schema cleanup that promoted ``health`` to a
typed column:

  * firmware emits capabilities with health values per init outcome
  * server persists them to the typed column
  * server promotes health when telemetry shows actuator working
  * server promotes health when a watering_event lands (recovery path)
  * server's lazy watchdog flips to "unresponsive" on
    command-without-event past the timeout
  * GET /api/grow/units/<id> surfaces health for the frontend to grey
    out actuator buttons gracefully

This is cross-package integration coverage that the per-handler unit
tests in ``test_handler_capabilities.py`` /
``test_handler_telemetry.py`` / ``test_handler_event.py`` /
``test_grow_units_api.py`` miss: those tests stand up bare ``Flask()``
apps with a single blueprint registered, talk to the handlers in
isolation, or stub the watchdog. Here we route through the production
blueprint registration, the OAuth-on auth gate, the real handler
modules, the real watchdog, and a single SQLite file. If any of those
layers regresses, this file fails.

Unlike ``test_configure_e2e.py`` there is no WS listener — the only
outbound thing in this scenario is telemetry FROM the firmware (which we
inject by calling the handler module directly the same way the real WS
listener would). No commands need to flow back TO firmware to exercise
the health field, so the fixture stays correspondingly simple.

Test order independence: each test gets its own fixture instance with a
fresh tmp DB. The watchdog's process-global ``_last_command_at`` dict is
cleared per-test via the fixture teardown so a stale entry from a prior
test can't leak into the next.
"""
import sqlite3
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Shared fixture: real Flask app + admin session + tmp DB + helper to
# inject WS-style messages by calling the handler module directly.
#
# Why call handlers directly instead of opening a real WS connection?
#  * the WS listener simply validates payloads via pydantic and dispatches
#    by ``type`` to ``handle_capabilities`` / ``handle_telemetry`` /
#    ``handle_event`` — exactly what we call below
#  * skipping the listener boot saves ~150ms per test and ~50 lines of
#    fixture surface (no port binding, no async, no _FakeFirmware)
#  * the auth + dispatch logic is already covered by ``test_grow_ws.py``
#    and ``test_e2e_smoke.py`` — exercising it again here would be
#    redundant test surface for zero additional signal
# ---------------------------------------------------------------------------


@pytest.fixture
def sense_stack(tmp_path, monkeypatch):
    """Yield a bundle for sense-only-mode e2e tests.

    Bundle keys:
      app:            the Flask app instance
      client:         test client with admin session
      unit_id:        1 (the seeded unit)
      db_path:        tmp DB path (for direct DB poke if needed)
      send_caps:      callable(payload, ts=None) — inject capabilities WS msg
      send_telemetry: callable(payload, ts=None) — inject telemetry WS msg
      send_event:     callable(payload, ts=None) — inject event WS msg
    """
    # ── 1. Tmp DB + DB_FILE patches across every grow module ──
    # Mirror the configure_e2e + history_e2e patch sets so any module that
    # snapshots DB_FILE at import time sees the test path. The watchdog
    # is patched too because it reads grow_watering_events / grow_telemetry
    # directly to evaluate the unresponsive condition.
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    import database.db_logger as dbl
    import database.user_db as udb
    init_db.DB_FILE = tmp.name
    dbl.DB_FILE = tmp.name
    udb.DB_FILE = tmp.name
    for mod in [
        "mlss_monitor.grow.auth",
        "mlss_monitor.grow.handlers",
        "mlss_monitor.grow.health_watchdog",
        "mlss_monitor.grow.photo_storage",
        "mlss_monitor.routes.api_grow_enroll",
        "mlss_monitor.routes.api_grow_units",
        "mlss_monitor.routes.api_grow_dist",
        "mlss_monitor.routes.api_grow_history",
        "mlss_monitor.routes.api_grow_photos",
        "mlss_monitor.routes.api_grow_ws",
        "mlss_monitor.routes.api_grow_config",
    ]:
        try:
            monkeypatch.setattr(f"{mod}.DB_FILE", tmp.name)
        except AttributeError:
            pass
    init_db.create_db()

    # ── 2. Seed a unit (no bearer token plumbing needed — tests drive the
    # handlers directly, and the GET endpoint is admin-session-authed). ──
    now = datetime.utcnow()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, current_phase, plant_type, "
        "medium_type, soil_dry_raw, soil_wet_raw) "
        "VALUES (1, 'hw-sense-e2e', 'E2E Sense', ?, 'h', ?, "
        "'vegetative', 'tomato', 'soil', 200, 1500)",
        (now, now),
    )
    conn.commit()
    conn.close()

    # ── 3. Boot the real Flask app with OAuth-on posture ──
    import mlss_monitor.app as app_module
    import mlss_monitor.state as app_state
    monkeypatch.setattr(app_module, "LOG_INTERVAL", 99999)
    monkeypatch.setattr(app_state, "fan_smart_plug", MagicMock())
    monkeypatch.setattr(app_state, "github_oauth", MagicMock())  # auth ON
    app_module.app.config["TESTING"] = True
    app_module.app.config["SECRET_KEY"] = "test-secret-sense-e2e"

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user"] = "test-admin"
        sess["user_role"] = "admin"

    # ── 4. Watchdog hygiene: clear any leaked module-global state from a
    # prior test in the same process. The watchdog uses a module-level dict
    # that survives across tests if not explicitly cleared. ──
    from mlss_monitor.grow import health_watchdog
    health_watchdog.clear()

    # ── 5. Helpers for injecting WS-style messages via the handler API. ──
    from mlss_monitor.grow import handlers as grow_handlers

    def send_caps(payload, ts=None):
        grow_handlers.handle_capabilities(1, ts or datetime.utcnow(), payload)

    def send_telemetry(payload, ts=None):
        return grow_handlers.handle_telemetry(1, ts or datetime.utcnow(), payload)

    def send_event(payload, ts=None):
        grow_handlers.handle_event(1, ts or datetime.utcnow(), payload)

    yield {
        "app":             app_module.app,
        "client":          client,
        "unit_id":         1,
        "db_path":         tmp.name,
        "send_caps":       send_caps,
        "send_telemetry":  send_telemetry,
        "send_event":      send_event,
    }

    # ── 6. Teardown: clear watchdog state so the next test starts clean. ──
    health_watchdog.clear()


# ---------------------------------------------------------------------------
# Helpers shared across tests.
# ---------------------------------------------------------------------------


def _get_caps(client, unit_id):
    """GET the unit and return capabilities keyed by channel."""
    r = client.get(f"/api/grow/units/{unit_id}")
    assert r.status_code == 200, r.data
    body = r.get_json()
    return {c["channel"]: c for c in body["capabilities"]}


def _seed_capability(db_path, unit_id, channel, hardware, health,
                     is_required=False, last_seen_at=None):
    """Direct INSERT into grow_unit_capabilities — used when a test needs
    to start from a specific health state (e.g. 'untested' or
    'unresponsive') without round-tripping a capabilities WS message."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_unit_capabilities "
        "(unit_id, channel, hardware, is_required, unit_label, "
        " installed_at, health, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (unit_id, channel, hardware, int(is_required), "bool",
         datetime.utcnow(), health, last_seen_at or datetime.utcnow()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Test 1: capabilities with no_hardware persist + surface to GET.
#
# The user's exact first-deployment scenario: camera + soil sensor wired,
# pump + light not yet powered. The firmware reports them as
# "no_hardware" and the server passes that all the way through to the
# GET response so the frontend can grey out the buttons.
# ---------------------------------------------------------------------------


def test_e2e_capabilities_with_no_hardware_health_persisted_and_surfaced(
    sense_stack,
):
    """Send a capabilities WS message with mixed health states; GET the
    unit and assert each capability's health is surfaced verbatim. Pins
    the end-to-end persistence + serialisation path through the typed
    health column (no details_json fallback)."""
    sense_stack["send_caps"]({
        "capabilities": [
            {"channel": "soil_moisture", "hardware": "Seesaw",
             "is_required": True, "unit_label": "raw",
             "details": {"i2c_address": "0x36"},
             "health": "connected"},
            {"channel": "pump", "hardware": "automation_phat",
             "is_required": False, "unit_label": "bool",
             "health": "no_hardware"},
            {"channel": "light", "hardware": "automation_phat",
             "is_required": False, "unit_label": "bool",
             "health": "no_hardware"},
        ],
        "firmware_version": "0.1.0",
        "hardware_serial": "hw-sense-e2e",
    })

    caps = _get_caps(sense_stack["client"], sense_stack["unit_id"])
    assert caps["soil_moisture"]["health"] == "connected"
    assert caps["pump"]["health"] == "no_hardware"
    assert caps["light"]["health"] == "no_hardware"
    # Heterogeneous metadata still surfaces alongside health (proves the
    # column-vs-details_json split is intact end-to-end).
    assert caps["soil_moisture"]["details"] == {"i2c_address": "0x36"}


# ---------------------------------------------------------------------------
# Test 2: telemetry with pump_state=1 promotes pump → "connected".
#
# Real-world flow: user installs the pump PSU; firmware reboots; the
# next pump pulse (or any test actuation) sends telemetry with
# pump_state=1; the server flips the persisted health to "connected" and
# the next GET reflects that.
# ---------------------------------------------------------------------------


def test_e2e_telemetry_with_pump_on_promotes_pump_health_to_connected(
    sense_stack,
):
    _seed_capability(
        sense_stack["db_path"], sense_stack["unit_id"],
        "pump", "automation_phat", "untested",
    )

    sense_stack["send_telemetry"]({
        "soil_moisture_raw": 612,
        "light_state": False,
        "pump_state": True,
    })

    caps = _get_caps(sense_stack["client"], sense_stack["unit_id"])
    assert caps["pump"]["health"] == "connected"


# ---------------------------------------------------------------------------
# Test 3: pump_state=0 does NOT demote a connected pump.
#
# Pins the asymmetry: pump_state=0 is the normal idle state, not
# evidence of disconnection. Only the watchdog (post-command timeout)
# demotes. Without this guarantee, every off-state telemetry frame would
# flicker the UI between connected and untested.
# ---------------------------------------------------------------------------


def test_e2e_telemetry_with_pump_off_does_not_demote_connected_pump(
    sense_stack,
):
    _seed_capability(
        sense_stack["db_path"], sense_stack["unit_id"],
        "pump", "automation_phat", "connected",
    )

    sense_stack["send_telemetry"]({
        "soil_moisture_raw": 612,
        "light_state": False,
        "pump_state": False,
    })

    caps = _get_caps(sense_stack["client"], sense_stack["unit_id"])
    assert caps["pump"]["health"] == "connected"


# ---------------------------------------------------------------------------
# Test 4: watering_event promotes pump → "connected" (recovery path).
#
# Strongest evidence the pump works: the firmware only emits
# watering_pulse AFTER the actuation completes. A previously-marked
# "unresponsive" capability snaps back to "connected" the moment the
# event lands.
# ---------------------------------------------------------------------------


def test_e2e_watering_event_promotes_pump_to_connected(sense_stack):
    _seed_capability(
        sense_stack["db_path"], sense_stack["unit_id"],
        "pump", "automation_phat", "unresponsive",
    )

    sense_stack["send_event"]({
        "kind": "watering_pulse",
        "details": {
            "duration_s": 5.0,
            "trigger": "manual",
            "triggered_by": "user",
        },
    })

    caps = _get_caps(sense_stack["client"], sense_stack["unit_id"])
    assert caps["pump"]["health"] == "connected"


# ---------------------------------------------------------------------------
# Test 5: watchdog marks pump unresponsive on command-without-event timeout.
#
# Lazy watchdog: the GET handler consults the watchdog for each
# actuator capability. If a command was recorded > timeout_s ago AND
# no follow-up evidence (watering_event row for pump) landed in that
# window, the response surfaces health="unresponsive" — even though
# the persisted column still says "connected" (so the next confirming
# event will quietly upgrade it back).
#
# Why we don't drive water_now POST end-to-end: that endpoint requires
# a live WS registry to push the command; the watchdog wiring is the
# ONLY behavior we're proving here, and the registry is covered by
# test_configure_e2e. Recording the command directly via
# record_command_sent(at=...) tests exactly the watchdog contract
# without 50 lines of WS fixture surface.
# ---------------------------------------------------------------------------


def test_e2e_watchdog_marks_pump_unresponsive_when_command_sent_but_no_event(
    sense_stack,
):
    _seed_capability(
        sense_stack["db_path"], sense_stack["unit_id"],
        "pump", "automation_phat", "connected",
    )

    # Pretend the server sent water_now 60 s ago. The default timeout is
    # 30 s, so we're well past it; no watering_event has landed since.
    from mlss_monitor.grow import health_watchdog
    health_watchdog.record_command_sent(
        sense_stack["unit_id"], "pump",
        at=datetime.utcnow() - timedelta(seconds=60),
    )

    caps = _get_caps(sense_stack["client"], sense_stack["unit_id"])
    assert caps["pump"]["health"] == "unresponsive"

    # The persisted column stays "connected" — the watchdog only overlays
    # the response. This guarantees the next confirming event quietly
    # upgrades the surfaced health back without a round-trip through the
    # database write path.
    conn = sqlite3.connect(sense_stack["db_path"])
    persisted = conn.execute(
        "SELECT health FROM grow_unit_capabilities "
        "WHERE unit_id=? AND channel='pump'",
        (sense_stack["unit_id"],),
    ).fetchone()[0]
    conn.close()
    assert persisted == "connected"


# ---------------------------------------------------------------------------
# Test 6: GET surfaces last_seen_at per capability.
#
# Pins the contract: when telemetry promotes a capability's health, the
# handler also stamps last_seen_at to the telemetry frame's ts. The GET
# response surfaces that field so the frontend can show "last reported X
# minutes ago" per channel.
# ---------------------------------------------------------------------------


def test_e2e_get_unit_includes_last_seen_at_per_capability(sense_stack):
    # Capabilities arrive at T0 with health="untested".
    t0 = datetime.utcnow() - timedelta(minutes=5)
    sense_stack["send_caps"]({
        "capabilities": [
            {"channel": "pump", "hardware": "automation_phat",
             "is_required": False, "unit_label": "bool",
             "health": "untested"},
        ],
        "firmware_version": "0.1.0",
        "hardware_serial": "hw-sense-e2e",
    }, ts=t0)

    # Telemetry arrives at T1 with pump_state=1 — should promote to
    # connected AND stamp last_seen_at = T1.
    t1 = datetime.utcnow()
    sense_stack["send_telemetry"]({
        "soil_moisture_raw": 612,
        "light_state": False,
        "pump_state": True,
    }, ts=t1)

    caps = _get_caps(sense_stack["client"], sense_stack["unit_id"])
    pump = caps["pump"]
    assert pump["health"] == "connected"
    assert pump["last_seen_at"] is not None
    # last_seen_at should reflect the telemetry frame's ts (T1), not the
    # earlier capabilities-registration ts (T0). The handler stamps it on
    # promotion.
    seen = datetime.fromisoformat(pump["last_seen_at"])
    # Allow 1 s slack for sqlite datetime adapter rounding.
    assert abs((seen - t1).total_seconds()) < 1.0


# ---------------------------------------------------------------------------
# Test 7: full first-boot story — no_hardware → user installs PSU →
#                                  reboot → untested → first water_now →
#                                  telemetry → connected.
#
# This is the user's exact intended UX flow for the first physical
# deployment. Pins the gradual-greening behavior: the buttons start
# greyed, un-grey to "untested" when the hardware is detected, and
# bright-green only once the hardware proves it works.
# ---------------------------------------------------------------------------


def test_e2e_full_first_boot_scenario_then_hardware_added(sense_stack):
    """End-to-end story across three boots + a telemetry frame.

    Phase A — first deployment (camera + soil sensor only):
      capabilities: soil_moisture=connected, pump=no_hardware,
                    light=no_hardware.
    Phase B — user installs the second PSU; firmware reboots and
              re-registers capabilities with successful HAT init:
      capabilities: soil_moisture=connected, pump=untested, light=untested.
    Phase C — user clicks "water now"; firmware actuates and emits
              telemetry with pump_state=1:
      pump capability promotes to "connected".
    """
    client = sense_stack["client"]
    uid = sense_stack["unit_id"]

    # ── Phase A: first deployment, pump+light have no hardware ──
    sense_stack["send_caps"]({
        "capabilities": [
            {"channel": "soil_moisture", "hardware": "Seesaw",
             "is_required": True, "unit_label": "raw",
             "health": "connected"},
            {"channel": "pump", "hardware": "automation_phat",
             "is_required": False, "unit_label": "bool",
             "health": "no_hardware"},
            {"channel": "light", "hardware": "automation_phat",
             "is_required": False, "unit_label": "bool",
             "health": "no_hardware"},
        ],
        "firmware_version": "0.1.0",
        "hardware_serial": "hw-sense-e2e",
    })

    caps_a = _get_caps(client, uid)
    # The frontend would render pump+light buttons greyed at this point.
    assert caps_a["soil_moisture"]["health"] == "connected"
    assert caps_a["pump"]["health"] == "no_hardware"
    assert caps_a["light"]["health"] == "no_hardware"

    # ── Phase B: user installs PSU; reboot; re-register capabilities ──
    # Same payload shape, different health values: HAT init now succeeds
    # but the actuators haven't been exercised yet, so they're "untested".
    sense_stack["send_caps"]({
        "capabilities": [
            {"channel": "soil_moisture", "hardware": "Seesaw",
             "is_required": True, "unit_label": "raw",
             "health": "connected"},
            {"channel": "pump", "hardware": "automation_phat",
             "is_required": False, "unit_label": "bool",
             "health": "untested"},
            {"channel": "light", "hardware": "automation_phat",
             "is_required": False, "unit_label": "bool",
             "health": "untested"},
        ],
        "firmware_version": "0.1.0",
        "hardware_serial": "hw-sense-e2e",
    })

    caps_b = _get_caps(client, uid)
    # The frontend un-greys to the "click to test" state.
    assert caps_b["pump"]["health"] == "untested"
    assert caps_b["light"]["health"] == "untested"
    # Soil moisture stays connected — the re-registration carried it
    # forward and DELETE+INSERT preserves the value, not the implicit
    # default.
    assert caps_b["soil_moisture"]["health"] == "connected"

    # ── Phase C: user clicks "water now"; firmware actuates; telemetry
    # arrives showing pump_state=1. The handler promotes pump health. ──
    sense_stack["send_telemetry"]({
        "soil_moisture_raw": 612,
        "light_state": False,
        "pump_state": True,
    })

    caps_c = _get_caps(client, uid)
    assert caps_c["pump"]["health"] == "connected"
    # Light remains untested — the user only tested the pump.
    assert caps_c["light"]["health"] == "untested"
    # Soil moisture, of course, still connected.
    assert caps_c["soil_moisture"]["health"] == "connected"
