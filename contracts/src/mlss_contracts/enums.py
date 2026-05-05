"""Enumerations shared between MLSS server and grow unit firmware."""
from enum import Enum


class Channel(str, Enum):
    """Sensor and actuator channels a unit can declare in its capabilities.

    REQUIRED channels (every unit must report these): SOIL_MOISTURE, LIGHT,
    PUMP, CAMERA. All others are optional and only present if the unit has
    the corresponding hardware.
    """
    # Required
    SOIL_MOISTURE = "soil_moisture"
    LIGHT = "light"
    PUMP = "pump"
    CAMERA = "camera"
    # Optional
    SOIL_TEMP_C = "soil_temp_c"
    AMBIENT_LUX = "ambient_lux"
    AIR_TEMP_C = "air_temp_c"
    AIR_HUMIDITY_PCT = "air_humidity_pct"
    RESERVOIR_LEVEL_PCT = "reservoir_level_pct"


class Phase(str, Enum):
    SEEDLING = "seedling"
    VEGETATIVE = "vegetative"
    FLOWERING = "flowering"
    FRUITING = "fruiting"
    DORMANT = "dormant"


class MediumType(str, Enum):
    SOIL = "soil"
    COCO = "coco"
    ROCKWOOL = "rockwool"
    CUSTOM = "custom"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class EventKind(str, Enum):
    WATERING_PULSE = "watering_pulse"
    SENSOR_DEGRADED = "sensor_degraded"
    SENSOR_RECOVERED = "sensor_recovered"
    CONFIG_APPLIED = "config_applied"
    IDENTIFY_COMPLETE = "identify_complete"
    SAFETY_CAP_HIT = "safety_cap_hit"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    BUFFER_REPLAY_STARTED = "buffer_replay_started"
    BUFFER_REPLAY_COMPLETE = "buffer_replay_complete"


class CommandName(str, Enum):
    IDENTIFY = "identify"
    WATER_NOW = "water_now"
    LIGHT_OVERRIDE = "light_override"
    SNAP_PHOTO = "snap_photo"
    RELOAD_CONFIG = "reload_config"
    REBOOT = "reboot"
