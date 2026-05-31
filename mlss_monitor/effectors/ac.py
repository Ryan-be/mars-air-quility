"""Air-conditioner controller (hub-only).

Rule shape: ``{"target": <float>}``. ON when ``temperature > target``,
OFF otherwise.

v1 deliberately omits compressor min-off protection — a real AC unit
should not re-start its compressor within 5 minutes of switching off
or the inrush current can trip a fuse / shorten the compressor life.
That protection needs runtime memory (last-off timestamp) the v1
evaluator doesn't carry, so v1 trusts:

  (a) the evaluator's "only flip when desired != current_state" de-dupe,
      which already collapses tick-to-tick chatter on a steady reading;
  (b) the AC's own thermostatic deadband, which most consumer units
      enforce internally regardless of what the smart plug requests.

TODO(v2): min-off compressor protection. Will live in the evaluator
loop alongside the carbon-filter min-on guard so both classes of
"protect the hardware from thrash" rules share one timer mechanism.
"""
from __future__ import annotations

from datetime import datetime

from mlss_monitor.effectors.base import EffectorController, Scope


class AC(EffectorController):
    """Air-conditioner controller — ON when temp > target."""

    effector_type = "ac"

    def should_be_on(self, reading: dict, rules: dict) -> bool:
        # Canonical NormalisedReading field name — the evaluator's
        # reading dict comes from ``dataclasses.asdict(NormalisedReading)``
        # so the key is ``temperature_c`` not ``temperature``. See the
        # 2026-05-31 incident note in tests/test_effectors_dispatch.py.
        temp = reading.get("temperature_c")
        target = rules.get("target")
        if temp is None or target is None:
            return False
        return float(temp) > float(target)

    def evaluate(self, reading: dict, rules: dict) -> dict:
        """Rich decision shape for the side-panel "Why?" surface."""
        temp = reading.get("temperature_c")  # See should_be_on comment.
        target = rules.get("target")
        reasons: list[dict] = []
        if temp is None:
            reasons.append({
                "rule":   "TargetTempRule",
                "fired":  False,
                "detail": "No temperature reading available",
            })
        elif target is None:
            reasons.append({
                "rule":   "TargetTempRule",
                "fired":  False,
                "detail": "No target temperature configured",
            })
        else:
            temp_f = float(temp)
            target_f = float(target)
            fired = temp_f > target_f
            if fired:
                detail = f"{temp_f:.1f}°C > {target_f}°C target"
            else:
                detail = f"{temp_f:.1f}°C ≤ {target_f}°C target"
            reasons.append({
                "rule":   "TargetTempRule",
                "fired":  fired,
                "detail": detail,
            })
        decision_on = any(r["fired"] for r in reasons)
        return {
            "decision":     "on" if decision_on else "off",
            "evaluated_at": datetime.utcnow().isoformat(),
            "reasons":      reasons,
        }

    @classmethod
    def compatible_scopes(cls) -> set[Scope]:
        return {Scope.HUB}
