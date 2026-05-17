"""GET /api/grow/units/<id>/history — moisture series + watering events for charts.

Supports ranges 24h / 7d / 30d / 90d / all. When the moisture-row count for a
range exceeds ``_DOWNSAMPLE_THRESHOLD`` (600), the rows are bucketed into at
most 600 buckets each carrying ``{ts, pct_min, pct_avg, pct_max, raw_avg}`` so
the frontend chart can render a min/max band rather than tens of thousands of
SVG points. Short ranges keep the original ``{ts, pct, raw}`` shape so the 24h
chart behaviour is unchanged.

The response always includes a ``phase_changes`` key (currently ``[]`` —
reserved for the Phase 3 phase-audit table the frontend annotation chart will
consume).

Compute-on-read for pct
-----------------------
A user can rack up days/weeks of telemetry against a Seesaw sensor BEFORE they
capture a dry/wet calibration. Pre-calibration rows have
``soil_moisture_pct = NULL`` (the firmware sends raw but can't compute pct).
Post-calibration rows have a non-null pct computed against whatever the
firmware's calibration was at the time the row was written.

The previous design read the stored pct column straight through to the chart.
That meant a freshly-calibrated unit's chart showed only the post-calibration
sliver of history (often just minutes of data) while the user's actual full
24h/7d/etc. timeline of raw readings sat invisible in the DB.

This module now IGNORES the stored ``soil_moisture_pct`` column for the chart
and recomputes pct from ``soil_moisture_raw`` against the unit's CURRENT
calibration on every request. Side effects:

  * Recalibrating a sensor instantly re-frames the entire visible history —
    no DB write needed, the next /history fetch reflects it.
  * All historical raw readings get a meaningful pct as long as the unit is
    calibrated, so the chart can show the user's full timeline in % terms.
  * The stored pct column becomes advisory — the WS handler still writes it
    (other consumers like alerting may use the firmware's view of pct, which
    can legitimately differ from the API's current-calibration view) but the
    History endpoint does not trust it.

A freshly-plugged-in Seesaw sensor with no calibration captured yet falls
through to the uncalibrated path: every row carries ``pct: None`` and the
frontend renders against the raw 0–1023 axis.

  * Returns a top-level ``calibrated`` boolean — derived from the unit's
    calibration columns, not the row data. The frontend uses this to pick a
    0–100 % Y-axis (calibrated) vs a 0–1023 raw Y-axis (uncalibrated).
  * In the bucketed path always emit a bucket if ``slice_rows`` is non-empty,
    always populate ``raw_avg``, and only emit the ``pct_*`` keys when at least
    one row in the bucket has a non-null pct (we drop them entirely rather than
    emitting nulls — keeps the existing shape-sniff via ``pct_avg`` presence
    working on the frontend).
"""
import sqlite3
from datetime import datetime, timedelta
from typing import Any
from flask import Blueprint, jsonify, request
from database.init_db import DB_FILE
from mlss_monitor.grow.api_helpers import RANGE_TO_HOURS

api_grow_history_bp = Blueprint("api_grow_history", __name__)

_DOWNSAMPLE_THRESHOLD = 600


def _compute_pct(raw, dry_raw, wet_raw):
    """Return moisture percent computed from raw using the unit's
    calibration. None if any input is None or the calibration is
    degenerate (wet == dry). Result is clamped to [0.0, 100.0] so a
    saturated reading (raw > wet_raw, possible after recalibration)
    doesn't render as > 100% on the chart.

    Degenerate / inverted calibrations (wet <= dry) are treated as
    uncalibrated — returning None lets the frontend fall back to the raw
    axis rather than rendering nonsense on a 0–100 % axis built around a
    zero-or-negative span.
    """
    if raw is None or dry_raw is None or wet_raw is None:
        return None
    span = wet_raw - dry_raw
    if span <= 0:
        return None  # degenerate / inverted calibration; treat as uncalibrated
    pct = (raw - dry_raw) / span * 100.0
    return max(0.0, min(100.0, pct))


