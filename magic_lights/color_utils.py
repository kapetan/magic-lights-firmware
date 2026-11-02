try:
    from .color_utils_native import (
        brightness_gamma8 as native_brightness_gamma8,
        brightness_gamma8_component as native_brightness_gamma8_component,
        hsv_to_rgb as native_hsv_to_rgb)
except ImportError:
    native_brightness_gamma8 = None
    native_brightness_gamma8_component = None
    native_hsv_to_rgb = None

def brightness(rgb, brightness):
    brightness = (brightness + 1) & 0xff

    if brightness:
        return tuple((component * brightness) >> 8 for component in rgb)

    return rgb
