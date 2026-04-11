import board
import busio
from adafruit_bmp280 import Adafruit_BMP280_I2C

i2c = busio.I2C(board.SCL, board.SDA)
bmp280 = Adafruit_BMP280_I2C(i2c)


def read_bmp280():
    pressure = bmp280.pressure
    return round(pressure, 2)