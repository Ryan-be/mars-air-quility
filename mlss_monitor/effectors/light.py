"""Supplementary-lighting controller.

Schedule-driven rather than sensor-driven: the operator configures one
or more on-ranges by UTC hour and the controller votes ON when the
current UTC hour falls inside any range. Sensor reading is not
consulted — lighting decisions in v1 are clock-based.

Rule shape::

    {
      "schedule": [
        {"start_hr": 6,  "end_hr": 18},
        {"start_hr": 22, "end_hr": 24},
      ],
    }

Each range is a half-open ``[start_hr, end_hr)`` window. ``end_hr``
may equal ``24`` to mean "through midnight"; ranges that wrap past
midnight (e.g. start=22, end=4) are NOT supported in v1 — the
operator should split such a schedule into two ranges. The UI's
24-cell schedule editor (planned for v2 per the topology backlog)
emits ranges in this normalised form.

The ``datetime`` import is module-level so test code can monkeypatch
:attr:`datetime` to drive deterministic hour-of-day cases without
freezing the whole process clock.
"""
from __future__ import annotations

from datetime import datetime

from mlss_monitor.effectors.base import EffectorController, Scope


class LightSupplementary(EffectorController):
    """Schedule-driven supplementary lighting controller."""

    effector_type = "light_supplementary"

    def should_be_on(self, reading: dict, rules: dict) -> bool:
        schedule = rules.get("schedule") or []
        if not schedule:
            return False
        # Module-level datetime is monkeypatched in tests to drive
        # deterministic hour-of-day cases.
        now_hour = datetime.utcnow().hour
        for window in schedule:
            start = window.get("start_hr")
            end = window.get("end_hr")
            if start is None or end is None:
                continue
            if int(start) <= now_hour < int(end):
                return True
        return False

    def evaluate(self, reading: dict, rules: dict) -> dict:
        schedule = rules.get("schedule") or []
        now = datetime.utcnow()
        now_hour = now.hour
        reasons: list[dict] = []
        decision_on = False
        if not schedule:
            reasons.append({
                "rule":   "ScheduleRule",
                "fired":  False,
                "detail": "No schedule windows configured",
            })
        else:
            for window in schedule:
                start = window.get("start_hr")
                end = window.get("end_hr")
                if start is None or end is None:
                    continue
                fired = int(start) <= now_hour < int(end)
                if fired:
                    decision_on = True
                    detail = (f"Hour {now_hour:02d} is inside "
                              f"{int(start):02d}-{int(end):02d}")
                else:
                    detail = (f"Hour {now_hour:02d} outside "
                              f"{int(start):02d}-{int(end):02d}")
                reasons.append({
                    "rule":   "ScheduleRule",
                    "fired":  fired,
                    "detail": detail,
                })
        return {
            "decision":     "on" if decision_on else "off",
            "evaluated_at": now.isoformat(),
            "reasons":      reasons,
        }

    @classmethod
    def compatible_scopes(cls) -> set[Scope]:
        return {Scope.HUB, Scope.GROW_UNIT}
