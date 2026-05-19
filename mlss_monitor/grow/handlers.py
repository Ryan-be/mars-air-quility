"""Per-message-type handlers for the grow WebSocket listener.

Each handler is a pure function over (unit_id, ts, payload) — easy to unit
test without spinning up a real WebSocket. The WS listener (Task 4.6)
dispatches incoming text frames to these by message type.
"""
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Optional

from database.init_db import DB_FILE
from mlss_monitor.backup import outbox


def _compute_moisture_pct(raw: int, dry: Optional[int], wet: Optional[int]) -> Optional[float]:
    """Linear-map raw → %. Returns None if calibration not present."""
    if dry is None or wet is None or wet <= dry:
        return None
    pct = (raw - dry) / (wet - dry) * 100
    return max(0.0, min(100.0, round(pct, 2)))


def handle_telemetry(unit_id: int, ts: datetime, payload: dict) -> int:
    """Insert one grow_telemetry row + refresh grow_units.last_seen_at.

    If payload['soil_moisture_pct'] is missing but the unit has calibration
    set, computes pct server-side from the raw reading.

    Caller (the WS listener in Task 4.5) is expected to have validated the
    payload via pydantic — this function trusts `light_state`/`pump_state`
    to be coercible to int and `soil_moisture_raw` to be present.

    Returns the inserted grow_telemetry.id (used by photo upload to backfill
    the telemetry_id join key).

    Phase 2 schema cleanup: the previous implementation rewrote a
    denormalised JSON blob (last_known_state_json) on every frame so the
    fleet-view API could read state without a join. That cache has been
    replaced with a SELECT against grow_telemetry ORDER BY timestamp_utc
    DESC LIMIT 1 (already indexed) — eliminating a hot write path.

    Phase 2 backup wiring: every write to a replicated table also
    enqueues an outbox pointer in the same transaction so the shipper
    can ship the change to the home server. `with closing(...) + with
    conn:` replaces the old try/finally + manual commit pattern —
    crash between the live write and the outbox enqueue is now
    impossible.
    """
    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        with conn:  # transaction context — commit on success, rollback on exception
            # Server-side pct fill if missing
            pct = payload.get("soil_moisture_pct")
            if pct is None and "soil_moisture_raw" in payload:
                cal = conn.execute(
                    "SELECT soil_dry_raw, soil_wet_raw FROM grow_units WHERE id=?",
                    (unit_id,),
                ).fetchone()
                if cal:
                    pct = _compute_moisture_pct(payload["soil_moisture_raw"],
                                                 cal["soil_dry_raw"], cal["soil_wet_raw"])

            cur = conn.execute(
                "INSERT INTO grow_telemetry "
                "(unit_id, timestamp_utc, soil_moisture_raw, soil_moisture_pct, "
                " light_state, pump_state, soil_temp_c, ambient_lux, "
                " air_temp_c, air_humidity_pct, reservoir_level_pct) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (unit_id, ts, payload["soil_moisture_raw"], pct,
                 int(payload["light_state"]), int(payload["pump_state"]),
                 payload.get("soil_temp_c"), payload.get("ambient_lux"),
                 payload.get("air_temp_c"), payload.get("air_humidity_pct"),
                 payload.get("reservoir_level_pct")),
            )
            inserted_id = cur.lastrowid
            outbox.enqueue_row(conn, table="grow_telemetry", pk=inserted_id)

            # NOTE: both timestamps bound to payload `ts`. Once heartbeats land
            # (post-Phase 1), last_seen_at should be set from server clock so the
            # fleet view can show "online recently" independently of telemetry cadence.
            conn.execute(
                "UPDATE grow_units SET last_telemetry_at=?, last_seen_at=? WHERE id=?",
                (ts, ts, unit_id),
            )
            outbox.enqueue_row(conn, table="grow_units", pk=unit_id)

            # Phase 3 diagnostics cache. uptime_s + buffer_size are optional —
            # firmware too old to emit them keeps validating; we just don't
            # update the cached columns. Omit-doesnt-clobber: a frame WITHOUT
            # the field MUST NOT overwrite the previously-known value with
            # NULL, because the Diagnostics tab would then bounce between
            # "known" and "unknown" depending on which firmware is currently
            # talking. Hence the conditional UPDATE.
            #
            # buffer_summary / photo_buffer_summary follow the same shape but
            # piggyback on every 10th telemetry frame rather than every tick
            # (see SafetyLoop._tick_count + _BUFFER_SUMMARY_EVERY_N_TICKS).
            # Stored as JSON-in-TEXT for the same reason as `details_json`:
            # the structure is a read-only cache the diagnostics endpoint
            # parses back, not something we ever query into.
            uptime_s = payload.get("uptime_s")
            buffer_size = payload.get("buffer_size")
            buffer_summary = payload.get("buffer_summary")
            photo_buffer_summary = payload.get("photo_buffer_summary")
            if (uptime_s is not None or buffer_size is not None
                    or buffer_summary is not None
                    or photo_buffer_summary is not None):
                sets: list[str] = []
                values: list = []
                if uptime_s is not None:
                    sets.append("last_uptime_s=?")
                    values.append(uptime_s)
                if buffer_size is not None:
                    sets.append("last_buffer_size=?")
                    values.append(buffer_size)
                if buffer_summary is not None:
                    sets.append("last_buffer_summary_json=?")
                    values.append(json.dumps(buffer_summary))
                if photo_buffer_summary is not None:
                    sets.append("last_photo_buffer_summary_json=?")
                    values.append(json.dumps(photo_buffer_summary))
                values.append(unit_id)
                conn.execute(
                    f"UPDATE grow_units SET {', '.join(sets)} WHERE id=?",
                    values,
                )
                # outbox ON CONFLICT(table_name, pk) coalesces — the
                # earlier last_seen_at enqueue already covers this row,
                # but call again so the intent is explicit and future
                # refactors that drop the earlier enqueue stay correct.
                outbox.enqueue_row(conn, table="grow_units", pk=unit_id)

            # Phase 2 sense-only-mode: promote actuator capabilities to
            # "connected" when their state is non-zero. Why only on state=1?
            # Because pump_state=0 / light_state=0 is the normal idle state,
            # not evidence of disconnection. The watchdog handles regression.
            if int(payload["pump_state"]):
                _promote_capability_health(conn, unit_id, "pump", "connected", ts)
            if int(payload["light_state"]):
                _promote_capability_health(conn, unit_id, "light", "connected", ts)

            # Pre-Phase-4 audit fix (Flow 1 #3): bump last_seen_at on each
            # sensor capability whose channel reported a real reading in
            # this frame. Pre-fix only the actuator-promotion path bumped
            # the timestamp, and only when state=1, so soil_moisture /
            # soil_temp_c / ambient_lux / etc. stayed forever stale in the
            # Diagnostics → Sensor sanity panel even when telemetry was
            # streaming.
            #
            # `soil_moisture_raw` is always present in the payload (firmware
            # sends 0 when there's no sensor, otherwise the raw value). We
            # treat raw > 0 as "sensor read produced a value" — the seesaw's
            # sane range is 200..2000 so 0 unambiguously means "no sensor"
            # vs every other channel which is nullable.
            if payload.get("soil_moisture_raw", 0) > 0:
                _promote_capability_health(
                    conn, unit_id, "soil_moisture", "connected", ts,
                )
            for channel in ("soil_temp_c", "ambient_lux", "air_temp_c",
                            "air_humidity_pct", "reservoir_level_pct"):
                if payload.get(channel) is not None:
                    _promote_capability_health(
                        conn, unit_id, channel, "connected", ts,
                    )

            return inserted_id


