"""Fitting into the desktop: autostart, the app-menu entry, the gallery hotkey, notifications.

Linux: XDG autostart and menu entries, a GNOME custom keybinding (other desktops: bind
`deskwidgets gallery` to a key yourself). Windows: the Run registry key and a Start-menu shortcut;
the Windows hotkey and tray icon live in the service (`platform/win32.py`).
Every step is optional and safe to repeat.
"""
import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..core import paths
from ..core.i18n import tr
from ..core.util import log, write_atomic

WINDOWS = sys.platform == 'win32'

CONFIG_HOME = Path(os.environ.get('XDG_CONFIG_HOME') or paths.HOME / '.config')
DATA_HOME = Path(os.environ.get('XDG_DATA_HOME') or paths.HOME / '.local' / 'share')
AUTOSTART = CONFIG_HOME / 'autostart' / (paths.APP_ID + '.desktop')
OLD_AUTOSTART = paths.HOME / '.config' / 'autostart' / 'conky.desktop'   # DeskWidgets 1.x
APPLICATIONS = DATA_HOME / 'applications'
KEYBINDING_PATH = '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/deskwidgets/'
MEDIA_KEYS = 'org.gnome.settings-daemon.plugins.media-keys'
HOTKEY = '<Super><Alt>w'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'


# how to start ourselves
def command(*args):
    """argv that starts `deskwidgets <args>` with this Python, wherever DeskWidgets is installed."""
    python = sys.executable
    if WINDOWS and args and args[0] == 'run':
        windowless = Path(python).with_name('pythonw.exe')
        python = str(windowless) if windowless.exists() else python
    return [python, '-m', 'deskwidgets', *args]


def _checkout():
    """The source folder when running from a git checkout (not installed), else None."""
    root = paths.PACKAGE.parent
    return root if (root / 'pyproject.toml').exists() and (root / '.git').exists() else None


def _desktop_exec(*args):
    """An Exec= line (desktop-entry quoting rules)."""
    def quote(text):
        text = text.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
        return f'"{text}"' if any(c in text for c in ' \t\'"<>~|&;*?#()') or not text else text
    argv = command(*args)
    prefix = ['env', f'PYTHONPATH={_checkout()}'] if _checkout() else []
    return ' '.join(quote(a) for a in prefix + argv)


# autostart
def set_autostart(enabled):
    if WINDOWS:
        return _set_run_key(enabled)
    if enabled:
        write_atomic(AUTOSTART, f'''[Desktop Entry]
Type=Application
Name=DeskWidgets
Comment={tr('Віджети робочого столу', 'Desktop widgets')}
Exec={_desktop_exec('run')}
Icon=preferences-desktop-theme
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5
''')
    else:
        AUTOSTART.unlink(missing_ok=True)
    return True


def autostart_enabled():
    if WINDOWS:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, 'DeskWidgets')
            return True
        except OSError:
            return False
    return AUTOSTART.exists()


def _set_run_key(enabled):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, 'DeskWidgets', 0, winreg.REG_SZ, subprocess.list2cmdline(command('run')))
        else:
            try:
                winreg.DeleteValue(key, 'DeskWidgets')
            except FileNotFoundError:
                pass
    return True


def retire_v1():
    """1.x started from ~/.config/autostart/conky.desktop (runs start.sh). Disable it, keep a copy."""
    try:
        if 'conky/mac/start.sh' not in OLD_AUTOSTART.read_text(encoding='utf-8'):
            return False
        OLD_AUTOSTART.replace(OLD_AUTOSTART.with_name(OLD_AUTOSTART.name + '.disabled-by-deskwidgets'))
        log('old autostart (conky.desktop) disabled')
        return True
    except OSError:
        return False


