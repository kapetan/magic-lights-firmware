include('$(PORT_DIR)/boards/RPI_PICO_W/manifest.py')

freeze('.', (
    'main.py',
    'magic_lights/__init__.py',
    'magic_lights/magic_lights.py',
    'magic_lights/pacman.py',
    'magic_lights/firework.py',
    'magic_lights/ansi_color.py',
    'magic_lights/atomic_file.py',
    'magic_lights/color_utils.py',
    'magic_lights/engine_utils.py',
    'magic_lights/fs.py',
    'magic_lights/_files.py',
    'magic_lights/version.py'))

freeze('lib/microdot', (
    'microdot/__init__.py',
    'microdot/microdot.py',
    'microdot/websocket.py',
    'microdot/helpers.py'))
