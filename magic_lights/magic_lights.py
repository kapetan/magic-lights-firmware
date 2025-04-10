import sys
import time
import asyncio
import network
import errno
import json
from collection import namedtuple
from machine import Pin
from micropython import const
from neopixel import NeoPixel
from microdot import Microdot, send_file
from microdot.websocket import with_websocket
from . import pacman

_PIN_PIXELS = const(15)
_PIN_LED = const('LED')

_PIXEL_COUNT = const(240)

_WLAN_CONNECT_TIMEOUT_MS = const(30000)

# https://github.com/adafruit/Adafruit_NeoPixel/blob/master/Adafruit_NeoPixel.h
# https://github.com/adafruit/Adafruit_NeoPixel/blob/master/Adafruit_NeoPixel.cpp
_GAMMA_8 = bytes((
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01,
    0x01, 0x01, 0x01, 0x01, 0x02, 0x02, 0x02, 0x02,
    0x02, 0x02, 0x02, 0x02, 0x03, 0x03, 0x03, 0x03,
    0x03, 0x03, 0x04, 0x04, 0x04, 0x04, 0x05, 0x05,
    0x05, 0x05, 0x05, 0x06, 0x06, 0x06, 0x06, 0x07,
    0x07, 0x07, 0x08, 0x08, 0x08, 0x09, 0x09, 0x09,
    0x0a, 0x0a, 0x0a, 0x0b, 0x0b, 0x0b, 0x0c, 0x0c,
    0x0d, 0x0d, 0x0d, 0x0e, 0x0e, 0x0f, 0x0f, 0x10,
    0x10, 0x11, 0x11, 0x12, 0x12, 0x13, 0x13, 0x14,
    0x14, 0x15, 0x15, 0x16, 0x16, 0x17, 0x18, 0x18,
    0x19, 0x19, 0x1a, 0x1b, 0x1b, 0x1c, 0x1d, 0x1d,
    0x1e, 0x1f, 0x1f, 0x20, 0x21, 0x22, 0x22, 0x23,
    0x24, 0x25, 0x26, 0x26, 0x27, 0x28, 0x29, 0x2a,
    0x2a, 0x2b, 0x2c, 0x2d, 0x2e, 0x2f, 0x30, 0x31,
    0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39,
    0x3a, 0x3b, 0x3c, 0x3d, 0x3e, 0x3f, 0x40, 0x41,
    0x42, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4b,
    0x4c, 0x4d, 0x4e, 0x50, 0x51, 0x52, 0x54, 0x55,
    0x56, 0x58, 0x59, 0x5a, 0x5c, 0x5d, 0x5e, 0x60,
    0x61, 0x63, 0x64, 0x66, 0x67, 0x69, 0x6a, 0x6c,
    0x6d, 0x6f, 0x70, 0x72, 0x73, 0x75, 0x77, 0x78,
    0x7a, 0x7c, 0x7d, 0x7f, 0x81, 0x82, 0x84, 0x86,
    0x88, 0x89, 0x8b, 0x8d, 0x8f, 0x91, 0x92, 0x94,
    0x96, 0x98, 0x9a, 0x9c, 0x9e, 0xa0, 0xa2, 0xa4,
    0xa6, 0xa8, 0xaa, 0xac, 0xae, 0xb0, 0xb2, 0xb4,
    0xb6, 0xb8, 0xba, 0xbc, 0xbf, 0xc1, 0xc3, 0xc5,
    0xc7, 0xca, 0xcc, 0xce, 0xd1, 0xd3, 0xd5, 0xd7,
    0xda, 0xdc, 0xdf, 0xe1, 0xe3, 0xe6, 0xe8, 0xeb,
    0xed, 0xf0, 0xf2, 0xf5, 0xf7, 0xfa, 0xfc, 0xff
))

def hsv_to_rgb(hue, saturation, value):
    red = green = blue = 0
    hue = (hue * 1530 + 32768) // 65536

    if hue < 510:
        blue = 0

        if hue < 255:
            red = 255
            green = hue
        else:
            red = 510 - hue
            green = 255
    elif hue < 1020:
        red = 0

        if hue < 765:
            green = 255
            blue = hue - 510
        else:
            green = 1020 - hue
            blue = 255
    elif hue < 1530:
        green = 0

        if hue < 1275:
            red = hue - 1020
            blue = 255
        else:
            red = 255
            blue = 1530 - hue
    else:
        red = 255
        green = blue = 0

    v1 = 1 + value
    s1 = 1 + saturation
    s2 = 255 - saturation

    red = (((((red * s1) >> 8) + s2) * v1) & 0xff00) >> 8
    green = (((((green * s1) >> 8) + s2) * v1) & 0xff00) >> 8
    blue = ((((blue * s1) >> 8) + s2) * v1) >> 8

    return (red, green, blue)

