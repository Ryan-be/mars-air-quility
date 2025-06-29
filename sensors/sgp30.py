import board
import busio
import time
from adafruit_sgp30 import Adafruit_SGP30

i2c = busio.I2C(board.SCL, board.SDA)
sgp30 = Adafruit_SGP30(i2c)

sgp30.iaq_init()
for _ in range(15):  # Baseline warm-up
    sgp30.iaq_measure()
    time.sleep(1)

def read_sgp30():
    return sgp30.eCO2, sgp30.TVOC