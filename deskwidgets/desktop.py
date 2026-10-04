"""Desktop integration: autostart, the Super+Alt+W hotkey (GNOME), the GNOME Shell grid helper,
the app-menu entry, and moving the 1.x setup out of the way."""
import os
import shutil
import subprocess

from . import paths
from .i18n import tr
from .util import log, write_atomic

AUTOSTART = os.path.join(paths.HOME, '.config', 'autostart', paths.APP_ID + '.desktop')
OLD_AUTOSTART = os.path.join(paths.HOME, '.config', 'autostart', 'conky.desktop')   # DeskWidgets 1.x
APPLICATIONS = os.path.join(paths.HOME, '.local', 'share', 'applications')
KEYBINDING_PATH = '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/deskwidgets/'
HOTKEY = '<Super><Alt>w'
USER_EXTENSIONS = os.path.join(paths.HOME, '.local', 'share', 'gnome-shell', 'extensions')
SYSTEM_EXTENSIONS = ['/usr/share/gnome-shell/extensions', '/usr/local/share/gnome-shell/extensions']


def _gsettings(schema, path=None):
    try:
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        if source is None or source.lookup(schema, True) is None:
            return None
        return Gio.Settings.new_with_path(schema, path) if path else Gio.Settings.new(schema)
    except Exception:
        return None


def is_gnome():
    return 'GNOME' in os.environ.get('XDG_CURRENT_DESKTOP', '').upper()


# autostart
def set_autostart(enabled):
    if enabled:
        os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
        write_atomic(AUTOSTART, f'''[Desktop Entry]
Type=Application
Name=DeskWidgets
Comment={tr('Віджети робочого столу', 'Desktop widgets')}
Exec="{paths.bin_path()}" run
Icon=preferences-desktop-theme
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5
''')
    elif os.path.exists(AUTOSTART):
        os.unlink(AUTOSTART)


def retire_v1():
    """1.x started from ~/.config/autostart/conky.desktop (runs start.sh). Disable it, keep a copy."""
    try:
        with open(OLD_AUTOSTART, encoding='utf-8') as f:
            if 'conky/mac/start.sh' not in f.read():
                return False
        os.replace(OLD_AUTOSTART, OLD_AUTOSTART + '.disabled-by-deskwidgets')
        log('old autostart (conky.desktop) disabled')
        return True
    except OSError:
        return False


# hotkey (GNOME custom keybinding)
def set_hotkey(enabled):
    media = _gsettings('org.gnome.settings-daemon.plugins.media-keys')
    if media is None:
        return False
    current = list(media.get_strv('custom-keybindings'))
    if enabled:
        kb = _gsettings('org.gnome.settings-daemon.plugins.media-keys.custom-keybinding', KEYBINDING_PATH)
        kb.set_string('name', tr('Віджети робочого столу', 'Desktop widgets'))
        kb.set_string('command', f'"{paths.bin_path()}" gallery')
        kb.set_string('binding', HOTKEY)
        if KEYBINDING_PATH not in current:
            media.set_strv('custom-keybindings', current + [KEYBINDING_PATH])
    elif KEYBINDING_PATH in current:
        media.set_strv('custom-keybindings', [p for p in current if p != KEYBINDING_PATH])
    return True


# GNOME Shell helper: grid overlay while dragging, snap on release
def extension_installed():
    for base in SYSTEM_EXTENSIONS + [USER_EXTENSIONS]:
        if os.path.isfile(os.path.join(base, paths.EXTENSION_UUID, 'metadata.json')):
            return base
    return None


def install_extension():
    """Copies the helper into ~/.local/share/gnome-shell/extensions unless the package installed it."""
    src = os.path.join(paths.EXTENSION_SRC, paths.EXTENSION_UUID)
    where = extension_installed()
    if where and where != USER_EXTENSIONS:
        return where
    if not os.path.isdir(src):
        return None
    dst = os.path.join(USER_EXTENSIONS, paths.EXTENSION_UUID)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return USER_EXTENSIONS


def set_extension(enabled):
    """Enables the helper for the next login (GNOME on Wayland loads new extensions only then)."""
    shell = _gsettings('org.gnome.shell')
    if shell is None:
        return False
    if enabled and not install_extension():
        return False
    on = list(shell.get_strv('enabled-extensions'))
    off = list(shell.get_strv('disabled-extensions'))
    uuid = paths.EXTENSION_UUID
    if enabled:
        if uuid not in on:
            shell.set_strv('enabled-extensions', on + [uuid])
        if uuid in off:
            shell.set_strv('disabled-extensions', [u for u in off if u != uuid])
        subprocess.run(['gnome-extensions', 'enable', uuid], capture_output=True)
    else:
        if uuid in on:
            shell.set_strv('enabled-extensions', [u for u in on if u != uuid])
        subprocess.run(['gnome-extensions', 'disable', uuid], capture_output=True)
    return True


def extension_state():
    """'active', 'pending' (needs a re-login), 'off' or 'unavailable'."""
    if not is_gnome() or shutil.which('gnome-extensions') is None:
        return 'unavailable'
    out = subprocess.run(['gnome-extensions', 'info', paths.EXTENSION_UUID], capture_output=True, text=True).stdout
    if 'State: ACTIVE' in out or 'State: ENABLED' in out:
        return 'active'
    shell = _gsettings('org.gnome.shell')
    if shell and paths.EXTENSION_UUID in shell.get_strv('enabled-extensions'):
        return 'pending'
    return 'off'


# app menu entry for source checkouts (packages ship their own)
def install_menu_entry():
    if os.path.exists(f'/usr/share/applications/{paths.GALLERY_ID}.desktop'):
        return
    os.makedirs(APPLICATIONS, exist_ok=True)
    write_atomic(os.path.join(APPLICATIONS, paths.GALLERY_ID + '.desktop'), f'''[Desktop Entry]
Type=Application
Name={tr('Віджети', 'Widgets')}
Comment={tr('Додати, налаштувати й розставити віджети робочого столу', 'Add, configure and arrange desktop widgets')}
Exec="{paths.bin_path()}" gallery
Icon={paths.ICON if os.path.exists(paths.ICON) else 'preferences-desktop-theme'}
Categories=Utility;GTK;
Keywords=widgets;conky;desktop;clock;weather;anime;віджети;
StartupNotify=true
''')


def setup(autostart=True, hotkey=True, extension=True):
    """First-run setup. Every step is optional and safe to repeat."""
    retire_v1()
    set_autostart(autostart)
    install_menu_entry()
    done = {'autostart': autostart}
    if is_gnome():
        done['hotkey'] = set_hotkey(hotkey)
        done['extension'] = set_extension(extension) if extension else False
    return done