def handle_capabilities(unit_id: int, ts: datetime, payload: dict) -> None:
    """Replace the unit's full capability set with what was just declared.

    Caller (the WS listener in Task 4.5) is expected to have validated the
    payload via pydantic. Idempotent: a re-pushed identical payload simply
    rewrites the same rows. A removed sensor disappears on next push.

    Phase 2 schema cleanup: `health` is now a typed column (TEXT NOT NULL
    DEFAULT 'untested', CHECK enum on fresh schemas, indexed on
    (unit_id, health)). Previously stored as details_json.health which
    forced read-modify-write and prevented cross-fleet querying.
    `details_json` retains driver-specific heterogeneous metadata only
    (e.g. {"i2c_address": "0x36"}).

    Phase 2 backup wiring: grow_unit_capabilities is a strict-mirror
    table — the DELETE+INSERT replace pattern means the server needs
    a wipe marker BEFORE the new INSERTs land, or a partial-ship +
    crash + restart sequence could leave stale rows on the server.
    enqueue_delete_scope queues that marker first; the shipper
    processes delete-scope entries ahead of the corresponding row
    pointers so the replace arrives atomically.
    """
    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
        with conn:  # transaction context — commit on success, rollback on exception
            # Queue the wipe marker BEFORE the DELETE so a re-crash between
            # DELETE and the per-row INSERT enqueues cannot leave the server
            # with stale rows that the wipe is supposed to clear.
            outbox.enqueue_delete_scope(
                conn, table="grow_unit_capabilities",
                scope={"unit_id": unit_id},
            )
            conn.execute(
                "DELETE FROM grow_unit_capabilities WHERE unit_id=?",
                (unit_id,),
            )
            for cap in payload["capabilities"]:
                details = cap.get("details") or None
                details_json = json.dumps(details) if details else None
                conn.execute(
                    "INSERT INTO grow_unit_capabilities "
                    "(unit_id, channel, hardware, is_required, unit_label, "
                    " installed_at, details_json, health, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (unit_id, cap["channel"], cap.get("hardware"),
                     int(cap["is_required"]), cap.get("unit_label"), ts,
                     details_json, cap.get("health", "untested"), ts),
                )
                outbox.enqueue_row(
                    conn, table="grow_unit_capabilities",
                    pk=f"{unit_id}:{cap['channel']}",
                )
            conn.execute(
                "UPDATE grow_units SET last_seen_at=? WHERE id=?",
                (ts, unit_id),
            )
            outbox.enqueue_row(conn, table="grow_units", pk=unit_id)

            # Phase 3 diagnostics cache. firmware_version is sent only with
            # capabilities (boot/reconnect) — keeping it off the per-tick
            # telemetry frame saves bandwidth, since it changes only when the
            # operator re-flashes the unit. Omit-doesnt-clobber: a payload
            # without the field leaves the column untouched.
            #
            # No second enqueue here: outbox ON CONFLICT coalesces the
            # firmware_version UPDATE into the already-queued grow_units
            # pointer — the shipper will read current state at ship time.
            fw_version = payload.get("firmware_version")
            if fw_version is not None:
                conn.execute(
                    "UPDATE grow_units SET firmware_version=? WHERE id=?",
                    (fw_version, unit_id),
                )


