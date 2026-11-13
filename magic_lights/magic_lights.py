import sys
import time
import asyncio
import network
import errno
import json
import struct
import math
from io import StringIO
from collections import namedtuple
from machine import Pin, RTC, Timer
from micropython import const
from neopixel import NeoPixel
from microdot import Microdot, send_file
from microdot.websocket import with_websocket
from suntime.suntime import Suntime
from . import pacman
from . import firework
from . import fs
from . import aiontp
from .engine_utils import Bounds
from .color_utils import (
    native_hsv_to_rgb,
    brightness,
    native_brightness_gamma8,
    native_brightness_gamma8_component)
from .atomic_file import AtomicFile

_PIN_PIXELS = const(15)
_PIN_LED = const('LED')

_DAY_SECONDS = const(24 * 60 * 60)
_MILLENNIUM_UNIX_EPOCH_DAYS = const(10957) # days between 1970-01-01 and 2000-01-01

_PIXEL_COUNT = const(240)

_WLAN_CONNECT_TIMEOUT_MS = const(30000)

_SETTINGS_PATH = const('/config.bin')

_DEFAULT_BRIGHTNESS = const(127)
_DEFAULT_LATITUDE = const(55.676097) # Copenhagen, Denmark
_DEFAULT_LONGITUDE = const(12.568337)
_DEFAULT_SSID = const('magic-lights')
_DEFAULT_KEY = const('magic-lights')

_Number = (int, float)
_Function = type(lambda: 0)

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

def parse_display(displays, name):
    return displays[DisplayEnum.parse(name)]

def next_day(hour=0, minute=0, second=0):
    now = time.time()
    start_of_next_day = now + (_DAY_SECONDS - (now % _DAY_SECONDS))
    return start_of_next_day + (3600 * hour) + (60 * minute) + second

def closest_active_interval(timestamp, start_at, end_at, active_interval):
    assert start_at <= end_at

    start_of_day = timestamp - (timestamp % _DAY_SECONDS)
    end_of_day = start_of_day + _DAY_SECONDS - 1
    start_of_interval = start_of_day + start_at
    end_of_interval = start_of_day + end_at

    if start_of_day <= timestamp < start_of_interval:
        return (start_of_interval, end_of_interval) if active_interval else (start_of_day, start_of_interval)
    elif start_of_interval <= timestamp <= end_of_interval:
        return (start_of_interval, end_of_interval) if active_interval else (end_of_interval, end_of_day)
    else:
        return ((start_of_interval + _DAY_SECONDS, end_of_interval + _DAY_SECONDS)
                if active_interval
                else (end_of_interval, end_of_day))

def unix_time(timestamp=None):
    epoch_year, *_ = time.gmtime(0)
    delta = 0

    if epoch_year == 2000:
        delta = _MILLENNIUM_UNIX_EPOCH_DAYS * _DAY_SECONDS
    elif epoch_year != 1970:
        raise OSError(errno.EOPNOTSUPP)

    if timestamp is None:
        timestamp = time.time()

    return timestamp + delta

def parse_time(value):
    epoch_year, *_ = time.gmtime(0)
    delta = 0

    if epoch_year == 2000:
        delta = _MILLENNIUM_UNIX_EPOCH_DAYS * _DAY_SECONDS
    elif epoch_year != 1970:
        raise OSError(errno.EOPNOTSUPP)

    return value - delta

def create_schedule_from_settings(settings):
    if settings.schedule == ScheduleEnum.manual:
        return FixedSchedule(settings.schedule_start_at, settings.schedule_end_at, settings.schedule_active_interval)
    elif settings.schedule == ScheduleEnum.automatic:
        return SuntimeSchedule(settings.latitude, settings.longitude)

def rtc_datetime(seconds_since_epoch):
    try:
        now = time.gmtime(seconds_since_epoch)
    except OverflowError as e:
        # if the server does not have the correct time set
        # the conversion might overflow.
        raise OSError(errno.EINVAL) from e

    year, month, day, hour, minute, second, week_day, _ = now
    rtc = RTC()
    rtc.datetime((year, month, day, week_day + 1, hour, minute, second, 0))

