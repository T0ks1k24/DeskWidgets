"""Where everything lives. Works from a source checkout, from `pip install` and from a distro
package, on Linux and on Windows: the data files sit inside the Python package."""
import os
import sys
import tempfile
from pathlib import Path

APP_ID = 'io.github.toksik.DeskWidgets'
GALLERY_ID = APP_ID + '.Gallery'

WINDOWS = sys.platform == 'win32'

# read-only data shipped with the package
PACKAGE = Path(__file__).resolve().parent.parent
BUILTIN_WIDGETS = PACKAGE / 'widgets'
LUA = PACKAGE / 'engine' / 'lua'
FONTS = PACKAGE / 'assets' / 'fonts'
ICON = PACKAGE / 'assets' / 'icons' / (APP_ID + '.svg')
ICON_PNG = PACKAGE / 'assets' / 'icons' / (APP_ID + '.png')
UDEV_RULES = PACKAGE / 'assets' / 'udev' / '70-deskwidgets-hid.rules'

HOME = Path.home()


def _xdg(variable, fallback):
    return Path(os.environ.get(variable) or HOME / fallback)


def _base_dirs():
    """(config, cache, runtime) for the user, following each platform's conventions."""
    if WINDOWS:
        roaming = Path(os.environ.get('APPDATA') or HOME / 'AppData' / 'Roaming')
        local = Path(os.environ.get('LOCALAPPDATA') or HOME / 'AppData' / 'Local')
        return roaming / 'DeskWidgets', local / 'DeskWidgets' / 'cache', local / 'DeskWidgets' / 'run'
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if not runtime:
        runtime = Path(tempfile.gettempdir()) / f'deskwidgets-{os.getuid()}'
    return (_xdg('XDG_CONFIG_HOME', '.config') / 'deskwidgets', _xdg('XDG_CACHE_HOME', '.cache') / 'deskwidgets',
            Path(runtime) / 'deskwidgets')


CONFIG, CACHE, RUN = _base_dirs()

USER_WIDGETS = CONFIG / 'widgets'   # your own widgets: one folder each
LAYOUT = CONFIG / 'layout.json'
SETTINGS = CONFIG / 'settings.json'
PREVIEWS = CACHE / 'previews'
IMAGES = CACHE / 'images'           # converted copies of pictures (and cover art)
SOCKET = RUN / 'control'            # the service listens here (a Unix socket, or a named pipe on Windows)
AUTH_KEY = RUN / 'control.key'

# DeskWidgets 1.x kept its files here
OLD_LAYOUT = HOME / '.config' / 'conky' / 'mac' / 'layout.json'
OLD_KNOWN_DEVICES = HOME / '.config' / 'conky' / 'network_known.txt'


def ensure_dirs():
    for directory in (CONFIG, USER_WIDGETS, CACHE, PREVIEWS, IMAGES, RUN):
        directory.mkdir(parents=True, exist_ok=True)
    if not WINDOWS:
        RUN.chmod(0o700)
