"""The DeskWidgets service: one conky window per widget instance, kept on a grid.

* starts a conky for every instance in layout.json and restarts it if it crashes
* a widget dragged with Super held snaps to the nearest free grid cell when you let go
  (instantly with the GNOME Shell helper, which also shows the grid while dragging)
* feeds the "media" and "events" widgets with live data (providers.py)
* is controlled through GApplication actions (gallery, CLI):
  add(s type), remove(s id), configure(s id, s json), arrange, reload, settings, quit
"""
import json
import os
import shutil
import signal
import time

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib

from . import catalog, engine, paths, settings as settings_mod
from .catalog import ACCENT_KEYS, SIZES, STYLE_KEYS
from .grid import Grid
from .providers import Events, HidBatteries, Media
from .util import log, write_atomic

POLL_MS = 500
SNAP_AFTER = 2   # polls without movement before a dropped widget snaps to the grid

# first-run layout: (type, size, col, row); negative columns count from the right edge
DEFAULT_LAYOUT = [
    ('clock', 'small', 0, 0), ('calendar', 'small', 1, 0), ('weather', 'medium', 0, 1), ('system', 'medium', 0, 2),
    ('events', 'medium', -2, 0), ('media', 'medium', -2, 1), ('anime_quote', 'medium', -2, 2),
]
OLD_LAYOUT = os.path.join(paths.HOME, '.config', 'conky', 'mac', 'layout.json')   # DeskWidgets 1.x


def kill_stale_conkys():
    """Stops widget conkys left over from an earlier run (including 1.x ones)."""
    for pid in filter(str.isdigit, os.listdir('/proc')):
        try:
            if os.stat(f'/proc/{pid}').st_uid != os.getuid():
                continue
            with open(f'/proc/{pid}/cmdline', 'rb') as f:
                argv = f.read().split(b'\0')
        except OSError:
            continue
        if os.path.basename(argv[0]) == b'conky' and \
                any(a.endswith((b'/widget.conf', b'mac/widgets.conf')) for a in argv):
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass


class Runner:
    """One conky process drawing one widget instance."""

    def __init__(self, daemon, inst_id):
        self.d, self.id = daemon, inst_id
        self.proc = None
        self.stopping = False
        self.crashes = []
        self.window = None
        self.requested = (0, 0)
        self.offset = None     # where the WM put the window relative to where it was asked to
        self.last = None       # position seen by the previous poll
        self.still = 0         # polls the window has not moved
        self.cooldown = 0      # polls to wait after we moved the window ourselves
        self.settling = False  # just snapped it: accept the WM's final spot

    @property
    def running(self):
        return self.proc is not None

    @property
    def inst(self):
        return self.d.instance(self.id)

    def start(self):
        inst = self.inst
        t = self.d.types.get(inst['type']) if inst else None
        if t is None:
            return
        grid = self.d.grid
        x, y = grid.window_xy(inst['col'], inst['row'])
        self.requested = (x, y)
        self.window, self.offset, self.last, self.still = None, None, None, 0
        opts = engine.write_options(inst, t, lang=self.d.lang)
        env = engine.widget_env(inst, t, lang=self.d.lang, scale=grid.scale, size_px=grid.window_px(inst['size']),
                                pos=(x, y), opts_path=opts)
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_SILENCE)
        for key, value in env.items():
            launcher.setenv(key, str(value), True)
        try:
            self.proc = launcher.spawnv(['conky', '-c', paths.CONF])
        except GLib.Error as e:
            log(f'{self.id}: cannot start conky: {e.message}')
            return
        self.stopping = False
        self.proc.wait_async(None, self._exited)

    def stop(self):
        self.stopping = True
        if self.proc:
            self.proc.send_signal(signal.SIGTERM)
        self.proc = None
        self.window = None

    def start_later(self, delay_ms=0):
        """Starts after `delay_ms` unless the widget got started or removed meanwhile."""
        def go():
            if not self.running and self.inst is not None and not self.d.quitting:
                self.start()
            return False
        GLib.timeout_add(delay_ms, go)

    def restart(self, delay_ms=0):
        """New conky only once the old one is gone: conky aborts on BadWindow when
        windows disappear while it is starting up."""
        old = self.proc
        self.stop()
        if old is None:
            self.start_later(delay_ms)
        else:
            old.wait_async(None, lambda *a: self.start_later(delay_ms + 300))

    def _exited(self, proc, result):
        try:
            proc.wait_finish(result)
        except GLib.Error:
            pass
        if proc is not self.proc or self.stopping:
            return
        self.proc = None
        now = time.monotonic()
        self.crashes = [t for t in self.crashes if now - t < 60] + [now]
        if len(self.crashes) > 4:
            log(f'{self.id}: conky keeps exiting, giving up (see the errors above)')
            return
        status = proc.get_exit_status() if proc.get_if_exited() else f'signal {proc.get_term_sig()}'
        log(f'{self.id}: conky exited ({status}), restarting')
        self.start_later(2000)


