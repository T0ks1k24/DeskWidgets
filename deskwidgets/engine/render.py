"""Headless rendering of a widget to a PNG: `deskwidgets preview`, gallery thumbnails, tests.

No window and no display are needed, and nothing but the widget itself is started.
"""
from pathlib import Path

from ..core import catalog, settings as settings_mod
from ..core.grid import Grid
from . import fonts
from .runtime import WidgetContext, WidgetRuntime
from .sysinfo import SysInfo

SCREEN = (1920, 1080)   # only decides how big a cell is: previews do not depend on the real screen


def render_surface(wtype, *, size=None, style=None, accent=None, lang='uk', scale=1.0, options=None, demo=True,
                   settings=None, font_map=None, sysinfo=None, frames=3):
    """Draws one frame of `wtype` and returns the cairo surface."""
    settings = settings or settings_mod.load()
    size = size or wtype.size
    inst = {'id': f'preview-{wtype.id}', 'type': wtype.id, 'size': size, 'style': style or wtype.style,
            'accent': accent or wtype.accent, 'options': options or {}}
    grid = Grid(*SCREEN, {**settings, 'scale': scale})
    ctx = WidgetContext(inst, wtype, lang=lang, scale=scale, size_px=grid.window_px(size),
                        fonts=font_map or fonts.setup(settings), sysinfo=sysinfo or SysInfo(threaded=False), demo=demo)
    runtime = WidgetRuntime(ctx)
    for _ in range(frames):   # widgets that sample values (rates, history) need a few ticks
        surface = runtime.render()
    return surface


def render_png(wtype, out, **kwargs):
    """Renders `wtype` into the file `out`. Returns True on success."""
    try:
        surface = render_surface(wtype, **kwargs)
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        surface.write_to_png(str(out))
        return True
    except Exception as e:
        from ..core.util import log
        log(f'{wtype.id}: rendering failed: {e}')
        return False


def render_all(out_dir, lang, types=None, **kwargs):
    """Gallery thumbnails: one PNG per widget type. Returns True when all of them rendered."""
    types = types if types is not None else catalog.discover()
    font_map = kwargs.pop('font_map', None) or fonts.setup(kwargs.get('settings') or settings_mod.load())
    return all([render_png(t, Path(out_dir) / f'{t.id}.png', lang=lang, font_map=font_map, **kwargs)
                for t in types.values() if t.supported])
