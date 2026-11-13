"""One widget instance: its own Lua state, drawn into a Cairo surface.

    ctx = WidgetContext(inst, wtype, lang='en', scale=1.0, size_px=(202, 202), fonts=..., ...)
    runtime = WidgetRuntime(ctx)
    surface = runtime.render()          # an ARGB32 ImageSurface, ready for a window or a PNG
    runtime.click(x, y)

Needs only lupa and pycairo. Windows, timers and the like belong to `deskwidgets.platform`.
"""
from dataclasses import dataclass, field
from typing import Callable

import cairo

try:   # the embedded Lua conky used is 5.3/5.4; any lupa build of 5.3+ will do
    from lupa import lua54 as lupa_lua
except ImportError:   # pragma: no cover - depends on how lupa was built
    try:
        from lupa import lua53 as lupa_lua
    except ImportError:
        import lupa as lupa_lua

if tuple(getattr(lupa_lua, 'LUA_VERSION', (0, 0))[:2]) < (5, 3):
    raise ImportError('DeskWidgets needs a lupa built with Lua 5.3 or newer (pip install --upgrade lupa)')

from ..core import catalog, paths
from ..core.util import log
from .cairo_api import CairoAPI
from .fonts import FontMap
from .hostapi import HostAPI
from .images import ImageCache
from .sysinfo import SysInfo


class NoServices:
    """Stand-in for the background services (media, events ...) when there are none, e.g. in previews."""

    def snapshot(self, name):
        return None

    def media_control(self, command):
        return False


@dataclass
class WidgetContext:
    inst: dict                       # the layout entry: id, size, style, accent, options
    wtype: catalog.WidgetType
    lang: str
    scale: float
    size_px: tuple                   # window size in pixels
    fonts: FontMap
    sysinfo: SysInfo
    images: ImageCache = field(default_factory=ImageCache)
    services: object = field(default_factory=NoServices)
    demo: bool = False               # show sample data (gallery thumbnails, previews)
    notify: Callable = lambda title, body: None

    @property
    def accent(self):
        return catalog.resolved_accent(self.inst, self.wtype)

    def option_strings(self):
        """Option values the way Lua reads them (L.opt): everything is a string; on/off is '1'/'0'."""
        values = catalog.option_values(self.inst, self.wtype, self.lang)
        out = {}
        for key, value in values.items():
            if isinstance(value, bool):
                value = '1' if value else '0'
            elif isinstance(value, (list, tuple)):
                value = ','.join(map(str, value))
            out[key] = str(value)
            if self.wtype.option(key) is not None and self.wtype.option(key).type == 'file':
                out[key + '_png'] = str(value)   # 1.x/2.x widgets read the converted copy; any picture works now
        return out


class WidgetRuntime:
    def __init__(self, ctx):
        self.ctx = ctx
        width, height = ctx.size_px
        self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        self.lua = lupa_lua.LuaRuntime(encoding="utf-8", unpack_returned_tuples=True)
        self._main = None
        self._failed = False
        self._start()

    def _start(self):
        lua = self.lua
        g = lua.globals()
        CairoAPI(self.ctx.fonts).install(lua)
        g.host = HostAPI(lua, self.ctx).table()
        # `require 'lib'` finds the engine's library; widget folders may ship modules of their own
        lua.execute(
            'package.path = ... .. "/?.lua;" .. host.widget_dir .. "/?.lua;" .. package.path',
            str(paths.LUA).replace('\\', '/'))
        try:
            self._main = lua.eval("(require('main'))")
        except Exception as e:   # lib.lua itself is broken: nothing sensible can be drawn
            log(f"{self.ctx.inst['id']}: cannot start the widget: {e}")
            self._failed = True

    def render(self):
        """Runs tick() and draw(); returns the surface (reused: copy it before the next call)."""
        cr = cairo.Context(self.surface)
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        if not self._failed:
            try:
                self._main.frame(cr)
            except Exception as e:
                log(f"{self.ctx.inst['id']}: {e}")
        self.surface.flush()
        return self.surface

    def click(self, x, y):
        """Left click at window pixel (x, y). True when the widget handled it."""
        if self._failed:
            return False
        try:
            return bool(self._main.click(x, y))
        except Exception as e:
            log(f"{self.ctx.inst['id']}: {e}")
            return False
