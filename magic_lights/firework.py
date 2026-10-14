import random
import asyncio

try:
    from .ansi_color import nearest_color
except ImportError:
    from ansi_color import nearest_color

try:
    from .firework_native import Engine as _NativeEngine, render as _native_render
except ImportError:
    try:
        from .firework_native import Engine as _NativeEngine, render as _native_render
    except ImportError:
        _NativeEngine = None
        _native_render = None

try:
    from time import ticks_ms, ticks_diff
except ImportError:
    from time import monotonic_ns

    def ticks_ms():
        return monotonic_ns() // 1_000_000

    def ticks_diff(a, b):
        return a - b

try:
    from micropython import const
except ImportError:
    def const(expr):
        return expr

try:
    from asyncio import sleep_ms
except ImportError:
    def sleep_ms(t):
        return asyncio.sleep(t / 1000)

_FRACTION_LENGTH = const(8)
_FRACTION_MAXIMUM = const(1 << _FRACTION_LENGTH)

_UPDATE_FREQUENCY_MS = const(32)

_EXPLOSION_DENSITY = const(50)
_EXPLOSION_CREATION_PROBABILITY = const(1)
_EXPLOSION_PARTICLES_LOWER_LIMIT = const(10)
_EXPLOSION_PARTICLES_UPPER_LIMIT = const(50)
_EXPLOSION_SPEED_MULTIPLIER = const(768)

_PARTICLE_SPEED_UPPER_LIMIT = const(96)

_PARTICLE_INITIAL_DURATION_MS = const(0)
_PARTICLE_IGNITION_DURATION_MS = const(200)
_PARTICLE_BURN_DURATION_MS = const(0)
_PARTICLE_FADE_DURATION_MS = const(2000)

_PARTICLE_PHASE_INITIAL = const(0)
_PARTICLE_PHASE_IGNITION = const(1)
_PARTICLE_PHASE_BURN = const(2)
_PARTICLE_PHASE_FADE = const(3)
_PARTICLE_PHASE_DEAD = const(4)

def rasterize(position, size):
    fractional = position & (_FRACTION_MAXIMUM - 1)
    i = position >> _FRACTION_LENGTH

    yield i, _FRACTION_MAXIMUM - fractional

    for i in range(i + 1, i + size):
        yield i, _FRACTION_MAXIMUM

    if fractional > 0:
        yield i + 1, fractional

