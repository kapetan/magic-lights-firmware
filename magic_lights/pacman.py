import random
import math
import asyncio

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

_DOTS_RATIO = const(3)

_PLAYER_SPEED = const(0.03)
_GHOST_SPEED = const(0.03)
_GHOST_SPEED_FACTOR = const(0.4)

_DIRECTION_LEFT = const(-1)
_DIRECTION_RIGHT = const(1)

_VULNERABLE_DURATION_MS = const(3000)

_DOT_POINTS = const(10)
_POWER_PELLET_POINTS = const(50)
_GHOST_POINTS = const(200)

_UPDATE_FREQUENCY_MS = const(16)

class Body:
    def __init__(self, position):
        self.position = position

class Movable(Body):
    def __init__(self, position, direction, speed, bounds):
        super().__init__(position)

        self.direction = direction
        self.speed = speed
        self.bounds = bounds

        self.fractional_distance = 0

    def change_direction(self):
        self.direction *= -1
        self.fractional_distance = 0

    def update(self, dt):
        self.fractional_distance += self.speed * dt

        if self.fractional_distance >= 1:
            distance = math.floor(self.fractional_distance)
            self.fractional_distance -= distance
            self.position += self.direction * distance

class Player(Movable):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.points = 0

    def update(self, dt):
        previous_position = self.position
        super().update(dt)

        if (distance := self.bounds.min - self.position) > 0:
            self.position = self.bounds.max - distance + 1
            return set(range(0, previous_position + 1)) | set(range(self.position, self.bounds.max + 1))
        elif (distance := self.position - self.bounds.max) > 0:
            self.position = self.bounds.min + distance - 1
            return set(range(previous_position, self.bounds.max + 1)) | set(range(0, self.position + 1))
        else:
            return set(range(previous_position, self.position + self.direction, self.direction))

class Ghost(Movable):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.dead = False
        self.vulnerable = 0

    def accelerate(self, factor=1):
        self.speed /= (_GHOST_SPEED_FACTOR ** factor)

    def decelerate(self, factor=1):
        self.speed *= (_GHOST_SPEED_FACTOR ** factor)

    def kill(self):
        self.dead = True
        self.vulnerable = 0

        self.accelerate(2)

        if self.position < self.bounds / 2:
            if self.direction == _DIRECTION_LEFT:
                self.change_direction()
        elif self.direction == _DIRECTION_RIGHT:
            self.change_direction()

    def weaken(self):
        self.vulnerable = _VULNERABLE_DURATION_MS
        self.decelerate()

    def update(self, dt):
        previous_position = self.position
        super().update(dt)

        if self.position < self.bounds.min:
            self.position = self.bounds.min
        elif self.position > self.bounds.max:
            self.position = self.bounds.max

        if self.dead:
            if ((self.direction == _DIRECTION_LEFT and self.position == self.bounds.min)
                    or (self.direction == _DIRECTION_RIGHT and self.position == self.bounds.max)):
                self.dead = False
                self.decelerate()
        elif self.vulnerable:
            self.vulnerable -= dt

            if self.vulnerable <= 0:
                self.vulnerable = 0
                self.accelerate()

        return set(range(previous_position, self.position + self.direction, self.direction))

class Eatable(Body):
    def __init__(self, position, points):
        super().__init__(position)
        self.points = points

class Dot(Eatable):
    pass

class PowerPellet(Eatable):
    pass

