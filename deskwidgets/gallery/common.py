"""What every gallery front end (GTK, Tk) shares: talking to the service and making pictures.

The gallery is a separate process from the service. It never draws widgets itself in-process:
Cairo's fonts are fixed per process (see engine/fonts.py), and a GUI toolkit may already have set
them up, so pictures are made by `deskwidgets preview(s)` child processes.
"""
import json
import subprocess
import sys
from pathlib import Path

from .. import VERSION
from ..core import catalog, paths, settings as settings_mod
from ..core.grid import Grid
from ..service import ipc

CALL_TIMEOUT = 6.0


def call(command, **args):
    """Sends a command to the service, starting it first if needed.
    Returns (True, answer) or (False, a message fit to show the user)."""
    if not ipc.ensure_running():
        return False, 'The DeskWidgets service did not start (see service.log in ' + str(paths.CACHE) + ')'
    try:
        return True, ipc.request(command, timeout=CALL_TIMEOUT, **args)
    except (RuntimeError, TimeoutError, ipc.ServiceNotRunning) as e:
        return False, str(e)


def read_layout():
    try:
        data = json.loads(paths.LAYOUT.read_text(encoding='utf-8'))
        return data.get('instances', []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []


def window_px(size):
    """Size in pixels of a widget at 100 %, for sizing thumbnails."""
    return Grid(1920, 1080, {**settings_mod.DEFAULTS, 'scale': 1.0}).window_px(size)


def thumbs_dir(lang=None):
    return paths.PREVIEWS / VERSION / (lang or settings_mod.language())


def thumbnail_path(type_id, lang=None):
    return thumbs_dir(lang) / f'{type_id}.png'


def instance_title(inst, t):
    opts = inst.get('options') or {}
    extra = opts.get('title') or (opts.get('city') if inst['type'] == 'weather' else None)
    return f'{t.title} — {extra}' if extra and extra != t.title else t.title


def plural_widgets(n):
    if settings_mod.language() == 'en':
        return f'{n} widget' + ('' if n == 1 else 's')
    if n % 10 == 1 and n % 100 != 11:
        return f'{n} віджет'
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f'{n} віджети'
    return f'{n} віджетів'


def python_command(*args):
    return [sys.executable, '-m', 'deskwidgets', *args]


def start_thumbnails(only=None, lang=None):
    """Renders the gallery thumbnails in a child process; returns the Popen (poll() is None while it runs)."""
    command = python_command('previews', *(['--lang', lang] if lang else []), *(['--only', ','.join(only)] if only else []))
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=_env(), creationflags=0x08000000 if sys.platform == 'win32' else 0)


def start_preview(type_id, out, size, style, accent, options, lang):
    """Renders one widget with exactly these settings into `out` (a PNG) in a child process."""
    command = python_command('preview', type_id, '--size', size, '--style', style, '--accent', accent or 'auto',
                             '--lang', lang, '-o', str(out))
    for key, value in (options or {}).items():
        command += ['--option', f'{key}={_option_text(value)}']
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=_env(), creationflags=0x08000000 if sys.platform == 'win32' else 0)


def _option_text(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def _env():
    import os
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(filter(None, [str(paths.PACKAGE.parent), env.get('PYTHONPATH')]))
    return env


def all_types():
    return {k: t for k, t in catalog.discover().items() if t.supported}