def log_message(*message):
    year, month, day, hour, minute, second, *_ = time.gmtime()
    sys.stderr.write(f'[{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}]:')

    for part in message:
        sys.stderr.write(' ')
        sys.stderr.write(part)

    sys.stderr.write('\n')

def log_error(err, *message):
    file = StringIO()
    sys.print_exception(err, file)
    log_message(*message)
    sys.stderr.write(file.getvalue())
    sys.stderr.write('\n')

def wlan_reset(wlan):
    wlan.active(False)
    wlan.active(True)

def define_enum(name, fields):
    _Enum = namedtuple(name, fields)

    class Enum(_Enum):
        NAMES = tuple(fields)
        VALUES = tuple(range(0, len(fields)))

        @classmethod
        def parse(cls, name: str) -> int:
            return cls.NAMES.index(name)

        @classmethod
        def format(cls, value: int) -> str:
            return cls.NAMES[value]

    return Enum(*Enum.VALUES)

def define_struct(name, byte_order, fields):
    _Struct = namedtuple(name, tuple(field_name for field_name, _ in fields))

    class Struct(_Struct):
        FORMAT = byte_order + ''.join(field_type for _, field_type in fields)
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
                    raise OSError(errno.EIO)

                return cls.unpack(cls.BUFFER)

        def pack(self, buffer, offset=0):
            struct.pack_into(self.FORMAT, buffer, offset, *self)

        def write(self, name):
            self.pack(self.BUFFER)

            with AtomicFile.open(name, 'w+b') as file:
                file.write(self.BUFFER)

    return Struct

def error_handler(fn):
    async def wrapper(request, ws):
        try:
            if isinstance(fn, _Function):
                return fn(request, ws)
            else:
                return await fn(request, ws)
        except ValidationError as e:
            message = json.dumps(dict(type='error', message=str(e)))
            await ws.send(message)
        except NetworkError as e:
            log_error(e.cause, 'Unexpected network error.')
            message = json.dumps(dict(type='error', message=str(e)))
            await ws.send(message)

    return wrapper

def validate_arguments(args, types, exact=True):
    if len(args) != len(types) if exact else len(args) < len(types):
        raise ValidationError(f'Expected at least {len(types)} arguments')

    for i, typ in enumerate(types):
        validate_argument_type(args[i], typ)

def validate_argument_range(arg, min=-math.inf, max=math.inf):
    if not (min <= arg <= max):
        if math.isinf(min):
            range = f'less than or equal to {max}'
        elif math.isinf(max):
            range = f'greater than or equal to {min}'
        else:
            range = f'between {min} and {max}'

        raise ValidationError(f'Expected argument to be {range}')

def validate_argument_type(arg, typ):
    if not isinstance(arg, typ):
        typ = typ.__name__ if isinstance(typ, type) else tuple(t.__name__ for t in typ)
        raise ValidationError(f'Expected argument to be of type {typ}')

def validate_argument_enum(arg, enum):
    if arg not in enum.NAMES:
        raise ValidationError(f'Expected argument to be one of {enum.NAMES}')

def validate_arguments_single_byte(args):
    validate_arguments(args, (int,))
    validate_argument_range(args[0], 0, 255)

class ValidationError(Exception):
    pass

class NetworkError(Exception):
    class _CaptureContext:
        def __init__(self, message):
            self.message = message

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            if isinstance(exc_value, OSError):
                raise NetworkError(self.message, exc_value)

    @classmethod
    def capture(cls, message):
        return cls._CaptureContext(message)

    def __init__(self, message, cause):
        super().__init__(message)
        self.cause = cause

