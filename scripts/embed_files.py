from os import path

_ROOT = path.join(path.dirname(path.realpath(__file__)), '..')

def embed_files(filenames, root, stream, columns=8):
    stream.write('FILES = {\n')

    for i, name in enumerate(filenames):
        if i > 0:
            stream.write(',\n')

        stream.write("    '/")
        stream.write(path.relpath(name, root))
        stream.write("': bytes((")

        with open(path.join(root, name), 'rb') as file:
            j = 0
            buffer = bytearray(1)

            while file.readinto(buffer) > 0:
                if j % columns == 0:
                    if j > 0:
                        stream.write(',')

                    stream.write('\n        ')
                else:
                    stream.write(', ')

                stream.write('0x%02x' % buffer[0])
                j += 1

        stream.write('\n    ))')

    stream.write('\n}\n')

if __name__ == '__main__':
    import sys

    filenames = sys.argv[1:]
    embed_files(filenames, _ROOT, sys.stdout)
