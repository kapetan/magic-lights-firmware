from machine import WDT
from micropython import const
from magic_lights import main

_WATCHDOG_TIMEOUT_MS = const(5000)

wdt = WDT(timeout=_WATCHDOG_TIMEOUT_MS)
main(wdt)
