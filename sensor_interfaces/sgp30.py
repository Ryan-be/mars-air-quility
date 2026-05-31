import board
import busio
import time
from adafruit_sgp30 import Adafruit_SGP30

try:
    i2c = busio.I2C(board.SCL, board.SDA)
    sgp30 = Adafruit_SGP30(i2c)
    sgp30.iaq_init()
    for _ in range(15):  # Baseline warm-up
        sgp30.iaq_measure()
        time.sleep(1)
except (OSError, ValueError) as e:
    print(f"Error initializing SGP30 sensor: {e}")
    sgp30 = None


def read_sgp30():
    """Return ``(eco2, tvoc)`` — always a 2-tuple.

    Both sad paths (sensor never initialised + per-read exception)
    previously returned ``(None, None, None, None)`` — probably a
    leftover from an older version of the function that also
    returned humidity / temperature fields. The caller
    (:class:`mlss_monitor.data_sources.sgp30_source.SGP30Source`)
    unpacks the return as ``eco2, tvoc = read_sgp30()``, which raised
    ``ValueError: too many values to unpack (expected 2)`` once per
    second on the production hub the moment the sensor wasn't
    present (2026-05-31 incident). Symmetric 2-tuple returns let
    the caller's unpack succeed and the DataSource degrade
    gracefully into a None-valued NormalisedReading.
    """
    if sgp30 is None:
        print("SGP30 sensor is not available.")
        return None, None
    try:
        eco2, tvoc = sgp30.eCO2, sgp30.TVOC
        return eco2, tvoc
    except Exception as e:
        print(f"Error reading from SGP30 sensor: {e}")
        return None, None
