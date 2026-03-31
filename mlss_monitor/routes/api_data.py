"""Sensor data API routes: fetch, download CSV, annotations."""

import csv
import io
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, send_file

from database.db_logger import (
    add_annotation, get_sensor_data_by_date, remove_annotation,
)
from mlss_monitor.rbac import require_role

api_data_bp = Blueprint("api_data", __name__)


def _parse_range(range_param):
    now = datetime.utcnow()
    range_map = {
        "15m": timedelta(minutes=15),
        "1h":  timedelta(hours=1),
        "6h":  timedelta(hours=6),
        "12h": timedelta(hours=12),
        "24h": timedelta(hours=24),
    }
    since = now - range_map.get(range_param, timedelta.max)
    if range_param not in range_map:
        since = datetime.min
    return since, now


@api_data_bp.route("/api/data")
def get_data():
    range_param = request.args.get("range", "24h")
    since, now = _parse_range(range_param)
    try:
        rows = get_sensor_data_by_date(since.isoformat(), now.isoformat())
        data = [
            {
                "id": row[0],
                "timestamp": row[1],
                "temperature": row[2],
                "humidity": row[3],
                "eco2": row[4],
                "tvoc": row[5],
                "annotation": row[6],
                "fan_power_w": row[7] if len(row) > 7 else None,
                "vpd_kpa": row[8] if len(row) > 8 else None,
                "pm1_0": row[9] if len(row) > 9 else None,
                "pm2_5": row[10] if len(row) > 10 else None,
                "pm10": row[11] if len(row) > 11 else None,
                "gas_co": row[12] if len(row) > 12 else None,
                "gas_no2": row[13] if len(row) > 13 else None,
                "gas_nh3": row[14] if len(row) > 14 else None,
            }
            for row in rows
        ]
    except Exception as e:
        return jsonify({"error": f"Error reading data: {str(e)}"}), 500
    return jsonify(data)


@api_data_bp.route("/api/download")
def download_data():
    range_param = request.args.get("range", "24h")
    since, now = _parse_range(range_param)
    try:
        rows = get_sensor_data_by_date(since.isoformat(), now.isoformat())
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "timestamp", "temperature", "humidity", "eco2", "tvoc", "annotation"])
        writer.writerows(rows)
        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode("utf-8")),
            mimetype="text/csv",
            as_attachment=True,
            download_name="sensor_data.csv",
        )
    except Exception as e:
        return jsonify({"error": f"Error generating CSV: {str(e)}"}), 500


@api_data_bp.route("/api/annotate", methods=["POST"])
@require_role("controller", "admin")
def annotate_point():
    try:
        entry_id = request.args.get("point", type=int)
        if not entry_id:
            return jsonify({"error": "'point' query parameter is required and must be an integer."}), 400
        data = request.get_json()
        annotation = data.get("annotation")
        if not annotation:
            return jsonify({"error": "'annotation' is required in the request body."}), 400
        add_annotation(entry_id, annotation)
        return jsonify({"message": "Annotation added successfully."}), 200
    except Exception as e:
        return jsonify({"error": f"Error adding annotation: {str(e)}"}), 500


@api_data_bp.route("/api/annotate", methods=["DELETE"])
@require_role("controller", "admin")
def remove_annotation_route():
    try:
        entry_id = request.args.get("point", type=int)
        if not entry_id:
            return jsonify({"error": "'point' query parameter is required and must be an integer."}), 400
        remove_annotation(entry_id)
        return jsonify({"message": "Annotation removed successfully."}), 200
    except Exception as e:
        return jsonify({"error": f"Error removing annotation: {str(e)}"}), 500
