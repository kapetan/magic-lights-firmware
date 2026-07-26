include('$(PORT_DIR)/boards/manifest.py')

freeze('.', (
    'main.py',
    'magic_lights/__init__.py',
    'magic_lights/magic_lights.py',
    'magic_lights/atomic_file.py'))
