"""static/css/grow.css: must define styles for the stat-tile component.

The Live-readings panel renders one stat-tile per capability via
static/js/grow/components/stat-tile.mjs, which emits elements with
classes ``du-stat-grid`` (container), ``du-stat`` (per-tile),
``du-stat .v`` (big value), ``du-stat .l`` (label), and optional
``cap-health-pill`` next to the label.

These class names were referenced from the JS since the first
implementation, but CSS rules for them were never added to grow.css —
the result was an unstyled fallback: vertical stacking, no grid
columns, no big-number/small-label visual hierarchy. From a screenshot
the tiles looked like plain text lists instead of dashboard tiles.

This test locks in the presence of all the rules a future refactor
might accidentally delete. It does NOT assert specific values (color,
spacing, font-size) because that's design-tweak territory — only that
the selectors exist with at least one declaration block.
"""
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GROW_CSS = REPO_ROOT / "static" / "css" / "grow.css"


def _has_rule(text: str, selector: str) -> bool:
    """Return True if `selector` appears as a CSS rule (selector { ... })
    rather than as a comment-only reference. Cheap regex — matches the
    selector followed by optional whitespace + ``{``."""
    pattern = re.escape(selector) + r"\s*[,{]"
    return re.search(pattern, text) is not None


def test_stat_tile_grid_is_styled():
    text = GROW_CSS.read_text(encoding="utf-8")
    assert _has_rule(text, ".du-stat-grid"), (
        "grow.css must define a .du-stat-grid rule (the grid container "
        "wrapping the Live-readings tiles). Without it tiles stack "
        "vertically with no columns — see screenshot evidence in commit "
        "message of this commit."
    )


def test_stat_tile_individual_is_styled():
    text = GROW_CSS.read_text(encoding="utf-8")
    assert _has_rule(text, ".du-stat"), (
        "grow.css must define a .du-stat rule (the individual stat-tile). "
        "Without it the value + label render as plain stacked text."
    )


def test_stat_tile_value_class_is_styled():
    text = GROW_CSS.read_text(encoding="utf-8")
    assert _has_rule(text, ".du-stat .v"), (
        "grow.css must style .du-stat .v (the big-number value). Without "
        "it the value is body-size text indistinguishable from the label."
    )


def test_stat_tile_label_class_is_styled():
    text = GROW_CSS.read_text(encoding="utf-8")
    assert _has_rule(text, ".du-stat .l"), (
        "grow.css must style .du-stat .l (the small label below the value)."
    )


def test_cap_health_pill_is_styled():
    text = GROW_CSS.read_text(encoding="utf-8")
    # The stat-tile JS appends one of these when capability health is
    # untested / unresponsive / no_hardware. The pill is meaningless
    # without a contrasting background / colour rule.
    assert _has_rule(text, ".cap-health-pill"), (
        "grow.css must style .cap-health-pill (the health badge appended "
        "to a tile's label when the capability isn't reporting cleanly)."
    )


def test_plant_happiness_classes_styled():
    """The 3 happy-* CSS rules drive the border-colour of soil_temp /
    soil_moisture tiles when the unit reports a plant-happiness zone.
    Without these rules the tile renders with the default
    required/optional-marker colour and the operator gets no visual
    feedback on whether the reading is ideal / tolerated / critical."""
    text = GROW_CSS.read_text(encoding="utf-8")
    for selector in (".du-stat.happy-ideal",
                     ".du-stat.happy-tolerated",
                     ".du-stat.happy-critical"):
        assert _has_rule(text, selector), (
            f"grow.css must define {selector} for plant-happiness border-colour."
        )
    # The ideal_range subtext sits beneath the label; small muted text.
    assert _has_rule(text, ".du-stat .happy-range"), (
        "grow.css must style .du-stat .happy-range (the ideal-range subtext)."
    )
