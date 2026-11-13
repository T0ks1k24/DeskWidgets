"""What is on the desktop: the widget instances and where they sit on the grid of their monitor.

`Layout` is plain data plus rules (no windows, no processes), so everything here is easy to test.
layout.json: {"version": 3, "instances": [{id, type, size, style, accent, options, monitor, col, row}]}

Every monitor that shows widgets has its own grid (`grids`: monitor id -> Grid, the first one is where new
widgets go). `col`/`row` count cells on the instance's monitor. A widget whose monitor is gone (unplugged, or
switched off in the settings) moves to the first monitor and stays there.
"""
import json
import re
import shutil

from . import paths
from .catalog import ACCENT_KEYS, SIZES, STYLE_KEYS
from .grid import Grid
from .util import log, write_atomic

VERSION = 3

# first-run layout: (type, size, col, row); negative columns count from the right edge
DEFAULT_LAYOUT = [
    ('clock', 'small', 0, 0), ('calendar', 'small', 1, 0), ('weather', 'medium', 0, 1), ('system', 'medium', 0, 2),
    ('events', 'medium', -2, 0), ('media', 'medium', -2, 1), ('anime_quote', 'medium', -2, 2),
]

_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')


class Layout:
    def __init__(self, grids, types, settings):
        self.grids = {'screen': grids} if isinstance(grids, Grid) else dict(grids)
        self.types = types
        self.settings = settings
        self.instances = []

    @property
    def home(self):
        """The monitor new widgets go to, and the one a widget falls back to."""
        return next(iter(self.grids))

    @property
    def grid(self):
        return self.grids[self.home]

    def grid_of(self, inst):
        return self.grids[inst['monitor']]

    # loading and saving
    def load(self):
        data = self._read(paths.LAYOUT)
        if data is None and paths.OLD_LAYOUT.exists():
            data = self._import_v1(self._read(paths.OLD_LAYOUT))
        raw = data.get('instances') if isinstance(data, dict) else None
        if not isinstance(raw, list):
            raw = self._defaults()
        self.instances = []
        taken = {m: set() for m in self.grids}
        for item in raw:
            inst = self._normalise(item)
            if inst is None:
                continue
            col, row = inst.get('col'), inst.get('row')
            inst['col'], inst['row'] = (col, row) if isinstance(col, int) and isinstance(row, int) else (0, 0)
            if not self._fit(inst, taken, keep=isinstance(col, int) and isinstance(row, int)):
                log(f"{inst['id']}: no room left on the grid, skipped")
                continue
            self.instances.append(inst)

    @staticmethod
    def _read(path):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def save(self):
        write_atomic(paths.LAYOUT, json.dumps({'version': VERSION, 'instances': self.instances}, indent=2,
                                              ensure_ascii=False) + '\n')

    def _import_v1(self, data):
        """Imports DeskWidgets 1.x widgets (~/.config/conky/mac), keeping their Ukrainian UI."""
        log('importing widgets from ~/.config/conky/mac')
        if paths.OLD_KNOWN_DEVICES.exists() and not (paths.CONFIG / 'network_known.txt').exists():
            paths.CONFIG.mkdir(parents=True, exist_ok=True)
            shutil.copy(paths.OLD_KNOWN_DEVICES, paths.CONFIG / 'network_known.txt')
        for inst in data.get('instances', []) if isinstance(data, dict) else []:
            if not isinstance(inst, dict):
                continue
            options = inst.setdefault('options', {})
            if inst.get('type') == 'network':   # 1.x always watched the LAN
                options.setdefault('lan', True)
            if inst.get('type') == 'weather':   # 1.x had Lutsk built in
                options.setdefault('city', 'Lutsk')
                options.setdefault('title', 'Луцьк')
        return data

    def _defaults(self):
        return [{'type': type_id, 'size': size, 'row': row, 'col': col if col >= 0 else self.grid.cols + col}
                for type_id, size, col, row in DEFAULT_LAYOUT if type_id in self.types]

    def _normalise(self, item):
        if not isinstance(item, dict) or item.get('type') not in self.types:
            if isinstance(item, dict):
                log(f"{item.get('id')}: unknown widget type {item.get('type')!r}, skipped")
            return None
        t = self.types[item['type']]
        wanted = item.get('id')
        inst_id = wanted if isinstance(wanted, str) and _ID.match(wanted) and wanted not in self.ids() \
            else self.new_id(t.id)
        options = {}
        for key, value in (item.get('options') if isinstance(item.get('options'), dict) else {}).items():
            option = t.option(key)
            if option is not None:
                try:
                    options[key] = option.coerce(value)
                except ValueError as e:
                    log(f'{inst_id}: {e}, using the default')
        monitor = item.get('monitor')
        return {
            'id': inst_id, 'type': t.id,
            'size': item.get('size') if item.get('size') in t.sizes else t.size,
            'style': item.get('style') if item.get('style') in STYLE_KEYS else self.default_style(t),
            'accent': item.get('accent') if item.get('accent') in ACCENT_KEYS else self.default_accent(t),
            'options': options,
            'monitor': monitor if isinstance(monitor, str) else '',
            'col': item.get('col'), 'row': item.get('row'),
        }

    def default_style(self, t):
        style = self.settings.get('default_style', 'auto')
        return style if style in STYLE_KEYS else t.style

    def default_accent(self, t):
        accent = self.settings.get('default_accent', 'auto')
        return accent if accent in ACCENT_KEYS and accent != 'auto' else t.accent

    def _fit(self, inst, taken, keep=True):
        """Puts `inst` on a free spot of its monitor, as close as it can to its cell (`keep`: it has a cell
        worth keeping); another monitor when its own is full or gone. `taken` maps monitor -> cells in use and is
        updated. Returns False when there is no room anywhere."""
        monitor = inst['monitor'] if inst['monitor'] in self.grids else self.home
        for candidate in [monitor] + [m for m in self.grids if m != monitor]:
            grid, used = self.grids[candidate], taken.setdefault(candidate, set())
            col, row = inst['col'], inst['row']
            if keep and candidate == monitor and grid.fits(col, row, inst['size']) and \
                    not (Grid.cells(col, row, inst['size']) & used):
                spot = (col, row)
            else:
                spot = grid.free_near(col, row, inst['size'], used)
            if spot is not None:
                inst['monitor'], (inst['col'], inst['row']) = candidate, spot
                used |= Grid.cells(spot[0], spot[1], inst['size'])
                return True
        return False

    # queries
    def ids(self):
        return {i['id'] for i in self.instances}

    def get(self, inst_id):
        return next((i for i in self.instances if i['id'] == inst_id), None)

    def taken(self, monitor=None, exclude=None):
        """The cells in use on a monitor (the first one by default)."""
        monitor = monitor if monitor in self.grids else self.home
        cells = set()
        for i in self.instances:
            if i['id'] != exclude and i['monitor'] == monitor:
                cells |= Grid.cells(i['col'], i['row'], i['size'])
        return cells

    def new_id(self, type_id):
        used = self.ids()
        if type_id not in used:
            return type_id
        n = 2
        while f'{type_id}-{n}' in used:
            n += 1
        return f'{type_id}-{n}'

    # changes; each returns what happened so the caller can react
    def add(self, type_id, monitor=None):
        """Puts a new widget on the first free spot (of `monitor`, else of the first monitor that has one).
        Returns the instance, or None when it does not fit."""
        t = self.types.get(type_id)
        if t is None:
            log(f'{type_id}: no such widget')
            return None
        monitors = [monitor] if monitor in self.grids else list(self.grids)
        sizes = [t.size] + [alt for alt in ('small', 'medium') if alt in t.sizes and alt != t.size]
        for size in sizes:
            for candidate in monitors:
                spot = self.grids[candidate].first_free(size, self.taken(candidate))
                if spot is not None:
                    inst = {'id': self.new_id(type_id), 'type': type_id, 'size': size, 'style': self.default_style(t),
                            'accent': self.default_accent(t), 'options': {}, 'monitor': candidate,
                            'col': spot[0], 'row': spot[1]}
                    self.instances.append(inst)
                    return inst
        log(f'{type_id}: the grid is full')
        return None

    def remove(self, inst_id):
        inst = self.get(inst_id)
        if inst is not None:
            self.instances.remove(inst)
        return inst

    def configure(self, inst_id, patch):
        """Applies size/style/accent/options/monitor from the gallery or the CLI. Invalid values are ignored.
        Returns the instance (None if there is no such one)."""
        inst = self.get(inst_id)
        if inst is None or not isinstance(patch, dict):
            return None
        t = self.types[inst['type']]
        old_size = inst['size']
        if patch.get('size') in t.sizes:
            inst['size'] = patch['size']
        if patch.get('style') in STYLE_KEYS:
            inst['style'] = patch['style']
        if patch.get('accent') in ACCENT_KEYS:
            inst['accent'] = patch['accent']
        if isinstance(patch.get('options'), dict):
            for key, value in patch['options'].items():
                option = t.option(key)
                if option is None:
                    continue
                try:
                    inst['options'][key] = option.coerce(value)
                except ValueError as e:
                    log(f'{inst_id}: {e}')
        if patch.get('monitor') in self.grids and patch['monitor'] != inst['monitor']:
            spot = self.grids[patch['monitor']].first_free(inst['size'], self.taken(patch['monitor']))
            if spot is None:
                log(f"{inst_id}: no room on {patch['monitor']}, staying on {inst['monitor']}")
            else:
                inst['monitor'], inst['col'], inst['row'] = patch['monitor'], spot[0], spot[1]
        if inst['size'] != old_size:
            grid, taken = self.grid_of(inst), self.taken(inst['monitor'], exclude=inst_id)
            here = grid.fits(inst['col'], inst['row'], inst['size']) and \
                not (Grid.cells(inst['col'], inst['row'], inst['size']) & taken)
            spot = (inst['col'], inst['row']) if here else grid.free_near(inst['col'], inst['row'], inst['size'], taken)
            if spot is None:
                log(f"{inst_id}: no room for {inst['size']}, size unchanged")
                inst['size'] = old_size
            else:
                inst['col'], inst['row'] = spot
        return inst

    def move(self, inst_id, col, row, monitor=None):
        """Snaps a widget to the free spot nearest to (col, row) of `monitor` (its own by default).
        Returns the cell, or None; the instance's monitor is updated."""
        inst = self.get(inst_id)
        if inst is None:
            return None
        monitor = monitor if monitor in self.grids else inst['monitor']
        spot = self.grids[monitor].free_near(col, row, inst['size'], self.taken(monitor, exclude=inst_id))
        if spot is None:
            return None
        inst['monitor'], inst['col'], inst['row'] = monitor, spot[0], spot[1]
        return spot

    def arrange(self):
        """Packs every widget onto the grid of its monitor again, outer columns first. Returns the moved instances."""
        moved = []
        for monitor, grid in self.grids.items():
            taken, half = set(), grid.cols // 2
            for inst in sorted((i for i in self.instances if i['monitor'] == monitor),
                               key=lambda i: (i['col'] > half, i['row'], i['col'])):
                spot = grid.first_free(inst['size'], taken) or (inst['col'], inst['row'])
                if spot != (inst['col'], inst['row']):
                    moved.append(inst)
                inst['col'], inst['row'] = spot
                taken |= Grid.cells(spot[0], spot[1], inst['size'])
        return moved

    def regrid(self, grids):
        """The screens or the grid settings changed. A widget stays on its monitor and where it was on the
        screen (a different widget size means a different cell size: a widget at the right or bottom edge stays
        at that edge, the others keep their distance from the top-left corner); if that cell is taken it goes to
        the nearest free one. A widget whose monitor is gone moves to the first monitor."""
        old = self.grids
        self.grids = {'screen': grids} if isinstance(grids, Grid) else dict(grids)
        taken = {m: set() for m in self.grids}
        for inst in sorted(self.instances, key=lambda i: (i['row'], i['col'])):
            before = old.get(inst['monitor'])
            if before is not None and inst['monitor'] in self.grids:
                inst['col'], inst['row'] = self._rescale(before, self.grids[inst['monitor']], inst)
            if not self._fit(inst, taken):
                self._squeeze(inst, taken)

    @staticmethod
    def _rescale(before, after, inst):
        """The cell of a widget on `after` that is where it was on `before` (the same cell if the cells did not change size)."""
        col, row = inst['col'], inst['row']
        if before.pitch == after.pitch:
            return col, row
        fw, fh = SIZES[inst['size']]
        col = after.cols - fw if col + fw >= before.cols else round(col * before.pitch / after.pitch)
        row = after.rows - fh if row + fh >= before.rows else round(row * before.pitch / after.pitch)
        return max(col, 0), max(row, 0)

    def _squeeze(self, inst, taken):
        """No free spot anywhere: the widget stays in the layout at the corner of the first monitor, on top of
        others, rather than being deleted from layout.json by a screen that was only too small for a moment."""
        log(f"{inst['id']}: no room left on the grid")
        inst['monitor'], inst['col'], inst['row'] = self.home, 0, 0

    def reload_types(self, types):
        """New or edited widget folders. Instances of a type that is gone stay in layout.json
        (a typo in widget.json must not delete them), the service just does not run them."""
        self.types = types
