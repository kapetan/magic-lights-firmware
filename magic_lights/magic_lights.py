import sys
import time
import asyncio
import network
import errno
import json
import struct
from collections import namedtuple
from machine import Pin
from micropython import const
from neopixel import NeoPixel
from microdot import Microdot, send_file
from microdot.websocket import with_websocket
from . import pacman
from . import firework
from . import fs
from .engine_utils import Bounds
from .color_utils import hsv_to_rgb, gamma8, brightness, native_brightness_gamma8
from .atomic_file import AtomicFile

_PIN_PIXELS = const(15)
_PIN_LED = const('LED')

_PIXEL_COUNT = const(240)

_WLAN_CONNECT_TIMEOUT_MS = const(30000)

_SETTINGS_PATH = const('/config.json')

_DEFAULT_BRIGHTNESS = const(127)
_DEFAULT_SSID = const('magic-lights')
_DEFAULT_KEY = const('magic-lights')

_ALIGNMENT_START = const('start')
_ALIGNMENT_CENTER = const('center')
_ALIGNMENT_END = const('end')

def format_security(security):
    if security == 0:
        return 'OPEN'
    elif security == 1:
        return 'WEP'
    elif security == 2:
        return 'WPA-PSK'
    elif security == 3:
        return 'WPA2-PSK'
    elif security == 4:
        return 'WPA/WPA2-PSK'

def format_client_address(addr):
    ip, port = addr
    return f'{ip}:{port}'

def format_interface(interface):
    return 'AP' if interface == network.WLAN.IF_AP else 'STA'

def format_mac_address(mac_add):
    return ':'.join(f'{x:02x}' for x in mac_add)

def format_alignment(alignment):
    if alignment == _ALIGNMENT_START:
        return 'start'
    elif alignment == _ALIGNMENT_CENTER:
        return 'center'
    elif alignment == _ALIGNMENT_END:
        return 'end'

def parse_alignment(value):
    if value == 'start':
        return _ALIGNMENT_START
    elif value == 'center':
        return _ALIGNMENT_CENTER
    elif value == 'end':
        return _ALIGNMENT_END

    return _ALIGNMENT_CENTER

def log_message(*message):
    year, month, day, week_day, hour, minute, second, subseconds = time.localtime()
    sys.stderr.write(f'[{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}]:')

    for part in message:
        sys.stderr.write(' ')
        sys.stderr.write(part)

    sys.stderr.write('\n')

def wlan_reset(wlan):
    wlan.active(False)
    wlan.active(True)

class Timer:
    counter = 0

    @classmethod
    def increase(cls):
        current = cls.counter
        cls.counter += 1
        return current

    def __init__(self, message=None):
        self.started = 0
        self.message = message

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop(self.message)
        return False

    def start(self):
        self.started = time.ticks_ms()

    def stop(self, message=None):
        now = time.ticks_ms()
        delta = time.ticks_diff(now, self.started)

        if not message:
            message = f'Timer {self.increase()}'

        print(message, f'{delta}ms')

DisplayTuple = namedtuple('DisplayTuple', ('rainbow', 'pacman', 'byte', 'firework'))

WirelessNetwork = namedtuple('WirelessNetwork', ('interface', 'ssid', 'key'))

_SettingsStruct = namedtuple('SettingsStruct', (
    'version',
    'brightness',
    'interface',
    'ssid_length',
    'ssid_buffer',
    'key_length',
    'key_buffer'))

class SettingsStruct(_SettingsStruct):
    FORMAT = '>BBBB32sB64s'
    BUFFER = bytearray(struct.calcsize(FORMAT))

    @classmethod
    def unpack(cls, buffer, offset=0):
        values = struct.unpack_from(cls.FORMAT, buffer, offset)
        return cls(*values)

    @classmethod
    def read(cls, name):
        with open(name, 'rb') as file:
            read = file.readinto(cls.BUFFER)

            if read < len(cls.BUFFER):
                raise OSError('not enough data read')

            return cls.unpack(cls.BUFFER)

    @property
    def ssid(self):
        return self.ssid_buffer[0:self.ssid_length]

    @property
    def key(self):
        return self.key_buffer[0:self.key_length]

    def pack(self, buffer, offset=0):
        struct.pack_into(self.FORMAT, buffer, offset, *self)

    def write(self, name):
        self.pack(self.BUFFER)

        with AtomicFile.open(name, 'w+b') as file:
            file.write(self.BUFFER)

