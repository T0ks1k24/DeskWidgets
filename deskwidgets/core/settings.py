"""Global settings (settings.json in the config folder) and the UI language."""
import json
import math

from . import paths
from .i18n import system_language
from .util import write_atomic

DEFAULTS = {
    'language': 'auto',        # auto | uk | en
    'scale': 1.0,              # size of every widget, 0.6-2.0
    'gap': 16,                 # px between widgets
    'margin_top': 44,          # first grid row starts below the top bar
    'margin_bottom': 88,       # keep free for a dock or the taskbar
    'margin_side': 24,
    'font': 'auto',            # text font family; auto = the system's UI font
    'default_style': 'auto',   # style for new widgets, auto = the widget's own
    'default_accent': 'auto',
    'autostart': True,
    'hotkey': True,            # Super+Alt+W (Ctrl+Alt+W on Windows) opens the gallery
    'show_grid': True,         # draw the grid while a widget is being dragged
    'lock': False,             # widgets cannot be dragged
}

LIMITS = {'scale': (0.6, 2.0), 'gap': (0, 64), 'margin_top': (0, 400), 'margin_bottom': (0, 400),
          'margin_side': (0, 400)}


def load():
    data = dict(DEFAULTS)
    try:
        stored = json.loads(paths.SETTINGS.read_text(encoding='utf-8'))
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in DEFAULTS or k.startswith('_')})
    except (OSError, ValueError):
        pass
    for key, (lo, hi) in LIMITS.items():
        try:
            value = type(DEFAULTS[key])(data[key])
            if not math.isfinite(value):
                raise ValueError('non-finite setting')
            data[key] = min(max(value, lo), hi)
        except (TypeError, ValueError, OverflowError):
            data[key] = DEFAULTS[key]
    for key in ('autostart', 'hotkey', 'show_grid', 'lock'):
        if not isinstance(data[key], bool):
            data[key] = DEFAULTS[key]
    return data


def save(data):
    write_atomic(paths.SETTINGS, json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def language(data=None):
    lang = (data or load()).get('language', 'auto')
    return lang if lang in ('uk', 'en') else system_language()