def _maybe_downsample(rows, target=_DOWNSAMPLE_THRESHOLD):
    """Return rows as-is when len(rows) <= target, else bucket into target buckets.

    Non-bucketed path emits ``{ts, pct, raw}`` (pct may be None — the frontend
    falls back to raw in uncalibrated mode).

    Bucketed path emits ``{ts, raw_avg, [pct_min, pct_avg, pct_max]}`` — the
    pct_* keys are dropped (not nulled) when no row in the bucket has a
    non-null pct, so the frontend can still sniff the shape via
    ``pct_avg`` presence. Buckets where every ``soil_moisture_raw`` is
    NULL are skipped (defensive — the schema marks raw NOT NULL, but the
    check is cheap insurance against future schema drift).
    """
    if len(rows) <= target:
        return [
            {
                "ts": r["timestamp_utc"],
                "pct": r["soil_moisture_pct"],
                "raw": r["soil_moisture_raw"],
            }
            for r in rows
        ]
    bucket_size = len(rows) / target
    buckets = []
    for i in range(target):
        start = int(i * bucket_size)
        end = int((i + 1) * bucket_size) if i < target - 1 else len(rows)
        slice_rows = rows[start:end]
        if not slice_rows:
            continue
        raws = [
            r["soil_moisture_raw"]
            for r in slice_rows
            if r["soil_moisture_raw"] is not None
        ]
        if not raws:
            # All-NULL-raw bucket — nothing to plot. Should never trigger
            # given the schema, but skipping is safer than emitting a
            # bucket with no numeric data the chart could render.
            continue
        pcts = [
            r["soil_moisture_pct"]
            for r in slice_rows
            if r["soil_moisture_pct"] is not None
        ]
        bucket = {
            "ts": slice_rows[len(slice_rows) // 2]["timestamp_utc"],
            "raw_avg": sum(raws) / len(raws),
        }
        if pcts:
            # At least one row in this bucket is calibrated — emit the band
            # keys. (Mixed buckets — some calibrated rows, some null — get
            # band keys derived from the calibrated subset only; that's a
            # close enough approximation while a sensor transitions out of
            # uncalibrated state.)
            bucket["pct_min"] = min(pcts)
            bucket["pct_avg"] = sum(pcts) / len(pcts)
            bucket["pct_max"] = max(pcts)
        buckets.append(bucket)
    return buckets


@api_grow_history_bp.route("/api/grow/units/<int:unit_id>/history", methods=["GET"])
def history(unit_id):
    range_str = request.args.get("range", "24h")
    if range_str not in RANGE_TO_HOURS:
        return jsonify({"error": "invalid_range"}), 400
    hours = RANGE_TO_HOURS[range_str]
    cutoff = (datetime.utcnow() - timedelta(hours=hours)) if hours is not None else None

    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        # Fetch the unit's current calibration FIRST. We need it before the
        # telemetry fetch so the per-row recompute loop can use it; and we
        # need it as the source of truth for the response's `calibrated`
        # flag (derived from the unit, not from any row data).
        cal_row = conn.execute(
            "SELECT soil_dry_raw, soil_wet_raw FROM grow_units WHERE id = ?",
            (unit_id,),
        ).fetchone()
        if cal_row is None:
            return jsonify({"error": "unit_not_found"}), 404
        dry_raw = cal_row["soil_dry_raw"]
        wet_raw = cal_row["soil_wet_raw"]

        if cutoff is not None:
            moisture_rows = conn.execute(
                "SELECT timestamp_utc, soil_moisture_pct, soil_moisture_raw "
                "FROM grow_telemetry WHERE unit_id=? AND timestamp_utc >= ? "
                "ORDER BY timestamp_utc ASC",
                (unit_id, cutoff),
            ).fetchall()
            event_rows = conn.execute(
                "SELECT timestamp_utc, trigger, duration_s, soil_pct_before "
                "FROM grow_watering_events WHERE unit_id=? AND timestamp_utc >= ? "
                "ORDER BY timestamp_utc ASC",
                (unit_id, cutoff),
            ).fetchall()
        else:
            moisture_rows = conn.execute(
                "SELECT timestamp_utc, soil_moisture_pct, soil_moisture_raw "
                "FROM grow_telemetry WHERE unit_id=? "
                "ORDER BY timestamp_utc ASC",
                (unit_id,),
            ).fetchall()
            event_rows = conn.execute(
                "SELECT timestamp_utc, trigger, duration_s, soil_pct_before "
                "FROM grow_watering_events WHERE unit_id=? "
                "ORDER BY timestamp_utc ASC",
                (unit_id,),
            ).fetchall()
    finally:
        conn.close()

    # Convert each row to a plain dict so the downsampler can read a
    # RECOMPUTED `soil_moisture_pct` from the unit's current calibration.
    # sqlite3.Row is immutable, so we'd otherwise have to wrap it in a
    # mapping shim — the dict copy is the simplest path. Compute-on-read:
    # we ignore the stored pct column entirely. The stored value may have
    # been computed against an older calibration (or be NULL because it
    # predates calibration), so always recompute from CURRENT calibration
    # so the chart's framing is consistent across the whole timeline.
    moisture_rows: list[dict[str, Any]] = [
        {
            "timestamp_utc": r["timestamp_utc"],
            "soil_moisture_raw": r["soil_moisture_raw"],
            "soil_moisture_pct": _compute_pct(
                r["soil_moisture_raw"], dry_raw, wet_raw),
        }
        for r in moisture_rows
    ]

    # `calibrated` is now a property of the UNIT, not the rows. A unit is
    # calibrated iff both raw bounds are set AND the calibration is
    # non-degenerate (wet > dry). Matches _compute_pct's contract — when
    # this is False, every row's recomputed pct is None.
    calibrated = (
        dry_raw is not None and wet_raw is not None and wet_raw > dry_raw
    )

    return jsonify({
        "calibrated": calibrated,
        "moisture": _maybe_downsample(moisture_rows),
        "watering_events": [
            {
                "ts": r["timestamp_utc"],
                "trigger": r["trigger"],
                "duration_s": r["duration_s"],
                "soil_pct_before": r["soil_pct_before"],
            }
            for r in event_rows
        ],
        # Reserved for the Phase 3 phase-audit table — frontend chart will
        # annotate phase transitions. Always present so the consumer doesn't
        # need to defensively check for the key.
        "phase_changes": [],
    })
