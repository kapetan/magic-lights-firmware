try:
    from .color_utils_native import (
        brightness_gamma8 as native_brightness_gamma8,
        brightness_gamma8_component as native_brightness_gamma8_component)
except ImportError:
    native_brightness_gamma8 = None
    native_brightness_gamma8_component = None

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

def brightness(rgb, brightness):
    brightness = (brightness + 1) & 0xff

    if brightness:
        return tuple((component * brightness) >> 8 for component in rgb)

    return rgb
