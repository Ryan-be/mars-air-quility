import board
import digitalio
from PIL import Image, ImageDraw, ImageFont
import adafruit_rgb_display.st7735 as st7735
import busio

# Configuration for CS, DC, and Reset pins
cs_pin = digitalio.DigitalInOut(board.CE0)
dc_pin = digitalio.DigitalInOut(board.D25)
reset_pin = digitalio.DigitalInOut(board.D24)

# Setup SPI bus
spi = busio.SPI(clock=board.SCK, MOSI=board.MOSI)

# Initialize display (rotation may need to change for your orientation)
disp = st7735.ST7735R(spi, rotation=90, cs=cs_pin, dc=dc_pin, rst=reset_pin, baudrate=24000000)
width = disp.width
height = disp.height

# Load default font
font = ImageFont.load_default()


def update_display(temp, hum, eco2, tvoc):
    image = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(image)

    draw.text((10, 5), "MLSS MONITOR", font=font, fill="cyan")
    draw.text((10, 25), f"Temp: {temp} C", font=font, fill="white")
    draw.text((10, 45), f"Humidity: {hum}%", font=font, fill="white")
    draw.text((10, 65), f"eCO2: {eco2} ppm", font=font, fill="white")
    draw.text((10, 85), f"TVOC: {tvoc} ppb", font=font, fill="white")

    disp.image(image)