class Engine:
    def __init__(self, bounds):
        self.bounds = bounds
        self.player = Player(bounds // 2, _DIRECTION_RIGHT, _PLAYER_SPEED, self.bounds)
        self.ghost = Ghost(self.bounds.max, _DIRECTION_LEFT, _GHOST_SPEED, self.bounds)
        self.dots = None
        self.ended = False
        self.level = 1

        self.initialize()

    def bodies(self):
        for dot in self.dots:
            if dot is not None:
                yield dot

        yield self.player
        yield self.ghost

    def initialize(self):
        dot_space = len(self.bounds) - (_DOTS_RATIO - 1) * 2
        dot_count = dot_space // _DOTS_RATIO
        dot_rest_space = dot_space - (dot_count * _DOTS_RATIO)
        power_pellet_index = random.randint(0, dot_count - 1)

        if self.dots is None:
            self.dots = [None] * dot_count

        for i in range(dot_count):
            dot_position = i * _DOTS_RATIO + (_DOTS_RATIO - 1) + (dot_rest_space // 2) + self.bounds.min
            self.dots[i] = (PowerPellet(dot_position, _POWER_PELLET_POINTS)
                if i == power_pellet_index else Dot(dot_position, _DOT_POINTS))

    def update(self, dt):
        if self.ended:
            return

        player_positions = self.player.update(dt)
        ghost_positions = self.ghost.update(dt)

        if not self.ghost.dead:
            if not player_positions.isdisjoint(ghost_positions):
                if self.ghost.vulnerable:
                    self.ghost.kill()
                else:
                    self.ended = True

            if self.ghost.vulnerable:
                opposite_direction_below = _DIRECTION_RIGHT
                opposite_direction_above = _DIRECTION_LEFT
            else:
                opposite_direction_below = _DIRECTION_LEFT
                opposite_direction_above = _DIRECTION_RIGHT

            if ((self.ghost.position < self.player.position and self.ghost.direction == opposite_direction_below)
                    or (self.player.position < self.ghost.position
                        and self.ghost.direction == opposite_direction_above)):
                self.ghost.change_direction()

        none_count = 0

        for i, dot in enumerate(self.dots):
            if dot is None:
                none_count += 1

            if dot is not None and dot.position in player_positions:
                self.dots[i] = None
                self.player.points += dot.points

                if isinstance(dot, PowerPellet) and not self.ghost.dead and not self.ghost.vulnerable:
                    self.ghost.weaken()

        if none_count == len(self.dots):
            self.initialize()
            self.level += 1
            self.ghost.accelerate(1 / 64)

    def loop(self):
        now_ms = past_ms = ticks_ms()

        while not self.ended:
            now_ms = ticks_ms()
            delta_ms = ticks_diff(now_ms, past_ms)
            past_ms = now_ms

            self.update(delta_ms)
            yield delta_ms

    async def run(self):
        for _ in self.loop():
            await sleep_ms(_UPDATE_FREQUENCY_MS)

class TerminalRenderer:
    def __init__(self, engine, stream):
        self.engine = engine
        self.stream = stream

    def render(self, clear=True):
        if clear:
            self.stream.write('\033[5A\r')

        self.stream.write(f'{self.engine.player.points}\n')
        self.stream.write(f'{self.engine.level}\n')
        self.stream.write('-' * len(self.engine.bounds) + '\n')

        stage = [' '] * len(self.engine.bounds)

        for body in self.engine.bodies():
            if isinstance(body, Dot):
                stage[body.position - self.engine.bounds.min] = '\u00B7'
            elif isinstance(body, PowerPellet):
                stage[body.position - self.engine.bounds.min] = '\u25CF'
            elif isinstance(body, Player):
                stage[body.position - self.engine.bounds.min] = ('\033[33m\u15E7\033[0m'
                    if body.direction == _DIRECTION_RIGHT else '\033[33m\u15E4\033[0m')
            elif isinstance(body, Ghost):
                character = None

                if body.dead:
                    character = '\u2025'
                elif body.vulnerable:
                    character = '\033[34m\u15E3\033[0m'
                else:
                    character = '\033[31m\u15E3\033[0m'

                stage[body.position - self.engine.bounds.min] = character

        self.stream.write(''.join(stage) + '\n')
        self.stream.write('-' * len(self.engine.bounds) + '\n')

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
                engine.player.change_direction()

        asyncio.create_task(engine.run())
        loop.run_in_executor(None, control)

        while True:
            renderer.render()
            await asyncio.sleep(0.016)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    asyncio.run(main())