# the menu entry for the gallery (packages ship their own on Linux)
def install_menu_entry():
    if WINDOWS:
        return _install_start_menu()
    if Path(f'/usr/share/applications/{paths.GALLERY_ID}.desktop').exists():
        return True
    icon = str(paths.ICON) if paths.ICON.exists() else 'preferences-desktop-theme'
    write_atomic(APPLICATIONS / (paths.GALLERY_ID + '.desktop'), f'''[Desktop Entry]
Type=Application
Name={tr('Віджети', 'Widgets')}
Comment={tr('Додати, налаштувати й розставити віджети робочого столу', 'Add, configure and arrange desktop widgets')}
Exec={_desktop_exec('gallery')}
Icon={icon}
Categories=Utility;GTK;
Keywords=widgets;clock;weather;anime;desktop;віджети;
StartupNotify=true
''')
    return True


def _install_start_menu():
    """A Start-menu shortcut to the gallery, made with the Windows scripting host (no extra packages)."""
    programs = Path(os.environ.get('APPDATA', '')) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs'
    if not programs.is_dir():
        return False
    icon = _windows_icon()
    argv = command('gallery')
    script = ('$s=(New-Object -ComObject WScript.Shell).CreateShortcut($args[0]);'
              '$s.TargetPath=$args[1];$s.Arguments=$args[2];$s.IconLocation=$args[3];$s.Save()')
    done = subprocess.run(
        ['powershell', '-NoProfile', '-Command', script, str(programs / 'DeskWidgets.lnk'),
         str(Path(argv[0]).with_name('pythonw.exe') if Path(argv[0]).with_name('pythonw.exe').exists() else argv[0]),
         ' '.join(argv[1:]), icon], capture_output=True, creationflags=0x08000000)
    return done.returncode == 0


def _windows_icon():
    """DeskWidgets.ico next to the settings (Windows shortcuts need .ico), made from the PNG with Pillow."""
    ico = paths.CONFIG / 'DeskWidgets.ico'
    try:
        from PIL import Image
        with Image.open(paths.ICON_PNG) as img:
            img.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
        return str(ico)
    except (ImportError, OSError):
        return ''


# GNOME: Super+Alt+W opens the gallery
def _gsettings(*args):
    try:
        done = subprocess.run(['gsettings', *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def is_gnome():
    return 'GNOME' in os.environ.get('XDG_CURRENT_DESKTOP', '').upper() and shutil.which('gsettings') is not None


def set_hotkey(enabled):
    if not is_gnome():
        return False
    raw = _gsettings('get', MEDIA_KEYS, 'custom-keybindings')
    if raw is None:
        return False
    try:
        current = list(ast.literal_eval(raw.removeprefix('@as ')))
    except (ValueError, SyntaxError):
        return False
    schema = f'{MEDIA_KEYS}.custom-keybinding:{KEYBINDING_PATH}'
    if enabled:
        for key, value in (('name', tr('Віджети робочого столу', 'Desktop widgets')),
                           ('command', _shell_command('gallery')), ('binding', HOTKEY)):
            _gsettings('set', schema, key, value)
        if KEYBINDING_PATH not in current:
            _gsettings('set', MEDIA_KEYS, 'custom-keybindings', str(current + [KEYBINDING_PATH]))
    elif KEYBINDING_PATH in current:
        _gsettings('set', MEDIA_KEYS, 'custom-keybindings', str([p for p in current if p != KEYBINDING_PATH]))
    return True


def _shell_command(*args):
    import shlex
    argv = command(*args)
    prefix = ['env', f'PYTHONPATH={_checkout()}'] if _checkout() else []
    return ' '.join(shlex.quote(a) for a in prefix + argv)


def setup(autostart=True, hotkey=True):
    """First-run setup. Returns what was done: {'autostart': bool, 'hotkey': bool, ...}."""
    retire_v1()
    done = {'autostart': set_autostart(autostart), 'menu': install_menu_entry()}
    if not WINDOWS:
        done['hotkey'] = set_hotkey(hotkey)
    return done


# notifications from processes that have no tray icon
def notify(title, body=''):
    if WINDOWS:
        log(f'{title}: {body}')   # the service shows these through its tray icon (`deskwidgets notify`)
    elif shutil.which('notify-send'):
        subprocess.run(['notify-send', '-u', 'critical', '-i', 'network-wireless', title, body], capture_output=True)