class TimerCallback:
    def __init__(self):
        self.callbacks = []
        self.task = None

    def __call__(self, timer):
        if not self.task:
            self.task = asyncio.create_task(self.execute())

    async def execute(self):
        now = time.time()

        for entry in list(self.callbacks):
            timestamp, fn = entry

            if now >= timestamp:
                self.callbacks.remove(entry)

                if isinstance(fn, _Function):
                    fn()
                else:
                    await fn()

        self.task = None

    def schedule(self, timestamp, fn):
        self.callbacks.append((timestamp, fn))

    def unschedule(self, fn):
        for i, (_, other) in enumerate(self.callbacks):
            if fn == other:
                self.callbacks.pop(i)
                break

class SuntimeSchedule:
    def __init__(self, latitude, longitude):
        self.suntime = Suntime(latitude, longitude)

    def closest_active_interval(self, timestamp):
        year, month, day, *_ = time.gmtime(timestamp)
        self.suntime.calc_sunrise_sunset(year, month, day)
        sunrise_minutes = self.suntime.sunrise
        sunset_minutes = self.suntime.sunset

        return closest_active_interval(timestamp, sunrise_minutes * 60, sunset_minutes * 60, False)

class FixedSchedule:
    def __init__(self, start_at, end_at, active_interval):
        assert start_at <= end_at

        self.start_at = start_at
        self.end_at = end_at
        self.active_interval = active_interval

    def closest_active_interval(self, timestamp):
        return closest_active_interval(timestamp, self.start_at, self.end_at, self.active_interval)

DisplayEnum = define_enum('DisplayEnum', ('none', 'rainbow', 'pacman', 'byte', 'firework'))
ScheduleEnum = define_enum('ScheduleEnum', ('none', 'manual', 'automatic'))
AlignmentEnum = define_enum('AlignmentEnum', ('start', 'center', 'end'))

ScheduleInterval = namedtuple('ScheduleInterval', ('start_at', 'end_at', 'timestamp', 'active'))

DisplayTuple = namedtuple('DisplayTuple', DisplayEnum.NAMES)

WirelessNetwork = namedtuple('WirelessNetwork', ('interface', 'ssid', 'key'))

SettingsStruct = define_struct('SettingsStruct', '>', (
    ('version', 'B'),
    ('brightness', 'B'),
    ('latitude', 'f'),
    ('longitude', 'f'),
    ('schedule', 'B'),
    ('schedule_start_at', 'I'),
    ('schedule_end_at', 'I'),
    ('schedule_active_interval', 'B'),
    ('interface', 'B'),
    ('ssid_length', 'B'),
    ('ssid_buffer', '32s'),
    ('key_length', 'B'),
    ('key_buffer', '64s')
))

class Settings:
    @classmethod
    def read(cls, name=_SETTINGS_PATH):
        try:
            struct = SettingsStruct.read(name)
            return cls(
                struct.brightness,
                struct.latitude,
                struct.longitude,
                struct.schedule,
                struct.schedule_start_at,
                struct.schedule_end_at,
                struct.schedule_active_interval,
                struct.interface,
                struct.ssid_buffer[0:struct.ssid_length],
                struct.key_buffer[0:struct.key_length])
        except OSError:
            return cls(
                _DEFAULT_BRIGHTNESS,
                _DEFAULT_LATITUDE,
                _DEFAULT_LONGITUDE,
                ScheduleEnum.automatic,
                0,
                0,
                0,
                network.WLAN.IF_AP,
                _DEFAULT_SSID,
                _DEFAULT_KEY)

    def __init__(
            self,
            brightness,
            latitude,
            longitude,
            schedule,
            schedule_start_at,
            schedule_end_at,
            schedule_active_interval,
            interface,
            ssid,
            key):
        self.brightness = brightness
        self.latitude = latitude
        self.longitude = longitude
        self.schedule = schedule
        self.schedule_start_at = schedule_start_at
        self.schedule_end_at = schedule_end_at
        self.schedule_active_interval = schedule_active_interval
        self.interface = interface
        self.ssid = ssid
        self.key = key

    def write(self, name=_SETTINGS_PATH):
        struct = SettingsStruct(1,
            self.brightness,
            self.latitude,
            self.longitude,
            self.schedule,
            self.schedule_start_at,
            self.schedule_end_at,
            self.schedule_active_interval,
            self.interface,
            len(self.ssid),
            self.ssid,
            len(self.key),
            self.key)

        struct.write(name)

