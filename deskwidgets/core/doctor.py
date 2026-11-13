"""`deskwidgets doctor`: what works, what is missing, and what to install."""
import importlib
import shutil
import subprocess
import sys

from . import paths, settings as settings_mod
from .i18n import tr

WINDOWS = sys.platform == 'win32'


def _module(name):
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def run_checks():
    ok = True

    def row(name, good, note=''):
        nonlocal ok
        ok &= bool(good) or note.startswith('(')
        print(f"{'✔' if good else '✘'} {name}{(' — ' + note) if note and not good else ''}")

    row('Python', sys.version_info >= (3, 9), sys.version.split()[0])
    for module, package, why in (('cairo', 'pycairo', ''), ('lupa', 'lupa', ''), ('psutil', 'psutil', ''),
                                 ('PIL', 'Pillow', '')):
        row(package, _module(module), f'pip install {package}' if not _module(module) else why)
    lupa = _module('lupa')
    if lupa:
        lua = _module('lupa.lua54') or _module('lupa.lua53')
        row(tr('Lua у lupa', 'Lua inside lupa'), lua, tr('потрібна збірка lupa з Lua 5.3+', 'needs a lupa build with Lua 5.3+'))
    if WINDOWS:
        row('winsdk', _module('winsdk'), f'({tr("необов’язково", "optional")}: ' + tr('віджет «Зараз грає»', 'the Now playing widget') + ')')
        row('tzdata', _module('tzdata'), 'pip install tzdata')
    else:
        import os
        row(tr('дисплей X11 (або Xwayland)', 'X11 display (or Xwayland)'), os.environ.get('DISPLAY'), 'DISPLAY')
        row('libX11', _can_load('libX11.so.6'), tr('встановіть libx11', 'install libx11'))
        row('libXext', _can_load('libXext.so.6'), f'({tr("для сітки під час перетягування", "for the grid while dragging")})')
        font = settings_mod.load()['font']
        font = 'Ubuntu Sans' if font == 'auto' else font
        found = _run(['fc-list', font])
        row(tr('шрифт ', 'font ') + font, found, tr('буде запасний sans-serif', 'a fallback sans-serif will be used'))
        row(tr('японський шрифт (аніме-віджети)', 'Japanese font (anime widgets)'), _run(['fc-list', ':lang=ja']),
            '(fonts-noto-cjk)')
    gi = _module('gi')
    for mod, ver, pkg in (('Gtk', '4.0', 'gir1.2-gtk-4.0'), ('Adw', '1', 'gir1.2-adw-1')):
        good = False
        if gi:
            try:
                gi.require_version(mod, ver)
                importlib.import_module('gi.repository.' + mod)
                good = True
            except (ImportError, ValueError):
                pass
        row(f'{mod} {ver} ({tr("галерея", "gallery")})', good, f'({pkg})')
    if not WINDOWS:
        eds = False
        if gi:
            try:
                gi.require_version('ECal', '2.0')
                importlib.import_module('gi.repository.ECal')
                eds = True
            except (ImportError, ValueError):
                pass
        row(tr('календарі GNOME (віджет «Події»)', 'GNOME calendars ("Up next" widget)'), eds, '(gir1.2-ecal-2.0)')
    for tool, why in (('nmap', tr('перевірка мережі', 'LAN watch')), ('docker', 'Docker')) + \
            ((('upower', tr('батареї', 'batteries')),) if not WINDOWS else ()):
        row(tool, shutil.which(tool), f'({tr("необов’язково", "optional")}: {why})')
    if not WINDOWS:
        from .. import cli
        from ..providers import hid_mice
        for m in hid_mice.find():
            row(tr('заряд миші ', 'mouse battery ') + m['name'], m['access'],
                tr('потрібне правило udev, виконайте: ', 'needs the udev rule, run: ') + cli.udev_command())
    hotkey_row(row)
    from ..service import ipc
    row(tr('сервіс працює', 'service running'), ipc.is_running(), f'({tr("запустіть", "start it")}: deskwidgets)')
    print(f'{tr("Віджети", "Widgets")}: {paths.BUILTIN_WIDGETS}  ·  {tr("мої", "mine")}: {paths.USER_WIDGETS}')
    return 0 if ok else 1


def hotkey_row(row):
    """The gallery hotkey: Windows registers it in the service, GNOME keeps it as a custom shortcut."""
    if WINDOWS:
        row(tr('гаряча клавіша Ctrl+Alt+W', 'hotkey Ctrl+Alt+W'), settings_mod.load()['hotkey'],
            tr('вимкнена в налаштуваннях', 'switched off in the settings'))
        return
    from ..platform import integration
    state = integration.hotkey_state()
    if state is None:
        row(tr('гаряча клавіша', 'hotkey'), False, f'({tr("не GNOME: прив’яжіть до клавіші команду", "not GNOME: bind this command to a key")}: '
                                                  f'deskwidgets gallery)')
        return
    name = tr('гаряча клавіша ', 'hotkey ') + (state['binding'] or integration.HOTKEY)
    if not state['registered']:
        row(name, False, tr('не створена: deskwidgets setup', 'not created: deskwidgets setup'))
    elif state['command'] != state['wanted']:
        row(name, False, tr('вказує на інший запуск; перезапустіть сервіс, щоб виправити', 'points at another install; restart the service to fix it'))
    else:
        row(name, True)


def _can_load(name):
    import ctypes
    try:
        ctypes.CDLL(name)
        return True
    except OSError:
        return False


def _run(command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''
