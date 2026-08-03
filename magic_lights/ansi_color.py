def nearest_color(red, green, blue):
    candidate = None
    candidate_distance = None

    for i, (r, g, b) in enumerate(_COLOR_TABLE):
        distance = distance_squared(red, green, blue, r, g, b)

        if candidate is None or distance < candidate_distance:
            candidate = i
            candidate_distance = distance

    return candidate

def build_color_table():
    table = [None] * 256

    for code in range(0, 16):
        if code > 8:
            level = 255
        elif code == 7:
            level = 229

        else:
            level = 205

        r = 127 if code == 8 else level if (code & 1) != 0 else 92 if code == 12 else 0
        g = 127 if code == 8 else level if (code & 2) != 0 else 92 if code == 12 else 0
        b = 127 if code == 8 else 238 if code == 4 else level if (code & 4) != 0 else 0

        table[code] = (r, g, b)

    for red in range(0, 6):
        for green in range(0, 6):
            for blue in range(0, 6):
                code = 16 + (red * 36) + (green * 6) + blue
                r = red * 40 + 55 if red != 0 else 0
                g = green * 40 + 55 if green != 0 else 0
                b = blue * 40 + 55 if blue != 0 else 0

                table[code] = (r, g, b)

    for gray in range(0, 24):
        level = gray * 10 + 8
        code = 232 + gray

        table[code] = (level, level, level)

    return table

def distance_squared(r1, g1, b1, r2, g2, b2):
    return (r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2

_COLOR_TABLE = build_color_table()
