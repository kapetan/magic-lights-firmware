import sys
import time
from machine import Pin
from micropython import const
from .version import __version__

_PIN_LED = const(25)

def main_unsafe(wdt):
    print('Hello', __version__)

def main(wdt):
    try:
        main_unsafe(wdt)
    except KeyboardInterrupt:
        pass
    except BaseException as e:
        sys.print_exception(e)

        led = Pin(_PIN_LED, Pin.OUT)

        while True:
            led.toggle()
            time.sleep_ms(500)
