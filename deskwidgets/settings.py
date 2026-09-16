"""Global settings (~/.config/deskwidgets/settings.json) and the UI language."""
import json
import os

from . import paths
from .util import write_atomic

DEFAULTS = {
    'language': 'auto',        # auto | uk | en
    'scale': 1.0,              # size of every widget, 0.8-1.5
    'gap': 16,                 # px between widgets
    'margin_top': 44,          # first grid row starts below the top bar
    'margin_bottom': 88,       # keep free for a dock
    'margin_side': 24,
    'font': 'Ubuntu Sans',     # text font family (icons come with DeskWidgets)
    'default_style': 'auto',   # style for new widgets, auto = the widget's own
    'default_accent': 'auto',
    'autostart': True,
    'hotkey': True,            # Super+Alt+W opens the gallery (GNOME)
    'grid_overlay': True,      # GNOME Shell helper: show the grid while dragging, snap on release
}

LIMITS = {'scale': (0.6, 2.0), 'gap': (0, 64), 'margin_top': (0, 400), 'margin_bottom': (0, 400), 'margin_side': (0, 400)}


def load():
    data = dict(DEFAULTS)
    try:
        with open(paths.SETTINGS, encoding='utf-8') as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in DEFAULTS or k.startswith('_')})
    except (OSError, ValueError):
        pass
    for key, (lo, hi) in LIMITS.items():
        try:
            data[key] = min(max(type(DEFAULTS[key])(data[key]), lo), hi)
        except (TypeError, ValueError):
            data[key] = DEFAULTS[key]
    return data


def save(data):
    os.makedirs(paths.CONFIG, exist_ok=True)
    write_atomic(paths.SETTINGS, json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def language(data=None):
    lang = (data or load()).get('language', 'auto')
    if lang in ('uk', 'en'):
        return lang
    for var in ('LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'LANG'):
        value = os.environ.get(var, '')
        if value:
            return 'uk' if value.split(':')[0].startswith('uk') else 'en'
    return 'en'
