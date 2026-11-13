"""A headless backend for tests: no display, windows are plain objects, time only moves when the test says so."""
import threading

from .base import Backend, TimerQueue, Window


class NullWindow(Window):
    def __init__(self, title, width, height, x, y, click_through=False):
        self.title, self.width, self.height, self.x, self.y = title, width, height, x, y
        self.click_through = click_through
        self.presented = 0
        self.last_surface = None
        self.closed = False

    def present(self, surface):
        self.presented += 1
        self.last_surface = surface
        self.width, self.height = surface.get_width(), surface.get_height()

    def move(self, x, y):
        self.x, self.y = x, y

    def position(self):
        return (self.x, self.y)

    def close(self):
        self.closed = True

    # helpers for tests: what a user would do
    def press(self, x=5, y=5, button=1):
        self.on_button(button, True, x, y, self.x + x, self.y + y)

    def release(self, x=5, y=5, button=1):
        self.on_button(button, False, x, y, self.x + x, self.y + y)

    def motion(self, x, y):
        self.on_motion(x, y, self.x + x, self.y + y)


class NullBackend(Backend):
    def __init__(self, screen=(1920, 1080), scale=1.0):
        self.screen, self.scale = screen, scale
        self.timers = TimerQueue()
        self.windows = []
        self._soon = []
        self._lock = threading.Lock()
        self._quit = threading.Event()

    def run(self):
        while not self._quit.is_set():
            self.flush()
            timeout = self.timers.next_timeout()
            self._quit.wait(0.01 if timeout is None else min(timeout, 0.05))
        self.flush()

    def flush(self):
        """Runs what is queued and due (tests call this instead of running the loop)."""
        with self._lock:
            soon, self._soon = self._soon, []
        for callback in soon:
            callback()
        self.timers.run_due()

    def quit(self):
        self._quit.set()

    def call_soon(self, callback):
        with self._lock:
            self._soon.append(callback)

    def call_later(self, seconds, callback):
        return self.timers.add(seconds, callback)

    def work_area(self):
        return (0, 0) + tuple(self.screen)

    def dpi_scale(self):
        return self.scale

    def create_window(self, title, width, height, x, y, click_through=False):
        window = NullWindow(title, width, height, x, y, click_through)
        self.windows.append(window)
        return window
