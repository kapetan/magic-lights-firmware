class Bounds:
    def __init__(self, min, max):
        self.min = min
        self.max = max

    def __str__(self):
        return f'[{self.min}, {self.max}]'

    def __len__(self):
        return self.max - self.min + 1

    def __contains__(self, other):
        return self.min <= other <= self.max

    def __truediv__(self, divisor):
        return (self.min + self.max) / divisor

    def __floordiv__(self, divisor):
        return (self.min + self.max) // divisor
