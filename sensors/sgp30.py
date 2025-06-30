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
    if sgp30 is None:
        print("SGP30 sensor is not available.")
        return None, None
    try:
        return sgp30.eCO2, sgp30.TVOC
    except Exception as e:
        print(f"Error reading from SGP30 sensor: {e}")
        return None, None