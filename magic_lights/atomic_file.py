import os
import sys

class AtomicFile:
    @classmethod
    def open(cls, name, mode='r', **kwargs):
        return cls(name, mode, os.sync, **kwargs)

    def __init__(self, name, mode='r', sync=os.sync, **kwargs):
        self._name = name
        self._mode = mode
        self._sync = sync
        self._kwargs = kwargs

        self._tmp = name + '.tmp'
        self._file = None

    def __enter__(self):
        self._file = open(self._tmp, self._mode, **self._kwargs)
        return self._file

    def __exit__(self, exc_type, exc_value, traceback):
        success = False

        try:
            self._file.close()

            if not exc_type:
                self._sync()
                os.rename(self._tmp, self._name)
                success = True
        finally:
            if not success:
                try:
                    os.unlink(self._tmp)
                except OSError as e:
                    sys.print_exception(e)
