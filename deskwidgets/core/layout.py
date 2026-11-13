"""What is on the desktop: the widget instances and where they sit on the grid.

`Layout` is plain data plus rules (no windows, no processes), so everything here is easy to test.
layout.json: {"version": 3, "instances": [{id, type, size, style, accent, options, col, row}]}
"""
import json
import re
import shutil

from . import paths
from .catalog import ACCENT_KEYS, STYLE_KEYS
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
    def __init__(self, grid, types, settings):
        self.grid = grid
        self.types = types
        self.settings = settings
        self.instances = []

    # loading and saving
    def load(self):
        data = self._read(paths.LAYOUT)
        if data is None and paths.OLD_LAYOUT.exists():
            data = self._import_v1(self._read(paths.OLD_LAYOUT))
        raw = data.get('instances') if isinstance(data, dict) else None
        if not isinstance(raw, list):
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
        return {
            'id': inst_id, 'type': t.id,
            'size': item.get('size') if item.get('size') in t.sizes else t.size,
            'style': item.get('style') if item.get('style') in STYLE_KEYS else self.default_style(t),
            'accent': item.get('accent') if item.get('accent') in ACCENT_KEYS else self.default_accent(t),
            'options': options,
            'col': item.get('col'), 'row': item.get('row'),
        }

    def default_style(self, t):
        style = self.settings.get('default_style', 'auto')
        return style if style in STYLE_KEYS else t.style

    def default_accent(self, t):
        accent = self.settings.get('default_accent', 'auto')
        return accent if accent in ACCENT_KEYS and accent != 'auto' else t.accent

    # queries
    def ids(self):
        return {i['id'] for i in self.instances}

    def get(self, inst_id):
        return next((i for i in self.instances if i['id'] == inst_id), None)

    def taken(self, exclude=None):
        cells = set()
        for i in self.instances:
            if i['id'] != exclude:
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
    def add(self, type_id):
        """Puts a new widget on the first free spot. Returns the instance, or None when it does not fit."""
        t = self.types.get(type_id)
        if t is None:
            log(f'{type_id}: no such widget')
            return None
        size, taken = t.size, self.taken()
        spot = self.grid.first_free(size, taken)
        if spot is None:
            for alt in ('small', 'medium'):
                if alt in t.sizes and (spot := self.grid.first_free(alt, taken)):
                    size = alt
                    break
        if spot is None:
            log(f'{type_id}: the grid is full')
            return None
        inst = {'id': self.new_id(type_id), 'type': type_id, 'size': size, 'style': self.default_style(t),
                'accent': self.default_accent(t), 'options': {}, 'col': spot[0], 'row': spot[1]}
        self.instances.append(inst)
        return inst

    def remove(self, inst_id):
        inst = self.get(inst_id)
        if inst is not None:
            self.instances.remove(inst)
        return inst

    def configure(self, inst_id, patch):
        """Applies size/style/accent/options from the gallery or the CLI. Invalid values are ignored.
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
        if inst['size'] != old_size:
            taken = self.taken(exclude=inst_id)
            here = self.grid.fits(inst['col'], inst['row'], inst['size']) and \
                not (Grid.cells(inst['col'], inst['row'], inst['size']) & taken)
            spot = (inst['col'], inst['row']) if here else \
                self.grid.free_near(inst['col'], inst['row'], inst['size'], taken)
            if spot is None:
                log(f"{inst_id}: no room for {inst['size']}, size unchanged")
                inst['size'] = old_size
            else:
                inst['col'], inst['row'] = spot
        return inst

    def move(self, inst_id, col, row):
        """Snaps a widget to the free spot nearest to (col, row). Returns the cell, or None."""
        inst = self.get(inst_id)
        if inst is None:
            return None
        spot = self.grid.free_near(col, row, inst['size'], self.taken(exclude=inst_id))
        if spot is None:
            return None
        inst['col'], inst['row'] = spot
        return spot

    def arrange(self):
        """Packs every widget onto the grid again, outer columns first. Returns the moved instances."""
        taken, moved = set(), []
        half = self.grid.cols // 2
        for inst in sorted(self.instances, key=lambda i: (i['col'] > half, i['row'], i['col'])):
            spot = self.grid.first_free(inst['size'], taken) or (inst['col'], inst['row'])
            if spot != (inst['col'], inst['row']):
                moved.append(inst)
            inst['col'], inst['row'] = spot
            taken |= Grid.cells(spot[0], spot[1], inst['size'])
        return moved

    def regrid(self, grid):
        """The screen or the grid settings changed: keep cells where possible, else the nearest free one."""
        self.grid = grid
        taken = set()
        for inst in self.instances:
            if grid.fits(inst['col'], inst['row'], inst['size']) and \
                    not (Grid.cells(inst['col'], inst['row'], inst['size']) & taken):
                spot = (inst['col'], inst['row'])
            else:
                spot = grid.free_near(inst['col'], inst['row'], inst['size'], taken) or (0, 0)
            inst['col'], inst['row'] = spot
            taken |= Grid.cells(spot[0], spot[1], inst['size'])

    def reload_types(self, types):
        """New or edited widget folders. Instances of a type that is gone stay in layout.json
        (a typo in widget.json must not delete them), the service just does not run them."""
        self.types = types


