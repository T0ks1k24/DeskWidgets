"""The `host` table: what the engine offers to a widget's Lua state besides Cairo.

Widgets do not call it directly, `engine/lua/lib.lua` wraps it into the `L` API
(L.sys, L.service, L.python, L.open_url ...). Everything here is OS-independent from the
widget's point of view; the OS-specific work lives in `deskwidgets.platform`.
"""
import os
import shlex
import subprocess
import sys
import webbrowser
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..core import paths
from ..core.util import log
from . import compat

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


class HostAPI:
    """Builds the `host` table for one widget."""

    def __init__(self, lua, ctx):
        self.lua, self.ctx = lua, ctx

    def table(self):
        lua, ctx = self.lua, self.ctx
        t = lua.table()
        for key, value in self._info().items():
            t[key] = value
        t['opts'] = lua.table_from(ctx.option_strings())
        t['fonts'] = lua.table_from({'cjk': ctx.fonts.cjk})
        t['sys'] = self._sys_table()
        for name, function in self._functions().items():
            t[name] = function
        return t

    def _info(self):
        ctx, inst = self.ctx, self.ctx.inst
        width, height = ctx.size_px
        return {
            'id': inst['id'], 'type': ctx.wtype.id, 'size': inst['size'], 'style': inst['style'],
            'accent': ctx.accent, 'lang': ctx.lang, 'demo': ctx.demo, 'scale': ctx.scale,
            'width': width, 'height': height, 'widget_dir': str(ctx.wtype.dir), 'run_dir': str(paths.RUN),
            'config_dir': str(paths.CONFIG), 'home': str(paths.HOME), 'os': 'windows' if sys.platform == 'win32' else 'linux',
            'sep': os.sep,
        }

    # system numbers
    def _sys_table(self):
        info = self.ctx.sysinfo
        convert = self._convert
        t = self.lua.table()
        for name in ('cpu', 'memory', 'disks', 'net', 'battery', 'temps'):
            t[name] = (lambda getter: lambda: convert(getter()))(getattr(info, name))
        t['disk'] = lambda path=None: convert(info.disk(_text(path) if path else None))
        t['top'] = lambda n=5: convert(info.top(int(n or 5)))
        t['uptime'] = info.uptime
        return t

    def _convert(self, value):
        """Python dicts and lists become Lua tables (lists start at 1); None becomes nil."""
        if isinstance(value, (dict, list, tuple)):
            return self.lua.table_from(value, recursive=True)
        return value

    # what widgets may call
    def _functions(self):
        ctx = self.ctx
        return {
            'log': lambda message: log(_text(message)),
            'load_image': lambda path: ctx.images.load(_text(path)),
            'service': self._service,
            'media_control': lambda command: bool(ctx.services.media_control(_text(command))),
            'run': lambda argv: run_background([_text(a) for a in _sequence(argv)], cwd=ctx.wtype.dir),
            'python': lambda script, args: run_background(
                [sys.executable, _text(script)] + [_text(a) for a in _sequence(args)], cwd=ctx.wtype.dir),
            'shell': lambda command: run_background(_text(command), shell=True, cwd=ctx.wtype.dir),
            'popen': lambda command: shell_output(_text(command)),
            'quote': quote,
            'open_url': lambda url: open_url(_text(url)),
            'notify': lambda title, body: ctx.notify(_text(title), _text(body)),
            'tz_offset': lambda zone: tz_offset(_text(zone)),
            'conky_parse': lambda text: compat.conky_parse(_text(text), ctx.sysinfo),
        }

    def _service(self, name):
        return self._convert(self.ctx.services.snapshot(_text(name)))


def _text(value):
    return value.decode('utf-8', 'replace') if isinstance(value, bytes) else str(value)


def _sequence(table):
    """A Lua array table as a Python list."""
    if table is None:
        return []
    try:
        return [table[i] for i in range(1, len(table) + 1)]
    except TypeError:
        return list(table)


def quote(text):
    if sys.platform == 'win32':
        return subprocess.list2cmdline([text])
    return shlex.quote(text)


def _window_flags():
    return CREATE_NO_WINDOW | DETACHED_PROCESS if sys.platform == 'win32' else 0


def helper_environment():
    """Environment for a widget's helper scripts: they can `import deskwidgets` even from a source checkout."""
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(filter(None, [str(paths.PACKAGE.parent), env.get('PYTHONPATH')]))
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


def run_background(command, shell=False, cwd=None):
    """Starts a program and does not wait for it (no console window on Windows)."""
    try:
        subprocess.Popen(command, shell=shell, cwd=cwd, env=helper_environment(), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=sys.platform != 'win32', creationflags=_window_flags())
    except OSError as e:
        log(f'cannot start {command if shell else command[0]}: {e}')


def shell_output(command, timeout=10):
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout,
                                errors='replace', creationflags=CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def open_url(target):
    """A web address in the browser, or a file/folder in its default application."""
    if target.startswith(('http://', 'https://', 'mailto:')):
        webbrowser.open(target)
    elif sys.platform == 'win32':
        os.startfile(target)   # noqa: S606
    else:
        run_background(['xdg-open', target])


def tz_offset(zone):
    """Seconds east of UTC that `zone` ('Europe/Kyiv') is at right now; 0 for an unknown zone."""
    try:
        return int(datetime.now(ZoneInfo(zone)).utcoffset().total_seconds())
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return 0