class Settings:
    @classmethod
    def read(cls, name):
        try:
            struct = SettingsStruct.read(name)
            return cls(
                struct.brightness,
                struct.interface,
                struct.ssid,
                struct.key)
        except OSError:
            return cls(
                _DEFAULT_BRIGHTNESS,
                network.WLAN.IF_AP,
                _DEFAULT_SSID,
                _DEFAULT_KEY)

    def __init__(self, brightness, interface, ssid, key):
        self.brightness = brightness
        self.interface = interface
        self.ssid = ssid
        self.key = key

    def write(self, name):
        struct = SettingsStruct(1,
            self.brightness,
            self.interface,
            len(self.ssid),
            self.ssid,
            len(self.key),
            self.key)

        struct.write(name)

class FireworkDisplay:
    _COLOR_BLACK = (0, 0, 0)

    def __init__(self, pixels, settings):
        self.pixels = pixels
        self.settings = settings
        self.engine = None
        self.reset()

    def ignite(self):
        self.engine.ignite()

    def reset(self):
        self.engine = firework.NativeEngine(Bounds(0, len(self.pixels) - 1))

    async def run(self):
        renderer = firework.NativeBaseRenderer(self.engine)
        now_ms = past_ms = time.ticks_ms()

        for _ in self.engine.loop():
            self.pixels.fill(self._COLOR_BLACK)

            for i, color in renderer.render():
                self.pixels[i] = native_brightness_gamma8(color, self.settings.brightness)

            self.pixels.write()

            now_ms = time.ticks_ms()
            delta_ms = time.ticks_diff(now_ms, past_ms)
            await asyncio.sleep_ms(max(16 - delta_ms, 1))
            past_ms = time.ticks_ms()

class ByteDisplay:
    _COLOR_BLACK = (0, 0, 0)
    _COLOR_START_MARKER = (0, 0, 0)
    _COLOR_LOW_BIT = (30, 35, 250)
    _COLOR_HIGH_BIT = (255, 0, 0)

    def __init__(self, pixels, settings, buffer=b'', alignment=_ALIGNMENT_CENTER):
        self.pixels = pixels
        self.settings = settings
        self.buffer = buffer
        self.alignment = alignment

    def reset(self):
        pass

    async def run(self):
        buffer = None
        alignment = None
        _brightness = None
        pixel_length = len(self.pixels)

        while True:
            if buffer != self.buffer or alignment != self.alignment or _brightness != self.settings.brightness:
                buffer = self.buffer
                alignment = self.alignment
                _brightness = self.settings.brightness

                # 8 pixels per byte plus 1 start marker
                buffer_pixel_length = len(buffer) * 9
                start_offset = 0

                if alignment == _ALIGNMENT_END:
                    start_offset = pixel_length - buffer_pixel_length
                elif alignment == _ALIGNMENT_CENTER:
                    start_offset = (pixel_length - buffer_pixel_length) // 2

                if start_offset < 0:
                    start_offset = 0

                for i in range(pixel_length):
                    if start_offset <= i < start_offset + buffer_pixel_length:
                        offset = i - start_offset

                        if offset % 9 == 0:
                            color = self._COLOR_START_MARKER
                        else:
                            buffer_offset = offset // 9
                            bit_offset = offset - (buffer_offset * 9) - 1
                            b = buffer[buffer_offset]

                            if b & (0x80 >> bit_offset):
                                color = self._COLOR_HIGH_BIT
                            else:
                                color = self._COLOR_LOW_BIT
                    else:
                        color = self._COLOR_BLACK

                    self.pixels[i] = brightness(gamma8(color), _brightness)

                self.pixels.write()

            await asyncio.sleep_ms(16)

