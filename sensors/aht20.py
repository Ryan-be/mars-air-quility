import board
import busio
from adafruit_ahtx0 import AHTx0

i2c = busio.I2C(board.SCL, board.SDA)
sensor = AHTx0(i2c)

def read_aht20():
    return round(sensor.temperature, 2), round(sensor.relative_humidity, 2)
