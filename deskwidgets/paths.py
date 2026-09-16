"""Where everything lives. Works from a source checkout and from an installed copy
(<prefix>/share/deskwidgets, with the launcher in <prefix>/bin)."""
import os
import shutil

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.dirname(PKG_DIR)                 # source checkout or <prefix>/share/deskwidgets
ENGINE = os.path.join(DATA, 'engine')
FONTS = os.path.join(DATA, 'fonts')
BUILTIN_WIDGETS = os.path.join(DATA, 'widgets')
EXTENSION_SRC = os.path.join(DATA, 'gnome-extension')
ICON = os.path.join(DATA, 'data', 'icons', 'io.github.toksik.DeskWidgets.svg')

HOME = os.path.expanduser('~')
CONFIG = os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.join(HOME, '.config'), 'deskwidgets')
USER_WIDGETS = os.path.join(CONFIG, 'widgets')  # your own widgets: one folder each
LAYOUT = os.path.join(CONFIG, 'layout.json')
SETTINGS = os.path.join(CONFIG, 'settings.json')
CACHE = os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.join(HOME, '.cache'), 'deskwidgets')
PREVIEWS = os.path.join(CACHE, 'previews')
RUN = os.path.join(os.environ.get('XDG_RUNTIME_DIR') or f'/tmp/deskwidgets-{os.getuid()}', 'deskwidgets')
CONF = os.path.join(ENGINE, 'widget.conf')
FONTCONF = os.path.join(RUN, 'fonts.conf')
GRID_JSON = os.path.join(RUN, 'grid.json')
DRAGGING = os.path.join(RUN, 'dragging')

APP_ID = 'io.github.toksik.DeskWidgets'
APP_PATH = '/io/github/toksik/DeskWidgets'
GALLERY_ID = APP_ID + '.Gallery'
EXTENSION_UUID = 'deskwidgets@toksik.github.io'


def bin_path():
    """The `deskwidgets` launcher, used for autostart, the hotkey and right-click."""
    for cand in (os.path.join(DATA, 'bin', 'deskwidgets'),                              # source checkout
                 os.path.normpath(os.path.join(DATA, '..', '..', 'bin', 'deskwidgets'))):  # <prefix>/bin
        if os.access(cand, os.X_OK):
            return cand
    return shutil.which('deskwidgets') or 'deskwidgets'


def ensure_dirs():
    for d in (CONFIG, USER_WIDGETS, CACHE, PREVIEWS, RUN, os.path.join(RUN, 'opts'), os.path.join(RUN, 'img')):
        os.makedirs(d, exist_ok=True)
