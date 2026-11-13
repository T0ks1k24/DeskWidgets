"""The grid that appears while a widget is dragged, with the cell it will snap to highlighted."""
import cairo

from ..core.catalog import SIZES
from ..core.grid import BASE_RADIUS

# Readable on any wallpaper: a translucent dark fill shows on light pictures, the light outline on dark ones,
# and the thin dark rim around it keeps the outline visible on mid-tones.
CELL_FILL = (0, 0, 0, 0.18)
CELL_STROKES = [((0, 0, 0, 0.30), 3.5), ((1, 1, 1, 0.70), 1.5)]
TARGET_FILL = (0.04, 0.52, 1.0, 0.38)
TARGET_STROKES = [((1, 1, 1, 0.85), 4.5), ((0.04, 0.52, 1.0, 1.0), 2.5)]


class GridOverlay:
    """A click-through window over the grid of one monitor: the one the dragged widget is over, so it moves
    to another monitor when the widget does. It is redrawn only when the target cell changes."""

    def __init__(self, daemon):
        self.d = daemon
        self.window = None
        self.monitor = None
        self.target = None
        self.origin = (0, 0)

    def show(self, instance):
        if not self.d.settings['show_grid'] or self.window is not None:
            return
        self._open(instance.monitor)
        self.update(instance, instance.window.position() or instance.requested)

    def _open(self, monitor):
        screen = self.d.screen_of(monitor)
        grid = screen.grid
        width = grid.cols * grid.pitch - grid.gap + 2
        height = grid.rows * grid.pitch - grid.gap + 2
        ox, oy = screen.origin
        self.origin = (ox + grid.x0 - 1, oy + grid.y0 - 1)
        self.monitor = screen.monitor.id
        self.window = self.d.backend.create_window('deskwidgets-grid', width, height, *self.origin, click_through=True)
        self.target = None

    def update(self, instance, pos):
        if self.window is None:
            return
        inst = instance.inst
        monitor, col, row = self.d.nearest_cell(instance, pos)
        if monitor != self.monitor:
            self.window.close()
            self._open(monitor)
        spot = self.d.screen_of(monitor).grid.free_near(col, row, inst['size'],
                                                        self.d.layout.taken(monitor, exclude=inst['id']))
        if spot == self.target:
            return
        self.target = spot
        self.window.present(self._draw(inst['size'], spot))

    def hide(self):
        if self.window is not None:
            self.window.close()
        self.window, self.monitor, self.target = None, None, None

    def _draw(self, size, spot):
        grid = self.d.screen_of(self.monitor).grid
        width, height = self.window.width, self.window.height
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        cr = cairo.Context(surface)
        radius = round(BASE_RADIUS * grid.scale)
        taken = self.d.layout.taken(self.monitor)
        target = self._target_cells(size, spot)
        for col in range(grid.cols):
            for row in range(grid.rows):
                if (col, row) in taken and (col, row) not in target:
                    continue
                self._cell(cr, 1 + col * grid.pitch, 1 + row * grid.pitch, grid.cell, grid.cell, radius,
                           CELL_FILL, CELL_STROKES)
        if spot is not None:
            self._cell(cr, 1 + spot[0] * grid.pitch, 1 + spot[1] * grid.pitch, *grid.card_px(size), radius,
                       TARGET_FILL, TARGET_STROKES)
        surface.flush()
        return surface

    @staticmethod
    def _target_cells(size, spot):
        if spot is None:
            return set()
        cw, ch = SIZES[size]
        return {(spot[0] + c, spot[1] + r) for c in range(cw) for r in range(ch)}

    @staticmethod
    def _cell(cr, x, y, w, h, r, fill, strokes):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -1.5708, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
        cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
        cr.arc(x + r, y + r, r, 3.1416, 4.7124)
        cr.close_path()
        cr.set_source_rgba(*fill)
        cr.fill_preserve()
        for color, width in strokes:
            cr.set_source_rgba(*color)
            cr.set_line_width(width)
            cr.stroke_preserve()
        cr.new_path()
