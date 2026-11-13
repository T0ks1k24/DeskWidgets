"""Small helpers shared by every part of DeskWidgets."""
import os
import sys
import tempfile
import time
from pathlib import Path


def log(message):
    print(f'deskwidgets: {message}', file=sys.stderr, flush=True)


def write_atomic(path, text):
    """Writes `text` so that readers see the old or the new file, never half of one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=f'.{path.name}-', delete=False) as f:
            tmp = f.name
            f.write(text)
        replace(tmp, path)
    finally:
        if tmp is not None and os.path.exists(tmp):
            os.unlink(tmp)


def replace(src, dst, attempts=20):
    """os.replace that survives Windows' short sharing violations while a reader has the file open."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1 or sys.platform != 'win32':
                raise
            time.sleep(0.02)


def clean(text):
    """One-line value: whitespace collapsed."""
    return ' '.join(str(text).split())