class Daemon(Gio.Application):
    def __init__(self):
        super().__init__(application_id=paths.APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.started = False
        self.quitting = False
        self.instances = []
        self.runners = {}
        self.types = {}
        self.media = None
        self.events = None
        self.hid = None

    def do_startup(self):
        Gio.Application.do_startup(self)
        self.hold()
        for name, ptype, handler in (('add', 's', self._act_add), ('remove', 's', self._act_remove),
                                     ('configure', '(ss)', self._act_configure), ('arrange', None, self._act_arrange),
                                     ('reload', None, self._act_reload), ('settings', None, self._act_settings),
                                     ('quit', None, lambda *a: self.quit())):
            action = Gio.SimpleAction.new(name, GLib.VariantType(ptype) if ptype else None)
            action.connect('activate', handler)
            self.add_action(action)

    def do_activate(self):
        if self.started:
            return
        self.started = True
        from .x11 import X11
        paths.ensure_dirs()
        kill_stale_conkys()
        self.x = X11()
        self.screen = self.x.screen_size()
        self.load_settings()
        self.types = catalog.discover()
        self.load_layout()
        self.save_layout()
        for i, inst in enumerate(self.instances):   # staggered: conkys starting together race on X windows
            self.runners[inst['id']] = Runner(self, inst['id'])
            self.runners[inst['id']].start_later(i * 150)
        self.sync_providers()
        GLib.timeout_add(POLL_MS, self.poll)
        log(f'starting {len(self.instances)} widgets on a {self.grid.cols}×{self.grid.rows} grid')

    def do_shutdown(self):
        self.quitting = True
        for r in self.runners.values():
            r.stop()
        for provider in (self.media, self.events, self.hid):
            if provider:
                provider.stop()
        for f in (paths.GRID_JSON, paths.DRAGGING):
            if os.path.exists(f):
                os.unlink(f)
        Gio.Application.do_shutdown(self)

    # settings
    def load_settings(self):
        self.settings = settings_mod.load()
        self.lang = settings_mod.language(self.settings)
        self.grid = Grid(*self.screen, self.settings)
        engine.write_fontconf(self.settings)

    # layout.json: {"version": 3, "instances": [{id, type, size, style, accent, options, col, row}]}
    def load_layout(self):
        data = None
        for path in (paths.LAYOUT, OLD_LAYOUT):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if path == OLD_LAYOUT:
                    data = self._migrate_v2(data)
                break
            except (OSError, ValueError):
                continue
        raw = data.get('instances') if isinstance(data, dict) and isinstance(data.get('instances'), list) else None
        if raw is None:
            raw = self._defaults()
        self.instances = []
        taken = set()
        for item in raw:
            inst = self._normalise(item)
            if inst is None:
                continue
            col, row = inst.get('col'), inst.get('row')
            if not (isinstance(col, int) and isinstance(row, int) and self.grid.fits(col, row, inst['size'])
                    and not (Grid.cells(col, row, inst['size']) & taken)):
                spot = self.grid.free_near(col if isinstance(col, int) else 0, row if isinstance(row, int) else 0,
                                           inst['size'], taken)
                if spot is None:
                    log(f"{inst['id']}: no room left on the grid, skipped")
                    continue
                inst['col'], inst['row'] = spot
            taken |= Grid.cells(inst['col'], inst['row'], inst['size'])
            self.instances.append(inst)

    def _migrate_v2(self, data):
        """Imports DeskWidgets 1.x widgets from ~/.config/conky/mac, keeping its Ukrainian UI."""
        log('importing widgets from ~/.config/conky/mac')
        if self._stored_settings().get('language', 'auto') == 'auto':
            settings_mod.save({**settings_mod.load(), 'language': 'uk'})
            self.load_settings()
        old_known = os.path.join(paths.HOME, '.config', 'conky', 'network_known.txt')
        new_known = os.path.join(paths.CONFIG, 'network_known.txt')
        if os.path.exists(old_known) and not os.path.exists(new_known):
            shutil.copy(old_known, new_known)
        for inst in data.get('instances', []) if isinstance(data, dict) else []:
            opts = inst.setdefault('options', {})
            if inst.get('type') == 'network':   # 1.x always watched the LAN
                opts.setdefault('lan', True)
            if inst.get('type') == 'weather':   # 1.x had Lutsk built in
                opts.setdefault('city', 'Lutsk')
                opts.setdefault('title', 'Луцьк')
        return data

    def _stored_settings(self):
        try:
            with open(paths.SETTINGS, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _normalise(self, item):
        if not isinstance(item, dict) or item.get('type') not in self.types:
            if isinstance(item, dict):
                log(f"{item.get('id')}: unknown widget type {item.get('type')!r}, skipped")
            return None
        t = self.types[item['type']]
        used = {i['id'] for i in self.instances}
        inst_id = item.get('id') if isinstance(item.get('id'), str) and item['id'] not in used else self._new_id(t.id)
        return {
            'id': inst_id, 'type': t.id,
            'size': item.get('size') if item.get('size') in t.sizes else t.size,
            'style': item.get('style') if item.get('style') in STYLE_KEYS else self._default_style(t),
            'accent': item.get('accent') if item.get('accent') in ACCENT_KEYS else self._default_accent(t),
            'options': item.get('options') if isinstance(item.get('options'), dict) else {},
            'col': item.get('col'), 'row': item.get('row'),
        }

    def _default_style(self, t):
        s = self.settings.get('default_style', 'auto')
        return s if s in STYLE_KEYS else t.style

    def _default_accent(self, t):
        a = self.settings.get('default_accent', 'auto')
        return a if a in ACCENT_KEYS and a != 'auto' else t.accent

    def _defaults(self):
        out = []
        for type_id, size, col, row in DEFAULT_LAYOUT:
            if type_id in self.types:
                out.append({'type': type_id, 'size': size, 'row': row,
                            'col': col if col >= 0 else self.grid.cols + col})
        return out

    def save_layout(self):
        write_atomic(paths.LAYOUT, json.dumps({'version': 3, 'instances': self.instances}, indent=2,
                                              ensure_ascii=False) + '\n')
        write_atomic(paths.GRID_JSON, json.dumps(self.grid.to_json(self.instances)))

    def instance(self, inst_id):
        return next((i for i in self.instances if i['id'] == inst_id), None)

    def taken(self, exclude=None):
        cells = set()
        for i in self.instances:
            if i['id'] != exclude:
                cells |= Grid.cells(i['col'], i['row'], i['size'])
        return cells

    def _new_id(self, type_id):
        used = {i['id'] for i in self.instances}
        if type_id not in used:
            return type_id
        n = 2
        while f'{type_id}-{n}' in used:
            n += 1
        return f'{type_id}-{n}'

    # actions
    def _act_add(self, _action, param):
        type_id = param.unpack()
        t = self.types.get(type_id) or catalog.discover().get(type_id)
        if t is None:
            log(f'{type_id}: no such widget')
            return
        self.types[t.id] = t
        size, taken = t.size, self.taken()
        spot = self.grid.first_free(size, taken)
        if spot is None:
            for alt in ('small', 'medium'):
                if alt in t.sizes and (spot := self.grid.first_free(alt, taken)):
                    size = alt
                    break
        if spot is None:
            log(f'{type_id}: the grid is full')
            return
        inst = {'id': self._new_id(type_id), 'type': type_id, 'size': size, 'style': self._default_style(t),
                'accent': self._default_accent(t), 'options': {}, 'col': spot[0], 'row': spot[1]}
        self.instances.append(inst)
        self.runners[inst['id']] = Runner(self, inst['id'])
        self.runners[inst['id']].start()
        self.save_layout()
        self.sync_providers()
        log(f"{inst['id']}: added at {spot}")

    def _act_remove(self, _action, param):
        inst = self.instance(param.unpack())
        if inst is None:
            return
        runner = self.runners.pop(inst['id'], None)
        if runner:
            runner.stop()
        self.instances.remove(inst)
        try:
            os.unlink(os.path.join(paths.RUN, 'opts', inst['id']))
        except OSError:
            pass
        self.save_layout()
        self.sync_providers()
        log(f"{inst['id']}: removed")

    def _act_configure(self, _action, param):
        inst_id, patch_json = param.unpack()
        inst = self.instance(inst_id)
        try:
            patch = json.loads(patch_json)
        except ValueError:
            return
        if inst is None or not isinstance(patch, dict):
            return
        t = self.types[inst['type']]
        old_size = inst['size']
        if patch.get('size') in t.sizes:
            inst['size'] = patch['size']
        if patch.get('style') in STYLE_KEYS:
            inst['style'] = patch['style']
        if patch.get('accent') in ACCENT_KEYS:
            inst['accent'] = patch['accent']
        if isinstance(patch.get('options'), dict):
            known = {o.key for o in t.options}
            for key, value in patch['options'].items():
                if key in known and isinstance(value, (str, bool, int, float)):
                    inst['options'][key] = value
        if inst['size'] != old_size:
            taken = self.taken(exclude=inst['id'])
            here_ok = self.grid.fits(inst['col'], inst['row'], inst['size']) and \
                not (Grid.cells(inst['col'], inst['row'], inst['size']) & taken)
            spot = (inst['col'], inst['row']) if here_ok else \
                self.grid.free_near(inst['col'], inst['row'], inst['size'], taken)
            if spot is None:
                log(f"{inst['id']}: no room for {inst['size']}, size unchanged")
                inst['size'] = old_size
            else:
                inst['col'], inst['row'] = spot
        self.save_layout()
        self.runners[inst['id']].restart()

    def _act_arrange(self, *_):
        """Packs every widget onto the grid again, outer columns first."""
        taken = set()
        for inst in sorted(self.instances, key=lambda i: (i['col'] > self.grid.cols // 2, i['row'], i['col'])):
            spot = self.grid.first_free(inst['size'], taken) or (inst['col'], inst['row'])
            inst['col'], inst['row'] = spot
            taken |= Grid.cells(spot[0], spot[1], inst['size'])
            r = self.runners.get(inst['id'])
            if r and r.running:
                self.move(r, inst)
        self.save_layout()
        log('widgets arranged')

    def _act_reload(self, *_):
        """Re-reads the widget folders (new or edited widgets) and restarts every widget."""
        self.types = catalog.discover()
        for i, inst in enumerate(self.instances):
            self.runners.setdefault(inst['id'], Runner(self, inst['id'])).restart(i * 150)
        log('widgets reloaded')

    def _act_settings(self, *_):
        """Settings were changed in the gallery: re-place and restart all widgets."""
        old = (self.grid.cell, self.grid.gap, self.grid.x0, self.grid.y0, self.grid.cols, self.grid.rows)
        self.load_settings()
        if old != (self.grid.cell, self.grid.gap, self.grid.x0, self.grid.y0, self.grid.cols, self.grid.rows):
            taken = set()
            for inst in self.instances:   # keep cells where possible, otherwise the nearest free one
                if self.grid.fits(inst['col'], inst['row'], inst['size']) and \
                        not (Grid.cells(inst['col'], inst['row'], inst['size']) & taken):
                    spot = (inst['col'], inst['row'])
                else:
                    spot = self.grid.free_near(inst['col'], inst['row'], inst['size'], taken) or (0, 0)
                inst['col'], inst['row'] = spot
                taken |= Grid.cells(spot[0], spot[1], inst['size'])
        self.save_layout()
        self._act_reload()
        log('settings applied')

    def sync_providers(self):
        uses = set()
        for inst in self.instances:
            t = self.types.get(inst['type'])
            if t:
                uses |= set(t.uses)
        self.media = self._toggle(self.media, 'media' in uses, Media)
        self.events = self._toggle(self.events, 'events' in uses, Events)
        self.hid = self._toggle(self.hid, 'hid-batteries' in uses, HidBatteries)

    @staticmethod
    def _toggle(provider, wanted, cls):
        if wanted and provider is None:
            try:
                return cls()
            except Exception as e:   # a broken provider must not take the widgets down
                log(f'{cls.__name__.lower()} provider failed: {e}')
                return None
        if not wanted and provider is not None:
            provider.stop()
            return None
        return provider

    # grid snapping
    def move(self, r, inst):
        x, y = self.grid.window_xy(inst['col'], inst['row'])
        if r.window is not None and r.offset is not None:
            self.x.move(r.window, x + r.offset[0], y + r.offset[1])
            r.cooldown, r.last, r.still, r.settling = 2, None, 0, True
        else:
            r.restart()

    @staticmethod
    def _dragging():
        """The GNOME Shell helper marks a drag in progress (it snaps on release itself)."""
        try:
            return time.time() - os.path.getmtime(paths.DRAGGING) < 60
        except OSError:
            return False

    def poll(self):
        """Watches widget windows. One that was dragged and let go snaps to the nearest free cell."""
        windows = None
        changed = False
        dragging = self._dragging()
        for inst in self.instances:
            r = self.runners.get(inst['id'])
            if not r or not r.running:
                continue
            if r.window is None:
                if windows is None:
                    windows = self.x.widget_windows()
                r.window = windows.get(inst['id'])
                if r.window is None:
                    continue
            pos = self.x.position(r.window)
            if pos is None:
                r.window = None
                continue
            if r.offset is None:
                r.offset = (pos[0] - r.requested[0], pos[1] - r.requested[1])
                r.last, r.settling = pos, False
                continue
            if r.cooldown:
                r.cooldown -= 1
                r.last = pos
                continue
            gx, gy = self.grid.window_xy(inst['col'], inst['row'])
            if pos == (gx + r.offset[0], gy + r.offset[1]):
                r.last, r.still, r.settling = pos, 0, False
                continue
            if pos != r.last or dragging:   # still being dragged
                r.last, r.still = pos, 0
                continue
            r.still += 1
            if r.still < SNAP_AFTER:
                continue
            col, row = self.grid.nearest_cell(pos[0] - r.offset[0], pos[1] - r.offset[1], inst['size'])
            spot = self.grid.free_near(col, row, inst['size'], self.taken(exclude=inst['id'])) \
                or (inst['col'], inst['row'])
            if spot == (inst['col'], inst['row']) and r.settling:
                # the WM kept it a few px off the cell (screen constraints): accept that spot
                r.offset = (pos[0] - gx, pos[1] - gy)
                r.settling = False
                continue
            inst['col'], inst['row'] = spot
            self.move(r, inst)
            changed = True
        if changed:
            self.save_layout()
        return True


def run():
    app = Daemon()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (app.quit(), False)[1])
    return app.run([app.get_application_id()])
