"""Shared pydantic validators used across schemas + server-side payloads.

Lives in the contracts package because pydantic validators belong with
the schemas; server-side payload models (e.g. _ProfileUpdate in
api_grow_settings.py) import the same factory so cross-field rules
stay in one place rather than diverging across modules.
"""
from pydantic import model_validator


def make_min_le_max_validator(min_field: str, max_field: str):
    """Returns a pydantic v2 model_validator that asserts
    `<min_field> <= <max_field>` if both are provided.

    Use as:
        class Foo(BaseModel):
            min_pulse_s: Optional[float] = None
            max_pulse_s: Optional[float] = None
            _min_le_max = make_min_le_max_validator("min_pulse_s", "max_pulse_s")

    The factory shape is needed because pydantic v2 model_validators
    close over field names at class-definition time; a single shared
    validator function couldn't introspect different field names per
    model. Factory returns a fresh closure per (min_field, max_field)
    pair so each model gets its own properly-scoped check.
    """
    @model_validator(mode="after")
    def _check(self):
        min_value = getattr(self, min_field, None)
        max_value = getattr(self, max_field, None)
        if (
            min_value is not None
            and max_value is not None
            and min_value > max_value
        ):
            raise ValueError(
                f"{min_field} must be <= {max_field}"
            )
        return self
    return _check
