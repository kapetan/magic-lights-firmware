import io

try:
    from ._files import FILES
except ImportError:
    FILES = {}

def open(file, mode='r', **kwargs):
    if file in FILES:
        content = FILES[file]

        if 'b' in mode:
            return io.BytesIO(content)
        else:
            encoding = kwargs.get('encoding', 'utf-8')
            return io.StringIO(content.decode(encoding))

    return io.open(file, mode, **kwargs)