def _promote_capability_health(conn: sqlite3.Connection, unit_id: int,
                               channel: str, new_health: str,
                               last_seen_at: Optional[datetime] = None) -> None:
    """Update grow_unit_capabilities.health in-place.

    Promotes a capability to a stronger health state when telemetry or an
    event proves the actuator is responding. A single column UPDATE —
    no JSON read-modify-write.

    Idempotent: if the capability already has the target health, this
    is a no-op write. Silently no-ops if the row doesn't exist (the
    telemetry handler shouldn't crash on a unit whose capabilities
    haven't yet been declared — that's a transient ordering quirk).

    Phase 2 backup wiring: only enqueues the outbox row when the
    UPDATE actually fires — no-op branches (row missing or health
    already at target) MUST NOT enqueue, otherwise every steady-state
    telemetry frame would mark capabilities dirty for shipping.
    """
    row = conn.execute(
        "SELECT health FROM grow_unit_capabilities "
        "WHERE unit_id=? AND channel=?",
        (unit_id, channel),
    ).fetchone()
    if row is None:
        return
    if row[0] == new_health:
        return
    conn.execute(
        "UPDATE grow_unit_capabilities SET health=?, last_seen_at=? "
        "WHERE unit_id=? AND channel=?",
        (new_health, last_seen_at or datetime.utcnow(), unit_id, channel),
    )
    outbox.enqueue_row(
        conn, table="grow_unit_capabilities", pk=f"{unit_id}:{channel}",
    )


