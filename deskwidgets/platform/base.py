"""What the service needs from an operating system, as small interfaces.

* `Backend`  - the event loop, timers, screen geometry and the widget windows
* `Window`   - one borderless transparent window that shows a Cairo image
* `Timer`    - a cancellable one-shot timer

Everything the service does goes through these, so Linux (X11 / Xwayland) and Windows differ
only in `x11.py` and `win32.py`. `null.py` is a headless implementation used by the tests.

Threading: a backend runs its loop on the thread that calls `run()`. Windows, timers and
callbacks live on that thread; `call_soon()` is the only method other threads may call.
"""
import heapq
import itertools
import threading
import time
from abc import ABC, abstractmethod

from ..core.monitors import Monitor

LEFT, MIDDLE, RIGHT = 1, 2, 3   # mouse buttons in `on_button`


class Window(ABC):
    """A borderless, transparent, non-focusable window below the normal windows, not in the taskbar,
    visible on every virtual desktop. Coordinates are screen pixels, (0, 0) = top-left of the screen.

    Set these callbacks (all optional, all called on the loop thread):
        on_button(button, pressed, x, y, root_x, root_y)   mouse button down/up; x, y inside the window,
                                                           root_* on the screen
        on_motion(x, y, root_x, root_y)                    pointer moved over the window (also while a
                                                           button is held, even outside the window)
        on_moved(x, y)                                     the window was moved by the user or the window
                                                           manager (not by `move()`)
        on_closed()                                        the window was destroyed from outside
    """
    on_button = on_motion = on_moved = on_closed = None

    @abstractmethod
    def present(self, surface):
        """Shows `surface` (a pycairo ARGB32 ImageSurface, premultiplied alpha) and makes the window
        visible. If its size differs from the window's, the window is resized to match."""

    @abstractmethod
    def move(self, x, y):
        """Moves the window so its top-left corner is at (x, y)."""

    @abstractmethod
    def position(self):
        """(x, y) of the window's top-left corner as it is now on screen, or None if unknown."""

    @abstractmethod
    def close(self):
        """Destroys the window. Further calls are ignored."""


class Timer:
    def __init__(self, due, callback, seq):
        self.due, self.callback, self.seq, self.cancelled = due, callback, seq, False

    def cancel(self):
        self.cancelled = True

    def __lt__(self, other):
        return (self.due, self.seq) < (other.due, other.seq)


class TimerQueue:
    """Timers for a backend loop. `next_timeout()` says how long the loop may sleep, `run_due()`
    calls what is due. Thread-safe so `call_soon()` can feed it from anywhere."""

    def __init__(self):
        self._heap = []
        self._lock = threading.Lock()
        self._seq = itertools.count()

    def add(self, delay, callback):
        timer = Timer(time.monotonic() + max(0.0, delay), callback, next(self._seq))
        with self._lock:
            heapq.heappush(self._heap, timer)
        return timer

    def next_timeout(self):
        """Seconds until the next timer (0 if one is due), None if there are none."""
        with self._lock:
            while self._heap and self._heap[0].cancelled:
                heapq.heappop(self._heap)
            if not self._heap:
                return None
            return max(0.0, self._heap[0].due - time.monotonic())

    def run_due(self):
        while True:
            with self._lock:
                while self._heap and self._heap[0].cancelled:
                    heapq.heappop(self._heap)
                if not self._heap or self._heap[0].due > time.monotonic():
                    return
                timer = heapq.heappop(self._heap)
            timer.callback()


class Backend(ABC):
    """The loop, the screen and the windows.

    Set `on_screen_changed` (no arguments) to hear about a monitor that was plugged in, unplugged or changed
    (resolution, work area, DPI)."""

    on_screen_changed = None

    # loop
    @abstractmethod
    def run(self):
        """Runs the loop until `quit()`. Exceptions raised by callbacks are logged, not fatal."""

    @abstractmethod
    def quit(self):
        """Ends `run()`. Safe to call from any thread."""

    @abstractmethod
    def call_soon(self, callback):
        """Runs `callback()` on the loop thread as soon as possible. Safe to call from any thread."""

    @abstractmethod
    def call_later(self, seconds, callback):
        """Runs `callback()` on the loop thread after `seconds`. Returns a Timer (`.cancel()`)."""

    # screen
    @abstractmethod
    def work_area(self):
        """(x, y, width, height) in pixels of the usable area of the primary monitor
        (without a Windows taskbar; on Linux the whole screen)."""

    def dpi_scale(self):
        """1.0 for 96 dpi, 1.5 for 144 dpi ... (the widgets are drawn that much larger)."""
        return 1.0

    def monitors(self):
        """Every connected monitor as a `core.monitors.Monitor`, the primary one first. A backend that cannot
        tell reports the one screen `work_area()` describes."""
        x, y, width, height = self.work_area()
        return [Monitor('screen', x, y, width, height, True, self.dpi_scale())]

    # windows
    @abstractmethod
    def create_window(self, title, width, height, x, y, click_through=False):
        """A hidden `Window` of this size at (x, y); it appears with the first `present()`.
        `click_through=True`: mouse events pass through it to what is below (the grid overlay)."""

    # optional desktop integration; backends return None / False when the platform has no equivalent
    def create_tray(self, tooltip, icon_path, items):
        """A notification-area icon whose menu has `items` = [(label, callback)] (None = separator).
        Returns an object with `.notify(title, body)` and `.close()`, or None."""
        return None

    def register_hotkey(self, spec, callback):
        """Global hotkey, e.g. 'ctrl+alt+w'. Returns True when registered."""
        return False
