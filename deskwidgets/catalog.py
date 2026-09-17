"""Widget types are folders with a widget.json manifest and a widget.lua drawing file.

Built-in widgets live in <data>/widgets, your own in ~/.config/deskwidgets/widgets
(a folder there with the same id overrides the built-in one). See docs/creating-widgets.md.
"""
import json
import os
from dataclasses import dataclass, field

from . import paths
from .i18n import loc, tr
from .util import log

SIZES = {'small': (1, 1), 'medium': (2, 1), 'large': (2, 2)}   # grid cells
OPTION_TYPES = {'bool', 'text', 'longtext', 'date', 'choice', 'multi', 'file', 'number'}
CATEGORIES = ['time', 'info', 'system', 'anime', 'personal', 'other']


def size_name(size):
    return {'small': tr('Маленький', 'Small'), 'medium': tr('Середній', 'Medium'),
            'large': tr('Великий', 'Large')}.get(size, size)


def category_name(cat):
    return {'time': tr('Час і дата', 'Time & date'), 'info': tr('Інформація', 'Information'),
            'system': tr('Система', 'System'), 'anime': tr('Аніме', 'Anime'),
            'personal': tr('Особисте', 'Personal'), 'other': tr('Інше', 'Other')}.get(cat, cat)


def styles():
    return [
        ('glass', tr('Скло', 'Glass')), ('graphite', tr('Графіт', 'Graphite')), ('light', tr('Світлий', 'Light')),
        ('color', tr('Кольоровий', 'Colour')), ('sakura', tr('Сакура (аніме)', 'Sakura (anime)')),
        ('manga', tr('Манґа (аніме)', 'Manga (anime)')), ('neon', tr('Неон (аніме)', 'Neon (anime)')),
        ('clear_dark', tr('Без фону · темний текст', 'No card · dark text')),
        ('clear_light', tr('Без фону · світлий текст', 'No card · light text')),
    ]


STYLE_KEYS = {'glass', 'graphite', 'light', 'color', 'sakura', 'manga', 'neon', 'clear_dark', 'clear_light'}

ACCENTS = [
    ('auto', None), ('red', '#FF453A'), ('orange', '#FF9F0A'), ('yellow', '#FFD60A'), ('green', '#30D158'),
    ('mint', '#63E6E2'), ('teal', '#40C8E0'), ('blue', '#0A84FF'), ('indigo', '#5E5CE6'), ('purple', '#BF5AF2'),
    ('pink', '#FF375F'), ('sakura', '#FF7EB6'), ('graphite', '#8E8E93'),
]
ACCENT_KEYS = {a for a, _ in ACCENTS}


def accent_name(key):
    return {'auto': tr('Як задумано', 'Default'), 'red': tr('Червоний', 'Red'), 'orange': tr('Помаранчевий', 'Orange'),
            'yellow': tr('Жовтий', 'Yellow'), 'green': tr('Зелений', 'Green'), 'mint': tr('М’ятний', 'Mint'),
            'teal': tr('Бірюзовий', 'Teal'), 'blue': tr('Синій', 'Blue'), 'indigo': tr('Індиго', 'Indigo'),
            'purple': tr('Фіолетовий', 'Purple'), 'pink': tr('Рожевий', 'Pink'), 'sakura': tr('Сакура', 'Sakura'),
            'graphite': tr('Графітовий', 'Graphite')}.get(key, key)


@dataclass
class Option:
    key: str
    type: str
    default: object = ''
    label: object = ''
    hint: object = ''
    choices: list = field(default_factory=list)   # [(value, label)]
    limit: int = 0
    min: float = 0
    max: float = 100
    step: float = 1

    @property
    def title(self):
        return loc(self.label) or self.key

    @property
    def hint_text(self):
        return loc(self.hint) or ''

    def default_value(self, language=None):
        return loc(self.default, language) if isinstance(self.default, dict) else self.default