def gamma8(rgb):
    return tuple(_GAMMA_8[component] for component in rgb)

def brightness(rgb, brightness):
    brightness = (brightness + 1) & 0xff

    if brightness:
        return tuple((component * brightness) >> 8 for component in rgb)

    return rgb

DisplayTuple = namedtuple('DisplayTuple', ('rainbow', 'pacman'))

# https://learn.adafruit.com/black-lives-matter-badge/arduino-neopixel-rainbow
class RainbowDisplay:
    def __init__(self, pixels, repetitions=1, saturation=255, brightness=255, wait_ms=10):
        self.pixels = pixels
        self.repetitions = repetitions
        self.saturation = saturation
        self.brightness = brightness
        self.wait_ms = wait_ms

    async def run(self):
        while True:
            for first_hue in range(0, 65536, 256):
                pixel_length = len(self.pixels)

                for i in range(pixel_length):
                    hue = (first_hue + (i * self.repetitions * 65536) // pixel_length) & 0xffff
                    color = hsv_to_rgb(hue, self.saturation, self.brightness)
                    color = gamma8(color)
                    color = brightness(color, 50)
                    self.pixels[i] = color

                self.pixels.write()
                await asyncio.sleep_ms(self.wait_ms)

class PacmanDisplay:
    _COLOR_BLACK = (0, 0, 0)
    _COLOR_POWER_PELLET = (255, 255, 255)
    _COLOR_DOT = (255, 184, 153)
    _COLOR_PLAYER = (255, 255, 0)
    _COLOR_GHOST = (255, 0, 0)
    _COLOR_GHOST_VULNERABLE = (30, 35, 250)
    _COLOR_GHOST_RECOVERY = (150, 150, 150)
    _COLOR_GHOST_DEAD = brightness(_COLOR_GHOST_VULNERABLE, 50)

    class Toggle:
        def __init__(self, frequency_ms):
            self.frequency_ms = frequency_ms
            self.elapsed_ms = 0
            self.state = 0

        def update(self, dt):
            self.elapsed_ms += dt

            if self.elapsed_ms >= self.frequency_ms:
                self.state = not self.state
                self.elapsed_ms = 0

    def __init__(self, pixels):
        self.pixels = pixels
        self.engine = pacman.Engine(pacman.Bounds(0, len(pixels) - 1))

    def change_player_direction(self):
        self.engine.player.change_direction()

    async def run(self):
        pixel_length = len(self.pixels)
        power_pellet_toggle = self.Toggle(100)
        ghost_toggle = self.Toggle(200)

        for delta_ms in self.engine.loop():
            for i in range(pixel_length):
                self.pixels[i] = self._COLOR_BLACK

            for body in self.engine.bodies():
                if isinstance(body, pacman.Dot):
                    color = self._COLOR_DOT
                elif isinstance(body, pacman.PowerPellet):
                    power_pellet_toggle.update(delta_ms)
                    color = self._COLOR_POWER_PELLET if power_pellet_toggle.state else self._COLOR_BLACK
                elif isinstance(body, pacman.Player):
                    color = self._COLOR_PLAYER
                elif isinstance(body, pacman.Ghost):
                    if body.dead:
                        color = self._COLOR_GHOST_DEAD
                    elif body.vulnerable:
                        if body.vulnerable <= 2000:
                            ghost_toggle.update(delta_ms)
                            color = self._COLOR_GHOST_VULNERABLE if ghost_toggle.state else self._COLOR_GHOST_RECOVERY
                        else:
                            color = self._COLOR_GHOST_VULNERABLE
                    else:
                        color = self._COLOR_GHOST

                self.pixels[body.position] = gamma8(color)

            self.pixels.write()
            await asyncio.sleep_ms(16)

class Controller:
    def __init__(self, displays):
        self.displays = displays
        self.brightness = 127
        self.task = None

    def create_wireless_network_access_point(self, ssid, password):
        wlan = network.WLAN(network.WLAN.IF_AP)

        wlan.active(False)
        wlan.config(ssid=ssid, key=password, security=3)
        wlan.active(True)

        return wlan

    async def connect_wireless_network_station(self, ssid, password):
        wlan = network.WLAN(network.WLAN.IF_STA)

        wlan.active(False)
        wlan.active(True)
        wlan.connect(ssid, password)

        now_ms = time.ticks_ms()
        deadline_ms = time.ticks_add(now_ms, _WLAN_CONNECT_TIMEOUT_MS)

        try:
            while time.ticks_diff(deadline_ms, now_ms) > 0:
                if wlan.isconnected():
                    break

                await asyncio.sleep_ms(100)
                now_ms = time.ticks_ms()
            else:
                raise OSError(errno.ETIMEDOUT)
        except:
            try:
                wlan.disconnect()
            except OSError:
                # Might not have been connected
                pass

            wlan.active(False)

        return wlan

    async def run(self):
        network.hostname('magic-lights')
        wlan = await self.connect_wireless_network_station('virus-3dacf5-7', 'hdx44dbd')

        print('ip', wlan.ifconfig())

        app = Microdot()

        @app.route('/')
        def index(request):
            return send_file('/web/index.html')

        @app.route('/bundle.js')
        def bundle(request):
            return send_file('/web/bundle.js.gz', content_type='text/javascript; charset=utf-8', compressed=True)

        @app.route('/ws')
        @with_websocket
        async def ws(request, ws):
            nonlocal wlan

            await ws.send(json.dumps(dict(
                display=None,
                brightness=self.brightness,
                displays=[
                    dict(
                        name='rainbow',
                        repetitions=self.displays.rainbow.repetitions,
                        saturation=self.displays.rainbow.saturation,
                        brightness=self.displays.rainbow.brightness,
                        wait_ms=self.displays.rainbow.wait_ms),
                    dict(name='pacman')])))

            while True:
                message = await ws.receive()
                message = json.loads(message)

                if isinstance(message, (list, tuple)) and len(message) > 1:
                    action, *args = message

                    if action == 'run_display':
                        name, = args
                        display = None

                        if name == 'rainbow':
                            display = self.displays.rainbow
                        elif name == 'pacman':
                            display = self.displays.pacman

                        if display:
                            if self.task:
                                self.task.cancel()

                                try:
                                    await self.task
                                except asyncio.CancelledError:
                                    pass

                                self.task = None

                            self.task = asyncio.create_task(display.run())
                    elif action == 'brightness':
                        value, = args
                        self.brightness = value
                    elif action == 'create_wireless_network_access_point':
                        ssid, password = args
                        wlan = self.create_wireless_network_access_point(ssid, password)
                    elif action == 'connect_wireless_network_station':
                        ssid, password = args
                        self.connect_wireless_network_station(ssid, password)
                    elif action == 'displays.rainbow.repetitions':
                        value, = args
                        self.displays.rainbow.repetitions = value
                    elif action == 'displays.rainbow.saturation':
                        value, = args
                        self.displays.rainbow.saturation = value
                    elif action == 'displays.rainbow.brightness':
                        value, = args
                        self.displays.rainbow.brightness = value
                    elif action == 'displays.rainbow.wait_ms':
                        value, = args
                        self.displays.rainbow.wait_ms = value
                    elif action == 'displays.pacman.change_player_direction':
                        self.pacman.change_player_direction()

        self.task = asyncio.create_task(self.displays.rainbow.run())
        await app.start_server(port=80)

def run_safe(coro):
    try:
        loop = asyncio.get_event_loop()
        task = loop.create_task(coro)

        try:
            loop.run_until_complete(task)
        finally:
            if not task.done():
                asyncio.core.cur_task = None
                task.cancel()

                try:
                    loop.run_until_complete(task)
                except BaseException as e:
                    sys.print_exception(e)

            asyncio.new_event_loop()
    except KeyboardInterrupt:
        pass
    except BaseException as e:
        sys.print_exception(e)

        led = Pin(_PIN_LED, Pin.OUT)

        while True:
            led.toggle()
            time.sleep_ms(500)

def main(wdt):
    led = Pin(_PIN_LED, Pin.OUT)
    pixels = NeoPixel(Pin(_PIN_PIXELS), _PIXEL_COUNT)

    led.on()

    controller = Controller(DisplayTuple(RainbowDisplay(), PacmanDisplay()))
    # display = PacmanDisplay(pixels)

    run_safe(controller.run())

    # engine = pacman.Engine(pacman.Bounds(0, 100))
    # renderer = pacman.TerminalRenderer(engine, sys.stdout)

    # renderer.render()

    # async def _main():
    #     task = asyncio.create_task(engine.run())

    #     try:
    #         for i in range(256):
    #             renderer.render()
    #             await asyncio.sleep_ms(16)
    #     finally:
    #         task.cancel()

    #         try:
    #             await task
    #         except asyncio.CancelledError:
    #             pass

    # run_safe(_main())

    # display = RainbowDisplay(pixels)

    # run_safe(display.run())

    # for i in range(_PIXEL_COUNT):
    #     pixels[i] = (255, 0, 0)

    # pixels.write()
