"""The grid every widget snaps to. A cell is a small card, medium is 2x1 cells, large is 2x2."""
from .catalog import SIZES

BASE_CELL = 170   # small card at 100 %
BASE_MARGIN = 16  # transparent shadow margin around a card (lib.lua L.M)


class Grid:
    def __init__(self, screen_w, screen_h, settings):
        self.scale = float(settings['scale'])
        self.cell = round(BASE_CELL * self.scale)
        self.gap = int(settings['gap'])
        self.margin = round(BASE_MARGIN * self.scale)
        self.pitch = self.cell + self.gap
        side, top, bottom = int(settings['margin_side']), int(settings['margin_top']), int(settings['margin_bottom'])
        self.cols = max(2, (screen_w - 2 * side + self.gap) // self.pitch)
        self.rows = max(2, (screen_h - top - bottom + self.gap) // self.pitch)
        self.x0 = (screen_w - (self.cols * self.pitch - self.gap)) // 2
        self.y0 = top

    def card_px(self, size):
        cw, ch = SIZES[size]
        return cw * self.cell + (cw - 1) * self.gap, ch * self.cell + (ch - 1) * self.gap

    def window_px(self, size):
        w, h = self.card_px(size)
        return w + 2 * self.margin, h + 2 * self.margin

    def window_xy(self, col, row):
        return self.x0 + col * self.pitch - self.margin, self.y0 + row * self.pitch - self.margin

    def fits(self, col, row, size):
        cw, ch = SIZES[size]
        return 0 <= col <= self.cols - cw and 0 <= row <= self.rows - ch

    @staticmethod
    def cells(col, row, size):
        cw, ch = SIZES[size]
        return {(c, r) for c in range(col, col + cw) for r in range(row, row + ch)}

    def nearest_cell(self, win_x, win_y, size):
        cw, ch = SIZES[size]
        col = round((win_x + self.margin - self.x0) / self.pitch)
        row = round((win_y + self.margin - self.y0) / self.pitch)
        return min(max(col, 0), self.cols - cw), min(max(row, 0), self.rows - ch)

    def free_near(self, col, row, size, taken):
        """Free spot for `size` closest to (col, row), or None when the grid is full."""
        spots = [(c, r) for c in range(self.cols) for r in range(self.rows) if self.fits(c, r, size)]
        spots.sort(key=lambda p: ((p[0] - col) ** 2 + (p[1] - row) ** 2, p[1], p[0]))
        for c, r in spots:
            if not (self.cells(c, r, size) & taken):
                return c, r
        return None

    def first_free(self, size, taken):
        """New widgets fill the outer columns first, so the middle of the wallpaper stays clear."""
        cw, _ = SIZES[size]
        last = self.cols - cw
        order = []
        for i in range(last + 1):
            for c in (i, last - i):
                if c not in order and 0 <= c <= last:
                    order.append(c)
        for c in order:
            for r in range(self.rows):
                if self.fits(c, r, size) and not (self.cells(c, r, size) & taken):
                    return c, r
        return None

    def to_json(self, instances):
        """Description for the GNOME Shell helper that draws the grid while you drag."""
        return {
            'x0': self.x0, 'y0': self.y0, 'cols': self.cols, 'rows': self.rows, 'cell': self.cell,
            'gap': self.gap, 'pitch': self.pitch, 'margin': self.margin, 'radius': round(22 * self.scale),
            'widgets': {i['id']: {'col': i['col'], 'row': i['row'], 'w': SIZES[i['size']][0], 'h': SIZES[i['size']][1]}
                        for i in instances},
        }
