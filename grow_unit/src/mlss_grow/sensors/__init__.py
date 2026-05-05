"""Plant Grow Unit sensor implementations.

REGISTERED_SENSORS is the auto-detect registry — `auto_detect(i2c)` walks
this list, calls each class's `.detect(i2c)`, and returns the instances
that succeeded. Add a new sensor by writing a Sensor subclass and adding
it to REGISTERED_SENSORS.
"""
from mlss_grow.sensors.base import Sensor
from mlss_grow.sensors.seesaw import SeesawSoilSensor

REGISTERED_SENSORS: list[type[Sensor]] = [
    SeesawSoilSensor,
]


def auto_detect(i2c_bus) -> list[Sensor]:
    """Probe each registered sensor class against the I2C bus; return survivors."""
    found = []
    for cls in REGISTERED_SENSORS:
        instance = cls.detect(i2c_bus)
        if instance is not None:
            found.append(instance)
    return found
