"""One widget on the desktop: its Lua runtime, its window, its redraw timer, and what the mouse does to it."""
import time

from ..core import catalog
from ..core.util import log
from ..engine.runtime import WidgetContext, WidgetRuntime
from ..platform.base import LEFT, RIGHT

DRAG_THRESHOLD = 6        # pixels the pointer must travel before a click becomes a drag
SETTLE_DELAY = 0.4        # seconds without movement before a window moved by the WM snaps to the grid
SLOW_FRAME = 0.25         # a frame slower than this (seconds) is reported: the widget blocks every other one
MIN_INTERVAL = 0.2


class WidgetInstance:
    def __init__(self, daemon, inst_id):
        self.d = daemon
        self.id = inst_id
        self.runtime = None
        self.window = None
        self.timer = None
        self.settle_timer = None
        self.offset = (0, 0)        # where the window manager put the window relative to where we asked
        self.requested = (0, 0)
        self.monitor = None         # the monitor the window is on (its grid sets the window's size)
        self.size_px = (0, 0)
        self._press = None          # (root_x, root_y, window position) while the left button is down
        self._dragging = False
        self._slow_reported = False

    @property
    def inst(self):
        return self.d.layout.get(self.id)

    @property
    def wtype(self):
        inst = self.inst
        return self.d.types.get(inst['type']) if inst else None

    @property
    def running(self):
        return self.window is not None

    # lifecycle
    def start(self):
        inst, t = self.inst, self.wtype
        if self.window is not None or inst is None or t is None or not t.supported:
            return
        self.monitor = self.d.screen_of(inst['monitor']).monitor.id
        grid = self.d.screen_of(self.monitor).grid
        self.size_px = size_px = grid.window_px(inst['size'])
        ctx = WidgetContext(inst, t, lang=self.d.lang, scale=grid.scale, size_px=size_px, fonts=self.d.fonts,
                            sysinfo=self.d.sysinfo, images=self.d.images, services=self.d.hub, notify=self.d.notify)
        try:
            self.runtime = WidgetRuntime(ctx)
        except Exception as e:   # Lua failed to even start: the other widgets carry on
            log(f'{self.id}: {e}')
            return
        self.requested = self.d.window_xy(inst['col'], inst['row'], self.monitor)
        self.window = self.d.backend.create_window(f'deskwidget:{self.id}', *size_px, *self.requested)
        self.window.on_button = self._on_button
        self.window.on_motion = self._on_motion
        self.window.on_moved = self._on_moved
        self.offset = (0, 0)
        self._frame()
        self.d.backend.call_later(0.5, self._measure_offset)

    def stop(self):
        for timer in (self.timer, self.settle_timer):
            if timer:
                timer.cancel()
        self.timer = self.settle_timer = None
        if self.window:
            self.window.on_button = self.window.on_motion = self.window.on_moved = None
            self.window.close()
        self.window = self.runtime = None
        self._press, self._dragging = None, False

    def restart(self):
        self.stop()
        self.start()

    # drawing
    def interval(self):
        t, inst = self.wtype, self.inst
        if t is None or inst is None:
            return 5.0
        return max(MIN_INTERVAL, t.interval_for(catalog.option_values(inst, t)))

    def _frame(self):
        self.timer = None
        if self.runtime is None:
            return
        began = time.monotonic()
        try:
            self.window.present(self.runtime.render())
        except Exception as e:   # one broken frame must not stop the widget for good
            log(f'{self.id}: {e}')
        took = time.monotonic() - began
        if took > SLOW_FRAME and not self._slow_reported:
            self._slow_reported = True
            log(f'{self.id}: a frame took {took:.2f}s; slow work belongs in a helper script (see docs/creating-widgets.md)')
        self.timer = self.d.backend.call_later(self.interval(), self._frame)

    def redraw_soon(self):
        """Right after a click: show what it changed without waiting for the next interval."""
        if self.timer:
            self.timer.cancel()
        self.timer = self.d.backend.call_later(0.05, self._frame)

    # position
    def relocate(self):
        """The layout moved this widget (a drop, `move`): put the window where it says. Another monitor can have
        another DPI, hence another size, so the widget starts over there."""
        inst = self.inst
        if inst is None:
            return
        if self.d.screen_of(inst['monitor']).monitor.id != self.monitor:
            self.restart()
        else:
            self.place(inst['col'], inst['row'])

    def place(self, col, row):
        """Moves the window onto a cell of its monitor."""
        x, y = self.d.window_xy(col, row, self.monitor)
        self.requested = (x, y)
        if self.window:
            self.window.move(x + self.offset[0], y + self.offset[1])

    def _measure_offset(self):
        """Some window managers shift a new window (panels, constraints); remember by how much so snapping
        compares like with like."""
        if self.window is None:
            return
        pos = self.window.position()
        if pos:
            dx, dy = pos[0] - self.requested[0], pos[1] - self.requested[1]
            self.offset = (dx, dy) if abs(dx) <= 48 and abs(dy) <= 48 else (0, 0)

    def window_origin(self, pos):
        """Where the window would sit without the manager's shift: for finding the nearest cell."""
        return pos[0] - self.offset[0], pos[1] - self.offset[1]

    # mouse
    def _on_button(self, button, pressed, x, y, root_x, root_y):
        if button == RIGHT:
            if not pressed:
                self.d.open_gallery(configure=self.id)
        elif button == LEFT:
            if pressed:
                self._press = (root_x, root_y, self.window.position() or self.requested)
                self._dragging = False
            else:
                self._release(x, y)

    def _on_motion(self, x, y, root_x, root_y):
        if self._press is None or self.d.settings['lock']:
            return
        start_x, start_y, (win_x, win_y) = self._press
        dx, dy = root_x - start_x, root_y - start_y
        if not self._dragging:
            if dx * dx + dy * dy < DRAG_THRESHOLD ** 2:
                return
            self._dragging = True
            self.d.drag_started(self)
        pos = (win_x + dx, win_y + dy)
        self.window.move(*pos)
        self.d.drag_moved(self, pos)

    def _release(self, x, y):
        dragging, self._press, self._dragging = self._dragging, None, False
        if dragging:
            self.d.drop(self, self.window.position() or self.requested)
        elif self.runtime and self.runtime.click(x, y):
            self.redraw_soon()

    def _on_moved(self, x, y):
        """The window manager moved the window (Super+drag on Linux): snap once it stops."""
        if self._press is not None:
            return
        if self.settle_timer:
            self.settle_timer.cancel()
        self.settle_timer = self.d.backend.call_later(SETTLE_DELAY, self._settled)

    def _settled(self):
        self.settle_timer = None
        if self.window and self._press is None:
            pos = self.window.position()
            if pos and pos != (self.requested[0] + self.offset[0], self.requested[1] + self.offset[1]):
                self.d.drop(self, pos)
