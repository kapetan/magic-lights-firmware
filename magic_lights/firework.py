import random
import math
import asyncio
from .ansi_color import nearest_color

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

_UPDATE_FREQUENCY_MS = const(32)

_EXPLOSION_DENSITY = const(50)
_EXPLOSION_CREATION_PROBABILITY = const(0.01)
_EXPLOSION_PARTICLES_LOWER_LIMIT = const(10)
_EXPLOSION_PARTICLES_UPPER_LIMIT = const(50)
_EXPLOSION_SPEED_MULTIPLIER = const(3)

_PARTICLE_SPEED_UPPER_LIMIT = const(375)

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
    fractional = position % 1
    i = math.floor(position)

    yield i, 1 - fractional

    for i in range(i + 1, i + size):
        yield i, 1

    if fractional > 0:
        yield i + 1, fractional

# https://www.reddit.com/r/arduino/comments/c3sd46/i_made_this_fireworks_effect_for_my_led_strips
class Particle:
    def __init__(self, position, max_speed, energy=1):
        self.position = position
        self.speed = random.random() * max_speed * 2 - max_speed
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

    @property
    def visibility(self):
        age = self.age_ms
        phase = self.phase

        if phase == _PARTICLE_PHASE_INITIAL:
            return (age / _PARTICLE_INITIAL_DURATION_MS)
        elif phase == _PARTICLE_PHASE_IGNITION:
            return 1
        elif phase == _PARTICLE_PHASE_BURN:
            return 1
        elif phase == _PARTICLE_PHASE_FADE:
            return 1 - ((age
                    - _PARTICLE_INITIAL_DURATION_MS
                    - _PARTICLE_BURN_DURATION_MS
                    - _PARTICLE_IGNITION_DURATION_MS)
                / _PARTICLE_FADE_DURATION_MS)
        else:
            return 0

    def update(self, dt):
        self.age_ms += dt
        self.position += self.speed * dt / 1000
        self.speed -= 2 * self.speed * dt / 1000
        self.energy *= 1.0 - random.random() * 0.1

class Engine:
    def __init__(self, bounds):
        self.bounds = bounds
        self.particles = []

    def ignite(self, position=None, particle_count=None):
        if position is None:
            position = random.randint(self.bounds.min, self.bounds.max)

        if particle_count is None:
            particle_count = random.randint(_EXPLOSION_PARTICLES_LOWER_LIMIT, _EXPLOSION_PARTICLES_UPPER_LIMIT)

        multiplier = random.random() * _EXPLOSION_SPEED_MULTIPLIER

        for _ in range(0, particle_count):
            particle = Particle(position, _PARTICLE_SPEED_UPPER_LIMIT * random.random() * multiplier)
            self.particles.append(particle)

    def update(self, dt):
        for _ in range(0, math.ceil(len(self.bounds) / _EXPLOSION_DENSITY)):
            if random.random() < _EXPLOSION_CREATION_PROBABILITY:
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
        for particle in self.engine.particles:
            if particle in self.particle_colors:
                color = self.particle_colors[particle]
            else:
                color = self.random_color()
                self.particle_colors[particle] = color

            if particle.phase == _PARTICLE_PHASE_IGNITION:
                color = (255, 255, 255)
            else:
                color = tuple(component * particle.energy * particle.visibility for component in color)

            size = math.floor(particle.visibility * 5)

            for i, coverage in rasterize(particle.position, size):
                if not (self.engine.bounds.min <= i <= self.engine.bounds.max):
                    break

                if coverage != 1:
                    color = tuple(component * coverage for component in color)

                yield i, color

        for particle in list(self.particle_colors.keys()):
            if particle.phase == _PARTICLE_PHASE_DEAD:
                del self.particle_colors[particle]

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