@dataclass
class WidgetType:
    id: str
    dir: str
    name: object
    description: object = ''
    category: str = 'other'
    sizes: tuple = ('small',)
    interval: float = 5
    fast_if: str = ''
    style: str = 'glass'
    accent: str = 'auto'
    color_accent: str = 'indigo'
    options: list = field(default_factory=list)
    uses: tuple = ()          # services the widget needs: "media", "events"
    author: str = ''
    version: str = ''
    user: bool = False        # from ~/.config/deskwidgets/widgets

    @property
    def title(self):
        return loc(self.name) or self.id

    @property
    def desc(self):
        return loc(self.description) or ''

    @property
    def size(self):
        return self.sizes[0]

    def option(self, key):
        return next((o for o in self.options if o.key == key), None)

    def interval_for(self, values):
        opt = self.option(self.fast_if) if self.fast_if else None
        if opt and values.get(opt.key, opt.default_value()) in (True, 'true', '1', 1):
            return 1
        return self.interval


def _load(folder, user):
    manifest = os.path.join(folder, 'widget.json')
    if not (os.path.isfile(manifest) and os.path.isfile(os.path.join(folder, 'widget.lua'))):
        return None
    with open(manifest, encoding='utf-8') as f:
        m = json.load(f)
    wid = str(m.get('id') or os.path.basename(folder))
    sizes = tuple(s for s in m.get('sizes', ['small']) if s in SIZES) or ('small',)
    options = []
    for o in m.get('options', []):
        if not isinstance(o, dict) or not o.get('key') or o.get('type') not in OPTION_TYPES:
            log(f'{wid}: skipped an option without key/type: {o}')
            continue
        choices = [(c['value'], c.get('label', c['value'])) if isinstance(c, dict) else (c, c)
                   for c in o.get('choices', [])]
        options.append(Option(key=o['key'], type=o['type'], default=o.get('default', ''), label=o.get('label', o['key']),
                              hint=o.get('hint', ''), choices=choices, limit=int(o.get('limit', 0)),
                              min=float(o.get('min', 0)), max=float(o.get('max', 100)), step=float(o.get('step', 1))))
    return WidgetType(
        id=wid, dir=folder, name=m.get('name', wid), description=m.get('description', ''),
        category=m.get('category', 'other') if m.get('category') in CATEGORIES else 'other',
        sizes=sizes, interval=float(m.get('interval', 5)), fast_if=m.get('fast_if', ''),
        style=m.get('style', 'glass') if m.get('style', 'glass') in STYLE_KEYS else 'glass',
        accent=m.get('accent', 'auto') if m.get('accent', 'auto') in ACCENT_KEYS else 'auto',
        color_accent=m.get('color_accent', 'indigo'), options=options, uses=tuple(m.get('uses', [])),
        author=m.get('author', ''), version=str(m.get('version', '')), user=user)


def discover():
    """{type id: WidgetType}. User widgets override built-ins with the same id."""
    types = {}
    for base, user in ((paths.BUILTIN_WIDGETS, False), (paths.USER_WIDGETS, True)):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            folder = os.path.join(base, name)
            if not os.path.isdir(folder):
                continue
            try:
                t = _load(folder, user)
            except (OSError, ValueError, KeyError, TypeError) as e:
                log(f'widget {folder}: {e}')
                continue
            if t:
                types[t.id] = t
    return types


def option_values(inst, t, language=None):
    """Options of an instance with the (localised) type defaults filled in."""
    values = dict(inst.get('options') or {})
    for o in t.options:
        values.setdefault(o.key, o.default_value(language))
    return values


def resolved_accent(inst, t):
    """The "colour" style needs a real colour when the accent is left on auto."""
    accent = inst.get('accent') or 'auto'
    if accent == 'auto' and inst.get('style') == 'color':
        return t.color_accent
    return accent


def describe(inst):
    """Gallery subtitle, e.g. "Medium · Glass · Purple"."""
    parts = [size_name(inst.get('size')), dict(styles()).get(inst.get('style'), '')]
    if (inst.get('accent') or 'auto') != 'auto':
        parts.append(accent_name(inst['accent']))
    return ' · '.join(p for p in parts if p)
