"""The service. One process, one loop: it owns every widget window.

* draws each widget instance in its own window and redraws it on its interval
* a widget dragged with the mouse (or moved with Super+drag by the window manager) snaps to the
  nearest free grid cell when you let go; the grid is shown while you drag
* feeds widgets live data (`providers`: media player, calendars, Docker, batteries)
* is controlled through `service/ipc.py`: add, remove, configure, move, arrange, reload, settings,
  gallery, notify, list, status, quit
"""
import os
import signal
import subprocess
import sys

from .. import VERSION
from ..core import catalog, paths, settings as settings_mod
from ..core.grid import Grid
from ..core.i18n import set_lang, tr
from ..core.layout import Layout
from ..core.util import log
from ..engine import fonts
from ..engine.images import ImageCache
from ..engine.sysinfo import SysInfo
from ..providers import ServiceHub
from . import ipc, legacy
from .instance import WidgetInstance
from .overlay import GridOverlay

GALLERY_HOTKEY = {'win32': 'ctrl+alt+w'}


class Daemon:
    def __init__(self, backend=None):
        self.backend = backend
        self.settings = {}
        self.lang = 'en'
        self.fonts = None
        self.sysinfo = SysInfo()
        self.images = ImageCache()
        self.hub = ServiceHub()
        self.types = {}
        self.layout = None
        self.grid = None
        self.origin = (0, 0)
        self.instances = {}
        self.overlay = GridOverlay(self)
        self.server = None
        self.tray = None
        self.restart_requested = False
        self._screen_timer = None

    # start and stop
    def run(self):
        """Runs until `quit`. Returns the process exit code."""
        os.environ[legacy.MARKER] = '1'   # tells a newer start-up that this is not a 2.x service
        try:
            self.startup()
        except ipc.AlreadyRunning:
            log('DeskWidgets is already running')
            return 0
        except Exception as e:
            log(f'cannot start: {e}')
            self.shutdown()
            return 1
        for name in ('SIGINT', 'SIGTERM', 'SIGHUP', 'SIGBREAK'):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_: self.backend.quit())
        try:
            self.backend.run()
        finally:
            self.shutdown()
        if self.restart_requested:   # the font changed: Cairo cannot swap fonts under a running process
            os.execv(sys.executable, [sys.executable, '-m', 'deskwidgets', 'run'])
        return 0

    def startup(self):
        paths.ensure_dirs()
        legacy.stop_old_versions()
        self.load_settings()
        self.fonts = fonts.setup(self.settings)   # before anything is drawn
        if self.backend is None:
            from ..platform import create_backend
            self.backend = create_backend()
        self.server = ipc.ControlServer(self.handle, self.backend)
        self.server.start()
        self.backend.on_screen_changed = self.screen_changed
        self.types = catalog.discover()
        self.rebuild_grid()
        self.layout = Layout(self.grid, self.types, self.settings)
        self.layout.load()
        self.layout.save()
        self.sync_services()
        for inst in self.layout.instances:
            self.start_instance(inst['id'])
        self.setup_desktop_integration()
        log(f'DeskWidgets {VERSION}: {len(self.instances)} widgets on a {self.grid.cols}×{self.grid.rows} grid')

    def shutdown(self):
        for instance in list(self.instances.values()):
            instance.stop()
        self.instances.clear()
        self.overlay.hide()
        if self.tray:
            self.tray.close()
        self.hub.stop()
        self.sysinfo.stop()
        if self.server:
            self.server.stop()

    # settings and geometry
    def load_settings(self):
        self.settings = settings_mod.load()
        self.lang = settings_mod.language(self.settings)
        set_lang(self.lang)

    def rebuild_grid(self):
        """The grid for this screen: the work area minus the margins from the settings, at the screen's DPI."""
        x, y, width, height = self.backend.work_area()
        dpi, s = self.backend.dpi_scale(), self.settings
        self.origin = (x, y)
        self.grid = Grid(width, height, {
            **s, 'scale': s['scale'] * dpi, 'gap': round(s['gap'] * dpi), 'margin_side': round(s['margin_side'] * dpi),
            'margin_top': round(s['margin_top'] * dpi), 'margin_bottom': round(s['margin_bottom'] * dpi)})

    def window_xy(self, col, row):
        gx, gy = self.grid.window_xy(col, row)
        return gx + self.origin[0], gy + self.origin[1]

    def nearest_cell(self, instance, pos):
        x, y = instance.window_origin(pos)
        return self.grid.nearest_cell(x - self.origin[0], y - self.origin[1], instance.inst['size'])

    def screen_changed(self):
        """A monitor was plugged in or the resolution changed: lay the widgets out again (debounced)."""
        if self._screen_timer:
            self._screen_timer.cancel()
        self._screen_timer = self.backend.call_later(0.8, self.apply_screen)

    def apply_screen(self):
        self._screen_timer = None
        self.rebuild_grid()
        self.layout.regrid(self.grid)
        self.layout.save()
        self.restart_all()

    # instances
    def start_instance(self, inst_id):
        instance = self.instances.get(inst_id)
        if instance is None:
            instance = self.instances[inst_id] = WidgetInstance(self, inst_id)
        instance.start()

    def stop_instance(self, inst_id):
        instance = self.instances.pop(inst_id, None)
        if instance:
            instance.stop()

    def restart_all(self):
        for inst in self.layout.instances:
            if inst['id'] in self.instances:
                self.instances[inst['id']].restart()
            else:
                self.start_instance(inst['id'])
        self.sync_services()

    def sync_services(self):
        wanted = set()
        for inst in self.layout.instances:
            t = self.types.get(inst['type'])
            if t and t.supported:
                wanted |= set(t.uses)
        self.hub.sync(wanted)

    # dragging
    def drag_started(self, instance):
        self.overlay.show(instance)

    def drag_moved(self, instance, pos):
        self.overlay.update(instance, pos)

    def drop(self, instance, pos):
        """The widget was let go at window position `pos`: snap it to the nearest free cell."""
        self.overlay.hide()
        inst = instance.inst
        if inst is None:
            return
        col, row = self.nearest_cell(instance, pos)
        spot = self.layout.move(inst['id'], col, row) or (inst['col'], inst['row'])
        instance.place(*spot)
        self.layout.save()

    # desktop
    def open_gallery(self, configure=None):
        command = [sys.executable, '-m', 'deskwidgets', 'gallery'] + (['--configure', configure] if configure else [])
        try:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=sys.platform != 'win32',
                             creationflags=0x08000000 if sys.platform == 'win32' else 0)
        except OSError as e:
            log(f'cannot open the gallery: {e}')

    def notify(self, title, body):
        if self.tray:
            self.tray.notify(title, body)
        else:
            from ..platform.integration import notify
            notify(title, body)

    def setup_desktop_integration(self):
        """The tray icon and the gallery hotkey where the backend can provide them (Windows)."""
        items = [(tr('Відкрити галерею', 'Open the gallery'), self.open_gallery),
                 (tr('Розставити по сітці', 'Arrange on the grid'), lambda: self.handle('arrange', {})),
                 None,
                 (tr('Вийти', 'Quit'), self.backend.quit)]
        self.tray = self.backend.create_tray('DeskWidgets', str(paths.ICON_PNG), items)
        hotkey = GALLERY_HOTKEY.get(sys.platform)
        if hotkey and self.settings['hotkey']:
            if not self.backend.register_hotkey(hotkey, self.open_gallery):
                log(f'the hotkey {hotkey} is taken by another program')

    # the control interface (ipc.py); every handler returns something JSON-able
    def handle(self, command, args):
        handler = getattr(self, f'cmd_{command}', None)
        if handler is None:
            raise ValueError(f'unknown command: {command}')
        return handler(**args)

    def cmd_ping(self):
        return 'pong'

    def cmd_status(self):
        return {'version': VERSION, 'pid': os.getpid(), 'widgets': len(self.instances),
                'running': sorted(i for i, w in self.instances.items() if w.running)}

    def cmd_list(self):
        return self.layout.instances

    def cmd_add(self, type):
        if type not in self.types:
            self.types = catalog.discover()
            self.layout.reload_types(self.types)
        t = self.types.get(type)
        if t is None:
            raise ValueError(tr(f'Немає такого віджета: {type}', f'No such widget: {type}'))
        if not t.supported:
            raise ValueError(tr(f'«{t.title}» не працює в цій системі', f'"{t.title}" does not work on this system'))
        inst = self.layout.add(type)
        if inst is None:
            raise ValueError(tr('На сітці немає місця', 'The grid is full'))
        self.layout.save()
        self.start_instance(inst['id'])
        self.sync_services()
        log(f"{inst['id']}: added at ({inst['col']}, {inst['row']})")
        return inst['id']

    def cmd_remove(self, id):
        if self.layout.remove(id) is None:
            raise KeyError(id)
        self.stop_instance(id)
        self.layout.save()
        self.sync_services()
        log(f'{id}: removed')

    def cmd_configure(self, id, patch):
        inst = self.layout.configure(id, patch)
        if inst is None:
            raise KeyError(id)
        self.layout.save()
        if id in self.instances:
            self.instances[id].restart()
            self.instances[id].place(inst['col'], inst['row'])
        else:
            self.start_instance(id)

    def cmd_move(self, id, col, row):
        instance = self.instances.get(id)
        if self.layout.get(id) is None:
            raise KeyError(id)
        spot = self.layout.move(id, int(col), int(row))
        if spot is None:
            raise ValueError(tr('Тут немає місця', 'No room there'))
        if instance:
            instance.place(*spot)
        self.layout.save()
        return list(spot)

    def cmd_arrange(self):
        """Packs every widget onto the grid again, outer columns first."""
        for inst in self.layout.arrange():
            instance = self.instances.get(inst['id'])
            if instance:
                instance.place(inst['col'], inst['row'])
        self.layout.save()
        log('widgets arranged')

    def cmd_reload(self):
        """Re-reads the widget folders (new or edited widgets) and restarts every widget."""
        self.types = catalog.discover()
        self.layout.reload_types(self.types)
        for inst_id in [i for i in self.instances if self.layout.get(i) is None or self.layout.get(i)['type'] not in self.types]:
            self.stop_instance(inst_id)
        self.restart_all()
        log('widgets reloaded')

    def cmd_settings(self):
        """Settings were changed in the gallery: apply them."""
        old_font = self.settings.get('font')
        self.load_settings()
        if self.settings.get('font') != old_font:
            self.restart_requested = True
            self.backend.quit()
            return
        self.rebuild_grid()
        self.layout.settings = self.settings
        self.layout.regrid(self.grid)
        self.layout.save()
        self.restart_all()
        log('settings applied')

    def cmd_gallery(self, configure=None):
        self.open_gallery(configure)

    def cmd_notify(self, title, body=''):
        self.notify(title, body)

    def cmd_quit(self):
        self.backend.call_later(0.05, self.backend.quit)


def run(backend=None):
    return Daemon(backend).run()
