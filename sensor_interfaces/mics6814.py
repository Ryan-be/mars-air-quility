"""
Pimoroni MICS6814 3-in-1 Gas Sensor interface (I2C).

Measures:
  - CO  (carbon monoxide) -- reducing gas
  - NO2 (nitrogen dioxide) -- oxidising gas
  - NH3 (ammonia)          -- reducing gas

The sensor is on the I2C bus (default address 0x04) and uses the
pimoroni_mics6814 library.  All reads are wrapped defensively so the
monitor continues to operate when the sensor is absent or faulty.
"""

import logging

log = logging.getLogger(__name__)

_sensor = None


def init_mics6814():
    """Attempt to initialise the MICS6814 sensor.

    Returns the sensor object on success, or None if the hardware is
    not present or the library is unavailable.
    """
    global _sensor
    try:
        from pimoroni_mics6814 import Mics6814
        _sensor = Mics6814()
        # Do a test read to confirm the sensor is responding
        reading = _sensor.read_all()
        log.info(
            "MICS6814 gas sensor initialised: CO=%.2f, NO2=%.2f, NH3=%.2f",
            reading.reducing, reading.oxidising, reading.nh3,
        )
        return _sensor
    except ImportError:
        log.warning("pimoroni_mics6814 library not installed -- gas sensor disabled")
        _sensor = None
        return None
    except (OSError, ValueError) as exc:
        log.error("Failed to initialise MICS6814 sensor: %s", exc)
        _sensor = None
        return None
    except Exception as exc:
        log.error("Unexpected error initialising MICS6814 sensor: %s", exc)
        _sensor = None
        return None


def read_mics6814():
    """Read CO, NO2 and NH3 from the MICS6814 sensor.

    Returns:
        tuple: (co, no2, nh3) as floats rounded to 2 dp, or
               (None, None, None) when the sensor is unavailable.
    """
    if _sensor is None:
        return None, None, None
    try:
        reading = _sensor.read_all()
        co = round(reading.reducing, 2)
        no2 = round(reading.oxidising, 2)
        nh3 = round(reading.nh3, 2)
        return co, no2, nh3
    except Exception as exc:
        log.error("Error reading MICS6814 sensor: %s", exc)
        return None, None, None
