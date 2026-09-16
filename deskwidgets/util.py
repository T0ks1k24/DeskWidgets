"""Small helpers shared by the service, the gallery and the CLI."""
import os
import sys


def log(msg):
    print(f'deskwidgets: {msg}', file=sys.stderr, flush=True)


def write_atomic(path, text):
    tmp = f'{path}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)


def clean(s):
    """One-line value for the key=value caches read by the Lua widgets."""
    return ' '.join(str(s).split()).replace('|', '/')
