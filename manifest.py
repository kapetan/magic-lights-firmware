include('$(PORT_DIR)/boards/RPI_PICO_W/manifest.py')

freeze('.', (
    'main.py',
    'magic_lights/__init__.py',
    'magic_lights/magic_lights.py',
    'magic_lights/aioudp.py',
    'magic_lights/aiodns.py',
    'magic_lights/aiontp.py',
    'magic_lights/pacman.py',
    'magic_lights/firework.py',
    'magic_lights/ansi_color.py',
    'magic_lights/atomic_file.py',
    'magic_lights/color_utils.py',
    'magic_lights/engine_utils.py',
    'magic_lights/fs.py',
    'magic_lights/_files.py',
    'magic_lights/version.py',
    'magic_lights/color_utils_native.mpy',
    'magic_lights/firework_native.mpy'))

freeze('lib/microdot/src', (
    'microdot/__init__.py',
    'microdot/microdot.py',
    'microdot/websocket.py',
    'microdot/helpers.py'))

freeze('lib/micropython-suntime', (
    'suntime/__init__.py',
    'suntime/suntime.py'))