class NoneDisplay:
    _COLOR_BLACK = (0, 0, 0)

    def __init__(self, pixels):
        self.pixels = pixels

    def reset(self):
        pass

    async def run(self):
        for i in range(len(self.pixels)):
            self.pixels[i] = self._COLOR_BLACK

        self.pixels.write()

class FireworkDisplay:
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
            renderer.render_into(self.pixels)

            for i, byte in enumerate(self.pixels.buf):
                self.pixels.buf[i] = native_brightness_gamma8_component(byte, self.settings.brightness)

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

    def __init__(self, pixels, settings, buffer=b'', alignment=AlignmentEnum.center):
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

                if alignment == AlignmentEnum.end:
                    start_offset = pixel_length - buffer_pixel_length
                elif alignment == AlignmentEnum.center:
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

                    self.pixels[i] = native_brightness_gamma8(color, _brightness)

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
                    color = native_hsv_to_rgb(hue, self.saturation, self.brightness)
                    color = native_brightness_gamma8(color, self.settings.brightness)
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

                self.pixels[body.position] = native_brightness_gamma8(color, self.settings.brightness)

            self.pixels.write()
            await asyncio.sleep_ms(16)

class Controller:
    def __init__(self, pixels, timer, displays, settings):
        self.pixels = pixels
        self.timer = timer
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

            ip, subnet, gateway, dns = wlan.ifconfig()
            log_message(
                'Successfully connected to wireless network with SSID', ssid,
                'IP', ip,
                'subnet', subnet,
                'gateway', gateway,
                'DNS', dns)
        except:
            try:
                wlan.disconnect()
            except OSError:
                # Might not have been connected
                pass

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

            if not wlan.isconnected():
                wlan.active(False)

                # Stay reachable, fall back to the configured access point
                if current_network.interface == network.WLAN.IF_STA:
                    wlan = await self.connect_wireless_network_station(current_network.ssid, current_network.key)
                else:
                    wlan = self.create_wireless_network_access_point(current_network.ssid, current_network.key)

                wireless_network = current_network

        return (wlan, wireless_network, available_networks)

    async def run(self):
        network.hostname('magic-lights')

        timer_callback = TimerCallback()
        display_name = None
        schedule = create_schedule_from_settings(self.settings)
        current_schedule_interval = None
        clients = set()
        wlan, wireless_network, available_networks = await self.setup_wireless_network(
            WirelessNetwork(self.settings.interface, self.settings.ssid, self.settings.key),
            WirelessNetwork(network.WLAN.IF_AP, _DEFAULT_SSID, _DEFAULT_KEY))

        ip, subnet, gateway, dns = wlan.ifconfig()
        log_message(
            'WiFi enabled in', format_interface(wireless_network.interface),
            'mode and with SSID', wireless_network.ssid,
            'IP', ip,
            'subnet', subnet,
            'gateway', gateway,
            'DNS', dns)

        async def synchronize_time():
            try:
                timestamp = await aiontp.time()
            except OSError as e:
                log_error(e, 'Failed to query NTP server.')
            else:
                rtc_datetime(timestamp)
                log_message('Synchronized with NTP server', str(timestamp))

            timer_callback.schedule(next_day(hour=1), synchronize_time)

        async def update_schedule():
            nonlocal current_schedule_interval

            timestamp = time.time()
            start, end = schedule.closest_active_interval(timestamp)
            active = start <= timestamp <= end
            current_schedule_interval = ScheduleInterval(start, end, timestamp, active)

            log_message(f'Processing schedule. Closest active interval [{start}, {end}].')

            if active:
                if not self.task and display_name:
                    display = parse_display(self.displays, display_name)
                    self.task = asyncio.create_task(display_run(display))

                timer_callback.schedule(end, update_schedule)
            else:
                await display_stop()
                await self.displays.none.run()

                timer_callback.schedule(start, update_schedule)

            await broadcast()
            return active

        def serialize_state():
            ip, *_ = wlan.ifconfig()

            return dict(
                display=display_name,
                brightness=self.settings.brightness,
                available_displays=[
                    dict(
                        name='none'),
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
                        alignment=self.displays.byte.alignment),
                    dict(
                        name='firework')],
                datetime=unix_time(),
                location=dict(
                    latitude=self.settings.latitude,
                    longitude=self.settings.longitude),
                schedule=dict(
                    name=ScheduleEnum.format(self.settings.schedule),
                    start_at=self.settings.schedule_start_at,
                    end_at=self.settings.schedule_end_at,
                    active_interval=self.settings.schedule_active_interval),
                current_schedule_interval=dict(
                    start_at=unix_time(current_schedule_interval.start_at),
                    end_at=unix_time(current_schedule_interval.end_at),
                    datetime=unix_time(current_schedule_interval.timestamp),
                    active=current_schedule_interval.active) if (current_schedule_interval and schedule) else None,
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
                    log_error(e, format_client_address(ws.request.client_addr), 'WebSocket send error.')
                    clients.discard(ws)

        async def display_run(display):
            log_message(f'Running display {display_name}.')

            display.reset()
            await display.run()
            self.task = None
            await broadcast()

        async def display_stop():
            log_message('Stopping display.')

            if self.task:
                self.task.cancel()

                try:
                    await self.task
                except asyncio.CancelledError:
                    pass

                self.task = None

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
        @error_handler
        async def ws(request, ws):
            nonlocal wlan, wireless_network, available_networks, display_name, schedule

            clients.add(ws)
            log_message(format_client_address(request.client_addr), 'WebSocket client connected.')

            try:
                await ws.send(json.dumps(serialize_state()))

                while True:
                    message = await ws.receive()

                    try:
                        message = json.loads(message)
                    except ValueError:
                        raise ValidationError('Invalid JSON syntax')

                    validate_argument_type(message, (list, tuple))
                    validate_arguments(message, (str,), exact=False)
                    action, *args = message

                    log_message(
                        format_client_address(request.client_addr),
                        f'Received WebSocket client message {action}{tuple(args)}')

                    if action == 'run_display':
                        validate_arguments(args, (str,))
                        name, = args
                        validate_argument_enum(name, DisplayEnum)

                        display = parse_display(self.displays, name)

                        await display_stop()
                        self.task = asyncio.create_task(display_run(display))
                        display_name = name
                    elif action == 'brightness':
                        validate_arguments_single_byte(args)
                        value, = args

                        self.settings.brightness = value
                        self.settings.write()
                    elif action == 'location':
                        validate_arguments(args, (_Number, _Number))
                        latitude, longitude = args
                        validate_argument_range(latitude, -90, 90)
                        validate_argument_range(longitude, -180, 180)

                        if self.settings.schedule == ScheduleEnum.automatic:
                            schedule = SuntimeSchedule(latitude, longitude)
                            timer_callback.unschedule(update_schedule)
                            await update_schedule()

                        self.settings.latitude = latitude
                        self.settings.longitude = longitude
                        self.settings.write()
                    elif action == 'datetime':
                        validate_arguments(args, (_Number,))
                        value, = args
                        validate_argument_range(value, min=0)

                        timestamp = parse_time(value)
                        rtc_datetime(timestamp)
                    elif action == 'schedule':
                        validate_arguments(args, (str,), exact=False)
                        value, *rest = args
                        validate_argument_enum(value, ScheduleEnum)

                        value = ScheduleEnum.parse(value)

                        if value == ScheduleEnum.manual:
                            validate_arguments(rest, (_Number, _Number, bool))
                            schedule_start_at, schedule_end_at, schedule_active_interval = rest
                            validate_argument_range(schedule_start_at, 0, schedule_end_at)
                            validate_argument_range(schedule_end_at, schedule_start_at, _DAY_SECONDS)

                            self.settings.schedule_start_at = schedule_start_at
                            self.settings.schedule_end_at = schedule_end_at
                            self.settings.schedule_active_interval = schedule_active_interval

                        self.settings.schedule = value
                        schedule = create_schedule_from_settings(self.settings)
                        timer_callback.unschedule(update_schedule)

                        if value != ScheduleEnum.none:
                            await update_schedule()

                        self.settings.write()
                    elif action == 'create_wireless_network_access_point':
                        validate_arguments(args, (str, str))

                        ssid, key = args

                        with NetworkError.capture('Failed to setup wireless network'):
                            wlan, wireless_network, available_networks = await self.setup_wireless_network(
                                WirelessNetwork(network.WLAN.IF_AP, ssid, key),
                                WirelessNetwork(self.settings.interface, self.settings.ssid, self.settings.key),
                                wlan)

                        ip, *_ = wlan.ifconfig()
                        display_ip(ip)

                        self.settings.interface = wireless_network.interface
                        self.settings.ssid = wireless_network.ssid
                        self.settings.key = wireless_network.key
                        self.settings.write()
                        timer_callback.unschedule(synchronize_time)
                    elif action == 'connect_wireless_network_station':
                        validate_arguments(args, (str, str))

                        ssid, key = args

                        with NetworkError.capture('Failed to setup wireless network'):
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
                            self.settings.write()

                            timer_callback.unschedule(synchronize_time)
                            await synchronize_time()
                    elif action == 'displays.rainbow.repetitions':
                        validate_arguments_single_byte(args)
                        value, = args
                        self.displays.rainbow.repetitions = value
                    elif action == 'displays.rainbow.saturation':
                        validate_arguments_single_byte(args)
                        value, = args
                        self.displays.rainbow.saturation = value
                    elif action == 'displays.rainbow.brightness':
                        validate_arguments_single_byte(args)
                        value, = args
                        self.displays.rainbow.brightness = value
                    elif action == 'displays.rainbow.wait_ms':
                        validate_arguments(args, (int,))
                        value, = args
                        validate_argument_range(value, min=0)
                        self.displays.rainbow.wait_ms = value
                    elif action == 'displays.pacman.change_player_direction':
                        self.displays.pacman.change_player_direction()
                    elif action == 'displays.byte.buffer':
                        validate_arguments(args, ((list, tuple),))
                        value, = args
                        for b in value:
                            validate_argument_type(b, int)
                            validate_argument_range(b, 0, 255)

                        self.displays.byte.buffer = value
                    elif action == 'displays.byte.alignment':
                        validate_arguments(args, (str,))
                        value, = args
                        validate_argument_enum(value, AlignmentEnum)
                        self.displays.byte.alignment = AlignmentEnum.parse(value)
                    elif action == 'displays.firework.ignite':
                        self.displays.firework.ignite()
                    else:
                        raise ValidationError(f'Unknown command {action}')

                    await broadcast()
            finally:
                clients.discard(ws)
                log_message(format_client_address(request.client_addr), 'WebSocket client disconnect.')

        if wireless_network.interface == network.WLAN.IF_STA and wlan.isconnected():
            await synchronize_time()

        if schedule:
            active = await update_schedule()
        else:
            active = True

        if not display_name and active:
            display_ip(ip)

        self.timer.init(mode=Timer.PERIODIC, period=60_000, callback=timer_callback)

        try:
            await app.start_server(port=80)
        finally:
            self.timer.deinit()

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
    timer = Timer()

    led.on()

    settings = Settings.read()
    displays = DisplayTuple(
        NoneDisplay(pixels),
        RainbowDisplay(pixels, settings),
        PacmanDisplay(pixels, settings),
        ByteDisplay(pixels, settings),
        FireworkDisplay(pixels, settings))
    controller = Controller(pixels, timer, displays, settings)

    run_safe(controller.run())
