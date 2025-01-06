include('$(PORT_DIR)/boards/manifest.py')

freeze('.', (
    'main.py',
    'firmware/__init__.py',
    'firmware/firmware.py'))
