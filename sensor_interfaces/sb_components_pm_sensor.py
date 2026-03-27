import serial


class AirMonitoringHAT_PM:
    """
    Simple interface for SB Components Air Monitoring HAT.
    Reads PM1.0, PM2.5, and PM10 values via UART (PMSA003 / PMS5003).
    """

    def __init__(self, port="/dev/ttyS0", baudrate=9600, timeout=2):
        self.ser = serial.Serial(port, baudrate, timeout=timeout)

    def read_pm(self):
        """Reads particulate matter values and returns a dict, or None if no valid data."""
        data = self.ser.read(32)
        if len(data) < 32:
            return None

        # Frame starts with 0x42 0x4D
        if data[0] == 0x42 and data[1] == 0x4D:
            pm1_0 = (data[10] << 8) | data[11]
            pm2_5 = (data[12] << 8) | data[13]
            pm10 = (data[14] << 8) | data[15]

            return {
                "pm1_0": pm1_0,
                "pm2_5": pm2_5,
                "pm10": pm10
            }
        return None
