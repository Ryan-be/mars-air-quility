"""Adafruit STEMMA Soil Sensor (Seesaw chip) — capacitive moisture + temp.

I2C address default 0x36 (selectable to 0x37/0x38/0x39 via solder jumpers).
Reports two channels: soil_moisture (raw 200-2000) and soil_temp_c.
"""
import logging
from mlss_grow.sensors.base import Sensor

log = logging.getLogger(__name__)

# Imported lazily so dev laptops without adafruit-circuitpython-seesaw can
# still import this module (and tests can monkeypatch).
try:
    from adafruit_seesaw import seesaw as _seesaw_module
except ImportError:
    _seesaw_module = None

I2C_ADDRESS = 0x36
SANE_RAW_MIN = 200
SANE_RAW_MAX = 2000


class SeesawSoilSensor(Sensor):
    def __init__(self, driver) -> None:
        super().__init__()
        self._driver = driver

    @classmethod
    def detect(cls, i2c_bus) -> "SeesawSoilSensor | None":
        if _seesaw_module is None:
            log.debug("adafruit_seesaw lib not installed; skipping detect")
            return None
        try:
            drv = _seesaw_module.Seesaw(i2c_bus, addr=I2C_ADDRESS)
            return cls(driver=drv)
        except (OSError, ValueError) as exc:
            log.debug("Seesaw not detected at 0x%02x: %s", I2C_ADDRESS, exc)
            return None

    def channels(self) -> list[str]:
        return ["soil_moisture", "soil_temp_c"]

    def read(self) -> dict[str, float]:
        try:
            raw = self._driver.moisture_read()
            temp = self._driver.get_temp()
        except Exception as exc:
            log.warning("Seesaw read failed: %s", exc)
            self.record_bad_read()
            return {}

        if raw < SANE_RAW_MIN or raw > SANE_RAW_MAX:
            log.warning("Seesaw raw %d out of sane range [%d, %d]",
                        raw, SANE_RAW_MIN, SANE_RAW_MAX)
            self.record_bad_read()
            return {}

        self.record_good_read()
        return {"soil_moisture": raw, "soil_temp_c": round(temp, 2)}