# https://learn.adafruit.com/black-lives-matter-badge/arduino-neopixel-rainbow
class RainbowDisplay:
    def __init__(self, pixels, settings, repetitions=1, saturation=255, brightness=255, wait_ms=10):
        self.pixels = pixels
        self.settings = settings
        self.repetitions = repetitions
        self.saturation = saturation
        self.brightness = brightness
        self.wait_ms = wait_ms

    def reset(self):
        pass

    async def run(self):
        while True:
            for first_hue in range(0, 65536, 256):
                pixel_length = len(self.pixels)

                for i in range(pixel_length):
                    hue = (first_hue + (i * self.repetitions * 65536) // pixel_length) & 0xffff
                    color = hsv_to_rgb(hue, self.saturation, self.brightness)
                    color = gamma8(color)
                    color = brightness(color, self.settings.brightness)
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

    def __init__(self, pixels, settings):
        self.pixels = pixels
        self.settings = settings
        self.engine = None
        self.reset()

    @property
    def ended(self):
        return self.engine.ended

    def change_player_direction(self):
        self.engine.player.change_direction()

    def reset(self):
        self.engine = pacman.Engine(Bounds(0, len(self.pixels) - 1))

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

                self.pixels[body.position] = brightness(gamma8(color), self.settings.brightness)

            self.pixels.write()
            await asyncio.sleep_ms(16)

class Controller:
    def __init__(self, pixels, displays, settings):
        self.pixels = pixels
        self.displays = displays
        self.settings = settings
        self.task = None

    def create_wireless_network_access_point(self, ssid, key):
        log_message(f'Creating wireless access point with SSID {ssid}.')

        wlan = network.WLAN(network.WLAN.IF_AP)

        wlan.active(False)
        wlan.config(ssid=ssid, key=key, security=network.WLAN.SEC_WPA_WPA2)
        wlan.active(True)

        return wlan

    async def connect_wireless_network_station(self, ssid, key):
        log_message(f'Connecting to wireless network with SSID {ssid}.')

        wlan = network.WLAN(network.WLAN.IF_STA)
        wlan_reset(wlan)
        wlan.connect(ssid, key)

        now_ms = time.ticks_ms()
        deadline_ms = time.ticks_add(now_ms, _WLAN_CONNECT_TIMEOUT_MS)

        try:
            while time.ticks_diff(deadline_ms, now_ms) > 0:
                if wlan.isconnected():
                    break

                await asyncio.sleep_ms(100)
                now_ms = time.ticks_ms()
            else:
                log_message(f'Failed to connect to wireless network with SSID {ssid}.')
                raise OSError(errno.ETIMEDOUT)
        except:
            try:
                wlan.disconnect()
            except OSError:
                # Might not have been connected
                pass

            wlan.active(False)

        return wlan

    async def setup_wireless_network(self, next_network, current_network, wlan=None):
        if wlan:
            wlan.active(False)

        if next_network.interface == network.WLAN.IF_AP:
            if current_network.interface != network.WLAN.IF_STA:
                wlan = network.WLAN(network.WLAN.IF_STA)
                wlan_reset(wlan)

            available_networks = wlan.scan()
            wlan.active(False)
            wlan = self.create_wireless_network_access_point(next_network.ssid, next_network.key)
            wireless_network = next_network
        else:
            wlan = await self.connect_wireless_network_station(next_network.ssid, next_network.key)
            available_networks = wlan.scan()
            wireless_network = next_network

            if not wlan.isconnected() and current_network.interface == network.WLAN.IF_AP:
                # Stay reachable, fall back to the configured access point
                wlan = self.create_wireless_network_access_point(current_network.ssid, current_network.key)
                wireless_network = current_network

        return (wlan, wireless_network, available_networks)

    async def run(self):
        network.hostname('magic-lights')

        display_name = None
        clients = set()
        wlan, wireless_network, available_networks = await self.setup_wireless_network(
            WirelessNetwork(self.settings.interface, self.settings.ssid, self.settings.key),
            WirelessNetwork(network.WLAN.IF_AP, _DEFAULT_SSID, _DEFAULT_KEY))

        ip, subnet, gateway, dns = wlan.ifconfig()
        log_message(
            'WiFi enabled in', format_interface(wireless_network.interface),
            'mode and with SSID', self.settings.ssid,
            'IP', ip,
            'subnet', subnet,
            'gateway', gateway,
            'DNS', dns)

        def serialize_state():
            ip, *_ = wlan.ifconfig()

            return dict(
                display=display_name,
                brightness=self.settings.brightness,
                available_displays=[
                    dict(
                        name='rainbow',
                        repetitions=self.displays.rainbow.repetitions,
                        saturation=self.displays.rainbow.saturation,
                        brightness=self.displays.rainbow.brightness,
                        wait_ms=self.displays.rainbow.wait_ms),
                    dict(
                        name='pacman',
                        ended=self.displays.pacman.ended),
                    dict(
                        name='byte',
                        buffer=list(self.displays.byte.buffer),
                        alignment=format_alignment(self.displays.byte.alignment)),
                    dict(
                        name='firework')],
                wireless_network=dict(
                    interface=format_interface(wireless_network.interface),
                    ssid=wireless_network.ssid,
                    ip=ip),
                available_wireless_networks=list(dict(
                    ssid=ssid.decode('utf-8'),
                    bssid=format_mac_address(bssid),
                    channel=channel,
                    rssi=rssi,
                    security=format_security(security),
                    hidden=bool(hidden)) for ssid, bssid, channel, rssi, security, hidden in available_networks))

        async def broadcast():
            message = json.dumps(serialize_state())

            for ws in tuple(clients):
                try:
                    await ws.send(message)
                except Exception as e:
                    log_message(format_client_address(ws.request.client_addr), 'WebSocket send error:', str(e))
                    clients.discard(ws)

        async def display_run(display):
            await display.run()
            self.task = None
            await broadcast()

        def display_ip(ip):
            nonlocal display_name

            self.displays.byte.buffer = [int(x) for x in ip.split('.')]
            self.task = asyncio.create_task(display_run(self.displays.byte))
            display_name = 'byte'

        app = Microdot()

        @app.route('/')
        def index(request):
            file = fs.open('/web/index.html.gz', 'rb')
            return send_file('/web/index.html.gz',
                content_type='text/html; charset=utf-8', compressed=True, stream=file)

        @app.route('/api/state')
        def state(request):
            return serialize_state()

        @app.route('/api/ws')
        @with_websocket
        async def ws(request, ws):
            nonlocal wlan, wireless_network, available_networks, display_name

            clients.add(ws)
            log_message(format_client_address(request.client_addr), 'WebSocket client connected.')

            try:
                await ws.send(json.dumps(serialize_state()))

                while True:
                    message = await ws.receive()
                    message = json.loads(message)

                    if isinstance(message, (list, tuple)) and len(message) >= 1:
                        action, *args = message
                        log_message(
                            format_client_address(request.client_addr),
                            f'Received WebSocket client message of type {action}:',
                            ', '.join(str(x) for x in args))

                        if action == 'run_display':
                            name, = args
                            display = None

                            if name == 'rainbow':
                                display = self.displays.rainbow
                            elif name == 'pacman':
                                display = self.displays.pacman
                            elif name == 'byte':
                                display = self.displays.byte
                            elif name == 'firework':
                                display = self.displays.firework

                            if display:
                                if self.task:
                                    self.task.cancel()

                                    try:
                                        await self.task
                                    except asyncio.CancelledError:
                                        pass

                                    self.task = None

                                display.reset()
                                self.task = asyncio.create_task(display_run(display))
                                display_name = name
                        elif action == 'brightness':
                            value, = args
                            self.settings.brightness = value
                            self.settings.write(_SETTINGS_PATH)
                        elif action == 'create_wireless_network_access_point':
                            ssid, key = args
                            wlan, wireless_network, available_networks = await self.setup_wireless_network(
                                WirelessNetwork(network.WLAN.IF_AP, ssid, key),
                                WirelessNetwork(self.settings.interface, self.settings.ssid, self.settings.key),
                                wlan)

                            ip, *_ = wlan.ifconfig()
                            display_ip(ip)

                            self.settings.interface = wireless_network.interface
                            self.settings.ssid = wireless_network.ssid
                            self.settings.key = wireless_network.key
                            self.settings.write(_SETTINGS_PATH)
                        elif action == 'connect_wireless_network_station':
                            ssid, key = args
                            wlan, wireless_network, available_networks = await self.setup_wireless_network(
                                WirelessNetwork(network.WLAN.IF_STA, ssid, key),
                                WirelessNetwork(self.settings.interface, self.settings.ssid, self.settings.key),
                                wlan)

                            ip, *_ = wlan.ifconfig()
                            display_ip(ip)

                            if wireless_network.interface == network.WLAN.IF_STA and wlan.isconnected():
                                self.settings.interface = wireless_network.interface
                                self.settings.ssid = wireless_network.ssid
                                self.settings.key = wireless_network.key
                                self.settings.write(_SETTINGS_PATH)
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
                            self.displays.pacman.change_player_direction()
                        elif action == 'displays.byte.buffer':
                            value, = args
                            self.displays.byte.buffer = value
                        elif action == 'displays.byte.alignment':
                            value, = args
                            self.displays.byte.alignment = parse_alignment(value)
                        elif action == 'displays.firework.ignite':
                            self.displays.firework.ignite()

                        await broadcast()
            finally:
                clients.discard(ws)
                log_message(format_client_address(request.client_addr), 'WebSocket client disconnect.')

        display_ip(ip)
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

    settings = Settings.read(_SETTINGS_PATH)
    displays = DisplayTuple(
        RainbowDisplay(pixels, settings),
        PacmanDisplay(pixels, settings),
        ByteDisplay(pixels, settings),
        FireworkDisplay(pixels, settings))
    controller = Controller(pixels, displays, settings)

    run_safe(controller.run())
