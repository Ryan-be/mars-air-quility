import board
import busio

_sensor = None

def _get_sensor():
    global _sensor
    if _sensor is None:
        from adafruit_bmp280 import Adafruit_BMP280
        i2c = busio.I2C(board.SCL, board.SDA)
        _sensor = Adafruit_BMP280(i2c)
    return _sensor


def read_bmp280():
    try:
        sensor = _get_sensor()
        return round(sensor.pressure, 1)
    except Exception:
        return None