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

from mlss_monitor.effectors.base import EffectorController, Scope


class AC(EffectorController):
    """Air-conditioner controller — ON when temp > target."""

    effector_type = "ac"

    def should_be_on(self, reading: dict, rules: dict) -> bool:
        temp = reading.get("temperature")
        target = rules.get("target")
        if temp is None or target is None:
            return False
        return float(temp) > float(target)

    @classmethod
    def compatible_scopes(cls) -> set[Scope]:
        return {Scope.HUB}
