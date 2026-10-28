"""The Cairo API that widgets see: `cairo_arc(cr, ...)`, `CAIRO_LINE_CAP_ROUND`, ...

Widgets were written against conky's Cairo bindings, so the same C-style names are provided
here on top of pycairo. Almost every function is `cairo_<method>(cr, ...)` -> `cr.<method>(...)`,
so those are generated; the few that differ (text extents, patterns, surfaces) are written out.
"""
import cairo

_ENUM_PREFIXES = ('FORMAT_', 'FONT_SLANT_', 'FONT_WEIGHT_', 'LINE_CAP_', 'LINE_JOIN_', 'ANTIALIAS_', 'HINT_STYLE_',
                  'HINT_METRICS_', 'FILTER_', 'EXTEND_', 'OPERATOR_', 'FILL_RULE_', 'SUBPIXEL_ORDER_', 'CONTENT_')
_EXTENT_FIELDS = ('x_bearing', 'y_bearing', 'width', 'height', 'x_advance', 'y_advance')
_FONT_EXTENT_FIELDS = ('ascent', 'descent', 'height', 'max_x_advance', 'max_y_advance')


def text(value):
    """Lua strings arrive as bytes; widgets may hand us anything, so never fail on bad UTF-8."""
    return value.decode('utf-8', 'replace') if isinstance(value, bytes) else str(value)


class CairoAPI:
    """Installs the cairo_* functions and CAIRO_* constants into a Lua state."""

    def __init__(self, fonts):
        self.fonts = fonts   # FontMap: widget font names -> real faces

    def install(self, lua):
        g = lua.globals()
        for name in dir(cairo):
            if name.startswith(_ENUM_PREFIXES):
                g['CAIRO_' + name] = int(getattr(cairo, name))
        for name in dir(cairo.Context):
            if not name.startswith('_') and callable(getattr(cairo.Context, name)):
                g['cairo_' + name] = _method(name)
        for name, function in self._functions().items():
            g[name] = function
        # `cairo_text_extents_t:create()` is conky's way to get a table the extents are written into
        g['cairo_text_extents_t'] = lua.eval("{ create = function() return {} end }")
        g['cairo_font_extents_t'] = lua.eval("{ create = function() return {} end }")

    def _functions(self):
        def create(surface):
            return cairo.Context(surface)

        def noop(*_args):
            """cairo_destroy & co.: Python's garbage collector owns the objects."""

        def select_font_face(cr, family, slant, weight):
            family, weight = self.fonts.face(text(family), weight)
            cr.select_font_face(family, slant, weight)

        def text_extents(cr, value, table):
            for key, number in zip(_EXTENT_FIELDS, cr.text_extents(text(value))):
                table[key] = number

        def font_extents(cr, table):
            for key, number in zip(_FONT_EXTENT_FIELDS, cr.font_extents()):
                table[key] = number

        def show_text(cr, value):
            cr.show_text(text(value))

        def text_path(cr, value):
            cr.text_path(text(value))

        def image_surface_from_png(path):
            try:
                return cairo.ImageSurface.create_from_png(text(path))
            except (cairo.Error, OSError, MemoryError):
                return None   # conky gives a 0x0 surface; get_width() below answers 0 for None

        def image_size(attribute):
            return lambda surface: getattr(surface, attribute)() if surface is not None else 0

        def write_to_png(surface, path):
            surface.write_to_png(text(path))

        def pattern_rgba(r, g, b, a=1):
            return cairo.SolidPattern(r, g, b, a)

        return {
            'cairo_create': create, 'cairo_destroy': noop, 'cairo_surface_destroy': noop,
            'cairo_pattern_destroy': noop, 'cairo_font_options_destroy': noop,
            'cairo_select_font_face': select_font_face,
            'cairo_text_extents': text_extents, 'cairo_font_extents': font_extents,
            'cairo_show_text': show_text, 'cairo_text_path': text_path,
            'cairo_image_surface_create': lambda fmt, w, h: cairo.ImageSurface(fmt, int(w), int(h)),
            'cairo_image_surface_create_from_png': image_surface_from_png,
            'cairo_image_surface_get_width': image_size('get_width'),
            'cairo_image_surface_get_height': image_size('get_height'),
            'cairo_surface_write_to_png': write_to_png,
            'cairo_surface_flush': lambda surface: surface.flush(),
            'cairo_pattern_create_linear': lambda x0, y0, x1, y1: cairo.LinearGradient(x0, y0, x1, y1),
            'cairo_pattern_create_radial': lambda *a: cairo.RadialGradient(*a),
            'cairo_pattern_create_rgb': lambda r, g, b: cairo.SolidPattern(r, g, b),
            'cairo_pattern_create_rgba': pattern_rgba,
            'cairo_pattern_create_for_surface': lambda surface: cairo.SurfacePattern(surface),
            'cairo_pattern_add_color_stop_rgb': lambda p, o, r, g, b: p.add_color_stop_rgb(o, r, g, b),
            'cairo_pattern_add_color_stop_rgba': lambda p, o, r, g, b, a: p.add_color_stop_rgba(o, r, g, b, a),
            'cairo_pattern_set_filter': lambda p, value: p.set_filter(value),
            'cairo_pattern_set_extend': lambda p, value: p.set_extend(value),
            'cairo_font_options_create': cairo.FontOptions,
            'cairo_font_options_set_antialias': lambda fo, v: fo.set_antialias(v),
            'cairo_font_options_set_hint_style': lambda fo, v: fo.set_hint_style(v),
            'cairo_font_options_set_hint_metrics': lambda fo, v: fo.set_hint_metrics(v),
            'cairo_font_options_set_subpixel_order': lambda fo, v: fo.set_subpixel_order(v),
        }


def _method(name):
    def call(cr, *args):
        return getattr(cr, name)(*args)
    call.__name__ = 'cairo_' + name
    return call