def handle_event(unit_id: int, ts: datetime, payload: dict) -> None:
    """Dispatch by event kind.

    Watering events → grow_watering_events; sensor_*/safety_* and other
    diagnostic events → grow_errors. sensor_recovered closes any matching
    open sensor_degraded row for the same sensor. Unknown kinds are
    log-only (no DB row in Phase 1).

    Caller (the WS listener in Task 4.5) is expected to have validated the
    payload via pydantic.

    Phase 2 backup wiring: every INSERT into grow_watering_events /
    grow_errors and every UPDATE on grow_units enqueues an outbox
    pointer in the same transaction. sensor_recovered SELECTs the
    affected grow_errors ids before the UPDATE and enqueues each so
    the server learns about the resolution (deletes don't propagate
    for grow_errors, but UPDATE on existing rows does).
    """
    kind = payload["kind"]
    details = payload.get("details") or {}
    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
        with conn:  # transaction context — commit on success, rollback on exception
            if kind == "watering_pulse":
                cur = conn.execute(
                    "INSERT INTO grow_watering_events "
                    "(unit_id, timestamp_utc, trigger, duration_s, soil_pct_before, "
                    " triggered_by, pid_error, pid_p_term, pid_i_term, pid_d_term) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (unit_id, ts, details.get("trigger", "pid"),
                     details["duration_s"], details.get("soil_pct_before"),
                     details.get("triggered_by", "system"), details.get("pid_error"),
                     details.get("pid_p_term"), details.get("pid_i_term"),
                     details.get("pid_d_term")),
                )
                outbox.enqueue_row(
                    conn, table="grow_watering_events", pk=cur.lastrowid,
                )
                # Phase 2 sense-only-mode: a completed watering pulse is the
                # strongest evidence the pump works. Promote regardless of
                # current health so a previously-"unresponsive" capability
                # snaps back to "connected" the moment evidence arrives.
                # _promote_capability_health enqueues caps if it fires.
                _promote_capability_health(conn, unit_id, "pump", "connected", ts)
            elif kind == "sensor_degraded":
                sensor = details.get("sensor", "unknown")
                cur = conn.execute(
                    "INSERT INTO grow_errors "
                    "(unit_id, timestamp_utc, severity, kind, message, details_json, "
                    " subject_sensor) "
                    "VALUES (?, ?, 'warning', 'sensor_degraded', ?, ?, ?)",
                    (unit_id, ts, f"Sensor {sensor} reporting bad reads",
                     json.dumps(details), sensor),
                )
                outbox.enqueue_row(conn, table="grow_errors", pk=cur.lastrowid)
            elif kind == "sensor_recovered":
                sensor = details.get("sensor", "unknown")
                # SELECT affected ids BEFORE the UPDATE so we know which
                # grow_errors rows to enqueue. Deletes don't propagate for
                # grow_errors (append-mostly), but UPDATE on existing rows
                # must — otherwise the server stays unaware that operator
                # alerts have been resolved.
                affected = conn.execute(
                    "SELECT id FROM grow_errors "
                    "WHERE unit_id=? AND kind='sensor_degraded' "
                    "AND resolved_at IS NULL AND subject_sensor=?",
                    (unit_id, sensor),
                ).fetchall()
                conn.execute(
                    "UPDATE grow_errors SET resolved_at=? "
                    "WHERE unit_id=? AND kind='sensor_degraded' AND resolved_at IS NULL "
                    "AND subject_sensor=?",
                    (ts, unit_id, sensor),
                )
                for (err_id,) in affected:
                    outbox.enqueue_row(conn, table="grow_errors", pk=err_id)
            elif kind == "safety_cap_hit":
                cur = conn.execute(
                    "INSERT INTO grow_errors "
                    "(unit_id, timestamp_utc, severity, kind, message, details_json) "
                    "VALUES (?, ?, 'warning', 'safety_cap_hit', ?, ?)",
                    (unit_id, ts, f"Safety cap hit: {details.get('cap', '')}",
                     json.dumps(details)),
                )
                outbox.enqueue_row(conn, table="grow_errors", pk=cur.lastrowid)
            elif kind == "buffer_eviction":
                # Pre-Phase-4 audit fix (Flow 6 #1): firmware emits this
                # when its LocalBuffer hits a row/byte cap and rotates
                # rows out FIFO-style. Surfaces as a warning-severity
                # grow_errors row so operators see "your unit dropped data"
                # in the UI rather than discovering it from journalctl.
                reason = details.get("reason", "unknown")
                evicted = details.get("evicted_count", "?")
                cur = conn.execute(
                    "INSERT INTO grow_errors "
                    "(unit_id, timestamp_utc, severity, kind, message, details_json) "
                    "VALUES (?, ?, 'warning', 'buffer_eviction', ?, ?)",
                    (unit_id, ts,
                     f"Buffer eviction ({reason}): {evicted} row(s) dropped",
                     json.dumps(details)),
                )
                outbox.enqueue_row(conn, table="grow_errors", pk=cur.lastrowid)
            elif kind in ("buffer_replay_started", "buffer_replay_complete"):
                # Info-severity diagnostic event — useful in the connection log
                # ("unit replayed 200 buffered messages after a 12m outage")
                # but not an alert. The errors page filters info-severity
                # rows by default (commit 2f3aa51) so this doesn't add noise.
                count = details.get("count")
                msg = (
                    f"Buffer replay {'started' if kind == 'buffer_replay_started' else 'complete'}"
                    + (f": {count} messages" if count is not None else "")
                )
                cur = conn.execute(
                    "INSERT INTO grow_errors "
                    "(unit_id, timestamp_utc, severity, kind, message, details_json) "
                    "VALUES (?, ?, 'info', ?, ?, ?)",
                    (unit_id, ts, kind, msg, json.dumps(details)),
                )
                outbox.enqueue_row(conn, table="grow_errors", pk=cur.lastrowid)
            # Other event kinds (startup, shutdown, identify_complete, etc.) are
            # logged-only — no DB row needed in Phase 1.
            conn.execute(
                "UPDATE grow_units SET last_seen_at=? WHERE id=?", (ts, unit_id),
            )
            outbox.enqueue_row(conn, table="grow_units", pk=unit_id)
