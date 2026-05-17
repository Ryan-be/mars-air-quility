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

A freshly-plugged-in Seesaw sensor emits raw values but its ``soil_moisture_pct``
column stays NULL until the user captures dry/wet calibration points. To stop
the chart from rendering blank for uncalibrated units we:

  * Return a top-level ``calibrated`` boolean — ``true`` iff at least one row in
    the returned moisture series has a non-null pct. The frontend uses this to
    pick a 0–100 % Y-axis (calibrated) vs a 0–1023 raw Y-axis (uncalibrated).
  * In the bucketed path always emit a bucket if ``slice_rows`` is non-empty,
    always populate ``raw_avg``, and only emit the ``pct_*`` keys when at least
    one row in the bucket has a non-null pct (we drop them entirely rather than
    emitting nulls — keeps the existing shape-sniff via ``pct_avg`` presence
    working on the frontend).
"""
import sqlite3
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request
from database.init_db import DB_FILE
from mlss_monitor.grow.api_helpers import RANGE_TO_HOURS

api_grow_history_bp = Blueprint("api_grow_history", __name__)

_DOWNSAMPLE_THRESHOLD = 600


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


def _is_calibrated(rows):
    """True iff at least one row carries a non-null soil_moisture_pct.

    Drives the top-level ``calibrated`` flag on the response — the
    frontend uses it to choose its Y-axis (0-100 % vs 0-1023 raw).
    """
    return any(r["soil_moisture_pct"] is not None for r in rows)


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

    return jsonify({
        "moisture": _maybe_downsample(moisture_rows),
        # Computed off the raw rows (not the downsampled output) so the flag
        # stays correct regardless of which path _maybe_downsample takes.
        "calibrated": _is_calibrated(moisture_rows),
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