# https://www.reddit.com/r/arduino/comments/c3sd46/i_made_this_fireworks_effect_for_my_led_strips
class Particle:
    def __init__(self, position, max_speed, energy=_FRACTION_MAXIMUM):
        self.position = position
        self.speed = random.randint(-max_speed, max_speed)
        self.energy = energy
        self.age_ms = 0

    @property
    def phase(self):
        age = self.age_ms

        if age < _PARTICLE_INITIAL_DURATION_MS:
            return _PARTICLE_PHASE_INITIAL

        age -= _PARTICLE_INITIAL_DURATION_MS
        if age < _PARTICLE_IGNITION_DURATION_MS:
            return _PARTICLE_PHASE_IGNITION

        age -= _PARTICLE_IGNITION_DURATION_MS
        if age < _PARTICLE_BURN_DURATION_MS:
            return _PARTICLE_PHASE_BURN

        age -= _PARTICLE_BURN_DURATION_MS
        if age < _PARTICLE_FADE_DURATION_MS:
            return _PARTICLE_PHASE_FADE

        return _PARTICLE_PHASE_DEAD

    def visibility(self, phase=None):
        age = self.age_ms

        if phase is None:
            phase = self.phase

        if phase == _PARTICLE_PHASE_INITIAL:
            return (_FRACTION_MAXIMUM * age // _PARTICLE_INITIAL_DURATION_MS)
        elif phase == _PARTICLE_PHASE_IGNITION:
            return _FRACTION_MAXIMUM
        elif phase == _PARTICLE_PHASE_BURN:
            return _FRACTION_MAXIMUM
        elif phase == _PARTICLE_PHASE_FADE:
            return _FRACTION_MAXIMUM - (_FRACTION_MAXIMUM * (age
                    - _PARTICLE_INITIAL_DURATION_MS
                    - _PARTICLE_BURN_DURATION_MS
                    - _PARTICLE_IGNITION_DURATION_MS)
                // _PARTICLE_FADE_DURATION_MS)
        else:
            return 0

    def update(self, dt):
        self.age_ms += dt
        self.position += self.speed * dt
        self.speed -= (self.speed * dt) >> 9
        self.energy = self.energy * random.randint(230, 256) >> _FRACTION_LENGTH

class Engine:
    def __init__(self, bounds):
        self.bounds = bounds
        self.particles = []

    def ignite(self, position=None, particle_count=None):
        if position is None:
            position = random.randint(self.bounds.min, self.bounds.max) << _FRACTION_LENGTH

        if particle_count is None:
            particle_count = random.randint(_EXPLOSION_PARTICLES_LOWER_LIMIT, _EXPLOSION_PARTICLES_UPPER_LIMIT)

        multiplier = random.randint(0, _EXPLOSION_SPEED_MULTIPLIER)
        speed_limit = (_PARTICLE_SPEED_UPPER_LIMIT * multiplier) >> _FRACTION_LENGTH

        for _ in range(0, particle_count):
            particle = Particle(position, random.randint(0, speed_limit))
            self.particles.append(particle)

    def update(self, dt):
        for _ in range(0, len(self.bounds), _EXPLOSION_DENSITY):
            if random.randint(0, 99) < _EXPLOSION_CREATION_PROBABILITY:
                self.ignite()

        remove = len(self.particles) - len(self.bounds)

        if remove > 0:
            del self.particles[0:remove]

        for particle in self.particles:
            particle.update(dt)

        while len(self.particles) > 0 and self.particles[0].phase == _PARTICLE_PHASE_DEAD:
            self.particles.pop(0)

    def loop(self):
        now_ms = past_ms = ticks_ms()

        while True:
            now_ms = ticks_ms()
            delta_ms = ticks_diff(now_ms, past_ms)
            past_ms = now_ms

            self.update(delta_ms)
            yield delta_ms

    async def run(self):
        for _ in self.loop():
            await sleep_ms(_UPDATE_FREQUENCY_MS)

class BaseRenderer:
    @staticmethod
    def random_color():
        return tuple(random.randint(0, 255) for _ in range(3))

    def __init__(self, engine):
        self.engine = engine
        self.particle_colors = {}

    def render(self):
        next_particle_colors = {}

        for particle in self.engine.particles:
            if particle in self.particle_colors:
                color = self.particle_colors[particle]
            else:
                color = self.random_color()

            next_particle_colors[particle] = color

            particle_phase = particle.phase
            particle_visibility = particle.visibility(particle_phase)

            if particle_phase == _PARTICLE_PHASE_IGNITION:
                color = (255, 255, 255)
            else:
                scale = (particle.energy * particle_visibility) >> _FRACTION_LENGTH
                color = tuple((component * scale) >> _FRACTION_LENGTH for component in color)

            size = (particle_visibility * 5) >> _FRACTION_LENGTH

            for i, coverage in rasterize(particle.position, size):
                if not (self.engine.bounds.min <= i <= self.engine.bounds.max):
                    break

                if coverage != _FRACTION_MAXIMUM:
                    color = tuple((component * coverage) >> _FRACTION_LENGTH for component in color)

                yield i, color

        self.particle_colors = next_particle_colors

if _NativeEngine and _native_render:
    class NativeEngine:
        def __init__(self, bounds):
            self.native = _NativeEngine(bounds)

        def ignite(self, position=None, particle_count=None):
            self.native.ignite()

        def update(self, dt):
            self.native.update(dt)

        def loop(self):
            now_ms = past_ms = ticks_ms()

            while True:
                now_ms = ticks_ms()
                delta_ms = ticks_diff(now_ms, past_ms)
                past_ms = now_ms

                self.update(delta_ms)
                yield delta_ms

        async def run(self):
            for _ in self.loop():
                await sleep_ms(_UPDATE_FREQUENCY_MS)

    class NativeBaseRenderer:
        def __init__(self, engine):
            self.engine = engine

        def render(self):
            return _native_render(self.engine.native)

class TerminalRenderer(BaseRenderer):
    def __init__(self, engine, stream):
        super().__init__(engine)
        self.stream = stream

    def render(self, clear=True):
        if clear:
            self.stream.write('\033[1A\r')

        stage = [' '] * len(self.engine.bounds)

        for i, color in super().render():
            stage[i] = f'\033[38;5;{nearest_color(*color)}m\u273D\033[0m'

        self.stream.write(''.join(stage) + '\n')

if __name__ == '__main__':
    import sys
    from engine_utils import Bounds

    async def main():
        engine = Engine(Bounds(0, 179))
        renderer = TerminalRenderer(engine, sys.stdout)

        renderer.render(False)

        def control():
            while True:
                input()
                sys.stdout.write('\033[1A')
                engine.ignite()

        asyncio.create_task(engine.run())
        loop.run_in_executor(None, control)

        while True:
            renderer.render()
            await asyncio.sleep(0.016)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    asyncio.run(main())
