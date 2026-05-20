"""Map event_bus event_type + payload -> NotificationSpec.

The dispatcher consults this module to decide which events become
notifications, in which category, at what severity, with what
title/body/deep_link. Return None for events that should NOT generate
a notification (sensor_update, fan_status, etc.).

To add a new notification source: add a handler function below, register
it in EVENT_HANDLERS, and (if needed) add a publish() call from the
event source.
"""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class NotificationSpec:
    """Everything the dispatcher needs to fan out one notification."""
    category: str          # 'air_quality' | 'grow_units' | 'system_health' | 'backup_pipeline'
    severity: str          # 'info' | 'warning' | 'critical'
    title: str             # shown as the notification heading
    body: str              # truncated to 120 chars in push payload
    deep_link: str         # URL path opened on notification tap


_BODY_MAX = 120


def _truncate(s: str, n: int = _BODY_MAX) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _map_inference_fired(data: dict) -> NotificationSpec | None:
    severity = data.get("severity", "info")
    if severity not in ("info", "warning", "critical"):
        return None
    return NotificationSpec(
        category="air_quality",
        severity=severity,
        title=data.get("title", "Air quality event"),
        body=_truncate(data.get("description", "")),
        deep_link="/incidents",
    )


_BACKUP_NOTIFY_STATES = {
    "BACKOFF":            "warning",
    "FAILED":             "warning",
    "DISABLED_BY_ERROR":  "critical",
}


def _map_backup_status_changed(data: dict) -> NotificationSpec | None:
    state = data.get("state", "")
    severity = _BACKUP_NOTIFY_STATES.get(state)
    if severity is None:
        return None
    pipeline = data.get("pipeline", "?")
    backoff = data.get("backoff_seconds")
    pending = data.get("pending")
    body_bits = []
    if backoff is not None:
        body_bits.append(f"Backoff {backoff}s")
    if pending is not None:
        body_bits.append(f"{pending} pending")
    return NotificationSpec(
        category="backup_pipeline",
        severity=severity,
        title=f"Backup {pipeline}: {state}",
        body=_truncate(", ".join(body_bits) or "Pipeline state changed"),
        deep_link="/admin/backup",
    )


def _map_health_update(data: dict) -> NotificationSpec | None:
    failed = [k for k, v in data.items() if v == "UNAVAILABLE"]
    if not failed:
        return None
    severity = "critical" if len(failed) > 1 else "warning"
    title = (f"Sensors offline: {', '.join(failed)}" if len(failed) > 1
             else f"Sensor offline: {failed[0]}")
    return NotificationSpec(
        category="system_health",
        severity=severity,
        title=title,
        body=_truncate("Health check reports UNAVAILABLE"),
        deep_link="/",
    )


def _map_grow_error_logged(data: dict) -> NotificationSpec | None:
    severity = data.get("severity", "info")
    if severity not in ("info", "warning", "critical"):
        return None
    unit_id = data.get("unit_id", "?")
    return NotificationSpec(
        category="grow_units",
        severity=severity,
        title=f"Grow unit #{unit_id}: {data.get('title', 'Error')}",
        body=_truncate(data.get("message", "")),
        deep_link=f"/grow/{unit_id}",
    )


EVENT_HANDLERS: dict[str, Callable[[dict], NotificationSpec | None]] = {
    "inference_fired":         _map_inference_fired,
    "backup_status_changed":   _map_backup_status_changed,
    "health_update":           _map_health_update,
    "grow_error_logged":       _map_grow_error_logged,
}


def map_event(event_type: str, data: dict) -> NotificationSpec | None:
    """Return a NotificationSpec for the event, or None if it shouldn't notify."""
    handler = EVENT_HANDLERS.get(event_type)
    if handler is None:
        return None
    try:
        return handler(data or {})
    except Exception:  # pylint: disable=broad-except
        return None
