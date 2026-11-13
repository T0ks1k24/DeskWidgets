"""The Linux backend: widget windows drawn straight onto an X server (X11 desktops and Xwayland).

Each widget is its own 32-bit ARGB window, so a compositing window manager blends the transparent
corners and shadows for us. Pixels are pushed with plain XPutImage: the images are a few hundred
pixels wide and change about once a second, which does not justify shared memory.

Everything here runs on the loop thread; the self-pipe behind `call_soon()` and `quit()` is the only
thing other threads touch.
"""
import collections
import ctypes
import math
import os
import re
import select
import sys
import threading
import time
import traceback

import cairo

from ..core.monitors import Monitor, ordered
from ..core.util import log
from . import x11_xlib as xl
from .base import Backend, TimerQueue, Window

ATOMS = ('WM_PROTOCOLS', 'WM_DELETE_WINDOW', 'UTF8_STRING', 'RESOURCE_MANAGER', '_MOTIF_WM_HINTS', '_NET_WM_NAME',
         '_NET_WM_PID', '_NET_WM_USER_TIME', '_NET_WM_DESKTOP', '_NET_WM_STATE', '_NET_WM_STATE_BELOW',
         '_NET_WM_STATE_STICKY', '_NET_WM_STATE_SKIP_TASKBAR', '_NET_WM_STATE_SKIP_PAGER', '_NET_WM_WINDOW_TYPE',
         '_NET_WM_WINDOW_TYPE_NORMAL')
EVENT_MASK = (xl.BUTTON_PRESS_MASK | xl.BUTTON_RELEASE_MASK | xl.POINTER_MOTION_MASK | xl.BUTTON_MOTION_MASK |
              xl.EXPOSURE_MASK | xl.STRUCTURE_NOTIFY_MASK)
SCROLL_BUTTONS = (4, 5, 6, 7)
ALL_DESKTOPS = 0xFFFFFFFF
XA_WM_NAME = 39
# A window manager may move or nudge a window a moment after `move()` (and a compositor may add an offset),
# so configure events this soon after our own request are not reported as the user's doing.
SELF_MOVE_GRACE = 0.3


def keep_below():
    """Whether widgets ask to stay below other windows (_NET_WM_STATE_BELOW).

    Not on GNOME under Wayland: Ubuntu's desktop icons (DING) keep one transparent window over the whole screen
    at the bottom of the *normal* layer, and a window marked "below" sits in a lower layer than that. The
    widgets would still be visible (DING is transparent) but every click and drag would land on DING, so they
    stay normal windows there, like the conky ones of DeskWidgets 1.x/2.x. DESKWIDGETS_KEEP_BELOW=1 or 0 overrides."""
    override = os.environ.get('DESKWIDGETS_KEEP_BELOW')
    if override in ('0', '1'):
        return override == '1'
    wayland = os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland' or bool(os.environ.get('WAYLAND_DISPLAY'))
    return not (wayland and 'GNOME' in os.environ.get('XDG_CURRENT_DESKTOP', '').upper())


def _on_x_error(display, event):
    """Errors arrive asynchronously. A widget window can vanish between two calls (BadWindow,
    BadDrawable): that must not end the process, which Xlib's default handler would."""
    error = ctypes.cast(event, ctypes.POINTER(xl.XErrorEvent)).contents
    if error.error_code not in (xl.BAD_WINDOW, xl.BAD_DRAWABLE):
        log(f'X error {error.error_code} in request {error.request_code}.{error.minor_code}')
    return 0


_ERROR_HANDLER = xl.ERROR_HANDLER(_on_x_error)   # must stay referenced for as long as Xlib may call it


def contains(outer, inner):
    """True when the (x, y, width, height) rectangle `outer` covers `inner` and is bigger than it."""
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return outer != inner and ox <= ix and oy <= iy and ox + ow >= ix + iw and oy + oh >= iy + ih


def parse_xft_dpi(resources):
    """The `Xft.dpi` value from an X resource database string (the root window's RESOURCE_MANAGER), or None."""
    found = re.search(r'^Xft\.dpi:\s*([0-9]+(?:\.[0-9]+)?)\s*$', resources or '', re.MULTILINE)
    return float(found.group(1)) if found else None


class X11Window(Window):
    """One widget window. It is created hidden and mapped by the first `present()`."""

    def __init__(self, backend, title, width, height, x, y, click_through):
        self._b, self._x, self.title = backend, backend._x, title
        self.width, self.height = max(1, int(width)), max(1, int(height))
        self._wanted = self._known = (int(x), int(y))   # asked for / believed to be at: see `_check_moved`
        self._moved_at = time.monotonic()
        self._closed = self._mapped = self._placed = False
        self._buf = self._image = self._image_shape = None
        dpy, xlib = backend.dpy, backend._x
        self._colormap = xlib.XCreateColormap(dpy, backend.root, backend.visual, xl.ALLOC_NONE)
        # no background pixmap: the server must not clear the window before an Expose repaint (flicker)
        attrs = xl.XSetWindowAttributes(background_pixmap=0, border_pixel=0, colormap=self._colormap,
                                        event_mask=EVENT_MASK)
        self.id = xlib.XCreateWindow(
            dpy, backend.root, self._known[0], self._known[1], self.width, self.height, 0, 32, xl.INPUT_OUTPUT,
            backend.visual, xl.CW_BACK_PIXMAP | xl.CW_BORDER_PIXEL | xl.CW_EVENT_MASK | xl.CW_COLORMAP,
            ctypes.byref(attrs))
        self._gc = xlib.XCreateGC(dpy, self.id, 0, None)
        self._set_hints(title)
        if click_through:
            backend.pass_clicks_through(self.id)

    # properties the window manager reads before it maps the window
    def _property(self, name, kind, values):
        a = self._b.atoms
        data = (ctypes.c_ulong * len(values))(*values)
        self._x.XChangeProperty(self._b.dpy, self.id, a[name] if isinstance(name, str) else name, kind, 32, 0,
                                data, len(values))

    def _set_hints(self, title):
        a, xlib, dpy = self._b.atoms, self._x, self._b.dpy
        raw = title.encode('utf-8')
        xlib.XChangeProperty(dpy, self.id, a['_NET_WM_NAME'], a['UTF8_STRING'], 8, 0, raw, len(raw))
        legacy = title.encode('latin-1', 'replace')
        xlib.XChangeProperty(dpy, self.id, XA_WM_NAME, xl.XA_STRING, 8, 0, legacy, len(legacy))
        xlib.XSetClassHint(dpy, self.id, ctypes.byref(xl.XClassHint(b'deskwidget', b'DeskWidget')))
        # input=False: the window manager must never hand a widget the keyboard focus
        xlib.XSetWMHints(dpy, self.id, ctypes.byref(xl.XWMHints(flags=xl.INPUT_HINT, input=0)))
        self._set_size_hints()
        protocols = (ctypes.c_ulong * 1)(a['WM_DELETE_WINDOW'])
        xlib.XSetWMProtocols(dpy, self.id, protocols, 1)
        self._property('_MOTIF_WM_HINTS', a['_MOTIF_WM_HINTS'], [xl.MWM_HINTS_DECORATIONS, 0, 0, 0, 0])
        # NORMAL rather than UTILITY or DESKTOP: window managers keep moving such windows on Super+drag,
        # and `skip_taskbar` already keeps them out of the alt-tab list
        self._property('_NET_WM_WINDOW_TYPE', xl.XA_ATOM, [a['_NET_WM_WINDOW_TYPE_NORMAL']])
        states = ['_NET_WM_STATE_STICKY', '_NET_WM_STATE_SKIP_TASKBAR', '_NET_WM_STATE_SKIP_PAGER']
        if keep_below():
            states.insert(0, '_NET_WM_STATE_BELOW')
        self._property('_NET_WM_STATE', xl.XA_ATOM, [a[n] for n in states])
        self._property('_NET_WM_DESKTOP', xl.XA_CARDINAL, [ALL_DESKTOPS])
        self._property('_NET_WM_USER_TIME', xl.XA_CARDINAL, [0])   # "not user-initiated": do not focus on map
        self._property('_NET_WM_PID', xl.XA_CARDINAL, [os.getpid()])

    def _set_size_hints(self):
        """Position given and size fixed: the window manager places the window and never resizes it.
        Static gravity makes (x, y) mean the client area, whatever frame the manager draws around it."""
        w, h, (x, y) = self.width, self.height, self._wanted
        hints = xl.XSizeHints(
            flags=xl.US_POSITION | xl.P_POSITION | xl.P_SIZE | xl.P_MIN_SIZE | xl.P_MAX_SIZE | xl.P_WIN_GRAVITY,
            x=x, y=y, width=w, height=h, min_width=w, min_height=h, max_width=w, max_height=h,
            win_gravity=xl.STATIC_GRAVITY)
        self._x.XSetWMNormalHints(self._b.dpy, self.id, ctypes.byref(hints))

    # Window
    def present(self, surface):
        if self._closed:
            return
        if surface.get_format() != cairo.FORMAT_ARGB32:
            raise ValueError('present() needs a cairo ARGB32 surface')
        surface.flush()
        w, h, stride = surface.get_width(), surface.get_height(), surface.get_stride()
        if (w, h) != (self.width, self.height):
            self.width, self.height = w, h
            self._set_size_hints()   # first: a manager would clamp the resize to the old fixed size
            self._x.XResizeWindow(self._b.dpy, self.id, w, h)
        self._store(surface, w, h, stride)
        if not self._mapped:
            self._mapped = True
            self._moved_at = time.monotonic()
            self._x.XMapWindow(self._b.dpy, self.id)
        self._paint()
        self._x.XFlush(self._b.dpy)

    def _store(self, surface, w, h, stride):
        """Copies the pixels into a buffer of our own. Cairo may reuse or finish the surface after
        `present()` returns, while an Expose can need the picture again much later."""
        size = stride * h
        if self._image is None or (w, h, stride) != self._image_shape:
            self._free_image()
            self._buf = ctypes.create_string_buffer(size)
            # bytes_per_line = stride: cairo rows are not necessarily width * 4 bytes
            image = self._x.XCreateImage(self._b.dpy, self._b.visual, 32, xl.Z_PIXMAP, 0,
                                         ctypes.addressof(self._buf), w, h, 32, stride)
            image.contents.byte_order = 0 if sys.byteorder == 'little' else 1   # cairo writes native-endian words
            self._image, self._image_shape = image, (w, h, stride)
        view = surface.get_data()
        try:
            try:
                source = (ctypes.c_char * size).from_buffer(view)
            except TypeError:   # a read-only view
                ctypes.memmove(self._buf, bytes(view), size)
            else:
                ctypes.memmove(self._buf, source, size)
                del source
        finally:
            view.release()

    def _paint(self):
        if self._image is not None and self._mapped:
            self._x.XPutImage(self._b.dpy, self.id, self._gc, self._image, 0, 0, 0, 0, self.width, self.height)

    def _free_image(self):
        if self._image is not None:
            self._image.contents.data = None   # the pixels belong to `_buf`, not to Xlib
            self._x.XDestroyImage(self._image)
            self._image = self._buf = None

    def move(self, x, y):
        if self._closed:
            return
        self._wanted = self._known = (int(x), int(y))
        self._moved_at = time.monotonic()
        self._x.XMoveWindow(self._b.dpy, self.id, *self._wanted)
        self._x.XFlush(self._b.dpy)

    def position(self):
        if self._closed:
            return None
        x, y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
        # in root coordinates, so a frame or a reparenting window manager does not matter
        if not self._x.XTranslateCoordinates(self._b.dpy, self.id, self._b.root, 0, 0,
                                             ctypes.byref(x), ctypes.byref(y), ctypes.byref(child)):
            return None
        return (x.value, y.value)

    def close(self):
        if not self._closed:
            self._release(destroy=True)

    @property
    def closed(self):
        return self._closed

    def _release(self, destroy):
        self._closed = True
        self._b.windows.pop(self.id, None)
        dpy, xlib = self._b.dpy, self._x
        if destroy:
            xlib.XDestroyWindow(dpy, self.id)
        xlib.XFreeGC(dpy, self._gc)
        xlib.XFreeColormap(dpy, self._colormap)
        self._free_image()
        xlib.XSync(dpy, 0)   # gone when close() returns, for whoever looks at the X tree next

    # events, called by the backend
    def _on_map(self):
        """Some window managers ignore the requested position when they place a new window: ask again once."""
        if self._placed:
            return
        self._placed = True
        position = self.position()
        if position is not None and position != self._wanted:
            self.move(*self._wanted)

    def _check_moved(self):
        position = self.position()
        if position is None:
            return
        own = time.monotonic() - self._moved_at < SELF_MOVE_GRACE
        changed, self._known = position != self._known, position
        if changed and not own:
            self._b._emit(self, 'on_moved', *position)


class X11Backend(Backend):
    """Opens its own connection to the X server (`display` or $DISPLAY) and runs the loop on it."""

    def __init__(self, display=None):
        self._x = xl.load_xlib()
        self._x.XSetErrorHandler(_ERROR_HANDLER)
        name = display.encode() if isinstance(display, str) else display
        self.dpy = self._x.XOpenDisplay(name)
        if not self.dpy:
            shown = (name or os.environb.get(b'DISPLAY', b'')).decode(errors='replace')
            raise RuntimeError(f'cannot open the X display {shown!r}: is DISPLAY set, and is the X server '
                               '(Xwayland on a Wayland session) running?')
        try:
            self._setup()
        except BaseException:
            self._x.XCloseDisplay(self.dpy)
            self.dpy = None
            raise

    def _setup(self):
        xlib, dpy = self._x, self.dpy
        self.screen = xlib.XDefaultScreen(dpy)
        self.root = xlib.XRootWindow(dpy, self.screen)
        names = (ctypes.c_char_p * len(ATOMS))(*[n.encode() for n in ATOMS])
        values = (ctypes.c_ulong * len(ATOMS))()
        xlib.XInternAtoms(dpy, names, len(ATOMS), 0, values)
        self.atoms = dict(zip(ATOMS, values))
        self.visual = self._argb_visual()
        self.windows = {}
        self._event = xl.XEvent()
        self._motion = None
        self._moved = []
        self._screen_dirty = False
        self._timers = TimerQueue()
        self._soon = collections.deque()
        self._quit = threading.Event()
        self._wake_lock = threading.Lock()
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        os.set_blocking(self._wake_w, False)
        self._shape = None   # libXext is only loaded when a click-through window is asked for
        self._xrandr = self._randr_events = None
        # screen size changes show up as ConfigureNotify on the root; DPI changes as its RESOURCE_MANAGER property
        xlib.XSelectInput(dpy, self.root, xl.STRUCTURE_NOTIFY_MASK | xl.PROPERTY_CHANGE_MASK)
        self._init_randr()
        self._screen_state = self.monitors()

    def _argb_visual(self):
        """A 32-bit TrueColor visual laid out like cairo's ARGB32 (the masks are checked, not assumed)."""
        wanted = xl.XVisualInfo(screen=self.screen, depth=32, klass=xl.TRUE_COLOR, red_mask=0xFF0000,
                                green_mask=0xFF00, blue_mask=0xFF)
        mask = (xl.VISUAL_SCREEN_MASK | xl.VISUAL_DEPTH_MASK | xl.VISUAL_CLASS_MASK | xl.VISUAL_RED_MASK |
                xl.VISUAL_GREEN_MASK | xl.VISUAL_BLUE_MASK)
        count = ctypes.c_int()
        found = self._x.XGetVisualInfo(self.dpy, mask, ctypes.byref(wanted), ctypes.byref(count))
        if not found or not count.value:
            raise RuntimeError('the X server (DISPLAY) offers no 32-bit ARGB visual, so widget windows cannot be '
                               'transparent; a compositing desktop session or Xvfb with "+32" has one')
        visual = found[0].visual
        self._x.XFree(found)
        return visual

    def _init_randr(self):
        """XRandR (optional) tells us about monitor changes and which monitor is the primary one."""
        lib = xl.load_xrandr()
        base, error, major, minor = (ctypes.c_int() for _ in range(4))
        if lib is None or not lib.XRRQueryExtension(self.dpy, ctypes.byref(base), ctypes.byref(error)):
            return
        lib.XRRQueryVersion(self.dpy, ctypes.byref(major), ctypes.byref(minor))
        lib.XRRSelectInput(self.dpy, self.root,
                           xl.RR_SCREEN_CHANGE_MASK | xl.RR_CRTC_CHANGE_MASK | xl.RR_OUTPUT_CHANGE_MASK)
        self._randr_events = (base.value, base.value + 1)   # RRScreenChangeNotify, RRNotify
        if (major.value, minor.value) >= (1, 5):   # XRRGetMonitors
            self._xrandr = lib

    def pass_clicks_through(self, window_id):
        """Empties the window's input region: the pointer then reaches whatever is below. Without the
        XShape extension the window just keeps its clicks."""
        if self._shape is None:
            self._shape = self._probe_shape()
        if self._shape:
            self._shape.XShapeCombineRectangles(self.dpy, window_id, xl.SHAPE_INPUT, 0, 0, None, 0, xl.SHAPE_SET,
                                                xl.SHAPE_UNSORTED)

    def _probe_shape(self):
        lib = xl.load_xext()
        event, error, major, minor = (ctypes.c_int() for _ in range(4))
        if (lib is None or not lib.XShapeQueryExtension(self.dpy, ctypes.byref(event), ctypes.byref(error)) or
                not lib.XShapeQueryVersion(self.dpy, ctypes.byref(major), ctypes.byref(minor)) or
                (major.value, minor.value) < (1, 1)):   # input shapes came with XShape 1.1
            log('this X server cannot make windows click-through: the grid overlay will take the mouse')
            return False
        return lib

    # loop
    def run(self):
        if self.dpy is None:
            raise RuntimeError('this X11 backend is closed')
        poller = select.poll()
        poller.register(self._x.XConnectionNumber(self.dpy), select.POLLIN)
        poller.register(self._wake_r, select.POLLIN)
        try:
            while not self._quit.is_set():
                self._drain()
                self._run_soon()
                self._run_timers()
                if self._quit.is_set():
                    break
                # callbacks talk to the server (position(), ...) and Xlib files the events that arrive meanwhile
                # in its own queue, not on the socket: sleeping now would leave them unhandled until the next wake-up
                if self._x.XPending(self.dpy):
                    continue
                timeout = self._timers.next_timeout()
                for fd, _ in poller.poll(None if timeout is None else math.ceil(timeout * 1000)):
                    if fd == self._wake_r:
                        self._drain_pipe()
        finally:
            self._close_windows()

    def quit(self):
        self._quit.set()
        self._wake()

    def call_soon(self, callback):
        self._soon.append(callback)
        self._wake()

    def call_later(self, seconds, callback):
        return self._timers.add(seconds, callback)

    def close(self):
        """Closes the windows, the connection and the wake-up pipe. Call it after `run()` has returned."""
        if self.dpy is None:
            return
        self._close_windows()
        self._x.XCloseDisplay(self.dpy)
        self.dpy = None
        with self._wake_lock:   # a late call_soon() must not write into a recycled file descriptor
            os.close(self._wake_r)
            os.close(self._wake_w)
            self._wake_r = self._wake_w = None

    def _wake(self):
        with self._wake_lock:
            if self._wake_w is not None:
                try:
                    os.write(self._wake_w, b'x')
                except BlockingIOError:   # the pipe is full: a wake-up is pending anyway
                    pass

    def _drain_pipe(self):
        try:
            while os.read(self._wake_r, 4096):
                pass
        except BlockingIOError:
            pass

    def _run_soon(self):
        for _ in range(len(self._soon)):
            self._guard('call_soon callback', self._soon.popleft())

    def _run_timers(self):
        while True:
            try:
                self._timers.run_due()
                return
            except Exception:   # run_due() had already popped the failing timer: carry on with the rest
                log(f'timer callback failed:\n{traceback.format_exc().rstrip()}')

    @staticmethod
    def _guard(what, function, *args):
        """One bad widget must not take the loop (and every other widget) down with it."""
        try:
            function(*args)
        except Exception:
            log(f'{what} failed:\n{traceback.format_exc().rstrip()}')

    def _emit(self, window, name, *args):
        callback = getattr(window, name)
        if callback is not None and not window.closed:
            self._guard(f'{window.title}: {name}', callback, *args)

    def _close_windows(self):
        for window in list(self.windows.values()):
            window.close()

    # X events
    def _drain(self):
        xlib, dpy, event = self._x, self.dpy, self._event
        while True:
            while xlib.XPending(dpy):
                xlib.XNextEvent(dpy, ctypes.byref(event))
                self._handle(event)
            self._flush_motion()
            if not (self._moved or self._screen_dirty):
                return
            # these ask the server (position, work area), which can queue further events
            moved, self._moved = self._moved, []
            for window in moved:
                if not window.closed:
                    window._check_moved()
            if self._screen_dirty:
                self._screen_dirty = False
                self._check_screen()

    def _handle(self, event):
        kind = event.type
        if kind == xl.MOTION_NOTIFY:
            m = event.xmotion
            window = self.windows.get(m.window)
            if window is not None:
                # a drag produces a burst of motion events; only the last one of a burst matters
                if self._motion is not None and self._motion[0] is not window:
                    self._flush_motion()
                self._motion = (window, m.x, m.y, m.x_root, m.y_root)
            return
        self._flush_motion()   # keep the order of motion and the events around it
        any_event = event.xany
        window = self.windows.get(any_event.window)
        if kind in (xl.BUTTON_PRESS, xl.BUTTON_RELEASE):
            b = event.xbutton
            if window is not None and b.button not in SCROLL_BUTTONS:
                self._emit(window, 'on_button', b.button, kind == xl.BUTTON_PRESS, b.x, b.y, b.x_root, b.y_root)
        elif kind == xl.EXPOSE:
            if window is not None and event.xexpose.count == 0:
                window._paint()
        elif kind == xl.CONFIGURE_NOTIFY:
            if window is not None:
                if window not in self._moved:
                    self._moved.append(window)
            elif any_event.window == self.root:
                self._screen_dirty = True
        elif kind == xl.MAP_NOTIFY:
            if window is not None:
                window._on_map()
        elif kind == xl.DESTROY_NOTIFY:
            if window is not None:
                window._release(destroy=False)
                self._emit_closed(window)
        elif kind == xl.CLIENT_MESSAGE:
            message = event.xclient
            if (window is not None and message.message_type == self.atoms['WM_PROTOCOLS'] and
                    message.data[0] == self.atoms['WM_DELETE_WINDOW']):
                window.close()
                self._emit_closed(window)
        elif kind == xl.PROPERTY_NOTIFY:
            if any_event.window == self.root and event.xproperty.atom == self.atoms['RESOURCE_MANAGER']:
                self._screen_dirty = True
        elif self._randr_events and kind in self._randr_events:
            self._screen_dirty = True

    def _flush_motion(self):
        if self._motion is not None:
            (window, *args), self._motion = self._motion, None
            self._emit(window, 'on_motion', *args)

    def _emit_closed(self, window):
        """`_emit` skips closed windows, but this is the one event a closed window still gets."""
        if window.on_closed is not None:
            self._guard(f'{window.title}: on_closed', window.on_closed)

    def _check_screen(self):
        state = self.monitors()
        if state != self._screen_state:
            self._screen_state = state
            if self.on_screen_changed is not None:
                self._guard('on_screen_changed', self.on_screen_changed)

    # screen
    def work_area(self):
        """The primary monitor, or the whole X screen when XRandR cannot say which that is. The service
        applies its own margins from the settings, so panels and docks are deliberately not subtracted."""
        return self.monitors()[0].rect

    def monitors(self):
        """The XRandR monitors, the primary one first (the one at the origin when none is marked, else the
        first). Without XRandR 1.5 the whole X screen is the one monitor."""
        found = self._randr_monitors()
        if not found:
            width, height = self._screen_size()
            return [Monitor('screen', 0, 0, width, height, True, self.dpi_scale())]
        if not any(primary for _name, primary, _geometry in found):
            origin = next((i for i, (_n, _p, g) in enumerate(found) if g[:2] == (0, 0)), 0)
            found[origin] = (found[origin][0], True, found[origin][2])
        scale = self.dpi_scale()
        return ordered(Monitor(name, *geometry, primary, scale) for name, primary, geometry in found)

    def _screen_size(self):
        root, x, y, w, h, border, depth = (ctypes.c_ulong(), ctypes.c_int(), ctypes.c_int(), ctypes.c_uint(),
                                           ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint())
        self._x.XGetGeometry(self.dpy, self.root, *map(ctypes.byref, (root, x, y, w, h, border, depth)))
        return (w.value, h.value)

    def _randr_monitors(self):
        """[(name, primary, (x, y, width, height))] from XRandR, [] when it cannot say."""
        if self._xrandr is None:
            return []
        count = ctypes.c_int()
        monitors = self._xrandr.XRRGetMonitors(self.dpy, self.root, 1, ctypes.byref(count))
        if not monitors:
            return []
        found, names = [], set()
        try:
            for m in monitors[:count.value]:
                if m.width <= 0 or m.height <= 0:
                    continue
                name = self._atom_name(m.name) or f'monitor{len(found) + 1}'
                while name in names:   # ids must be unique to be settings
                    name += '+'
                names.add(name)
                found.append((name, bool(m.primary), (m.x, m.y, m.width, m.height)))
        finally:
            self._xrandr.XRRFreeMonitors(monitors)
        # a monitor that covers other monitors is a span over them (a user-defined "whole screen"), not a display
        return [m for m in found if not any(o is not m and contains(m[2], o[2]) for o in found)]

    def _atom_name(self, atom):
        pointer = self._x.XGetAtomName(self.dpy, atom) if atom else None
        if not pointer:
            return ''
        try:
            return ctypes.string_at(pointer).decode('utf-8', 'replace')
        finally:
            self._x.XFree(pointer)

    def dpi_scale(self):
        """`Xft.dpi` / 96 when the desktop sets it (1.0 below 96 dpi, at most 3.0). X reports a physical
        screen size that is often wrong, so it is not used."""
        dpi = parse_xft_dpi(self._resource_manager())
        return 1.0 if dpi is None or dpi < 96 else min(3.0, dpi / 96)

    def _resource_manager(self):
        kind, fmt, length, left, data = (ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong(),
                                         ctypes.c_void_p())
        status = self._x.XGetWindowProperty(
            self.dpy, self.root, self.atoms['RESOURCE_MANAGER'], 0, 1 << 16, 0, xl.XA_STRING, *map(
                ctypes.byref, (kind, fmt, length, left, data)))
        if status != 0 or not data.value:
            return None
        try:
            return ctypes.string_at(data.value, length.value).decode('utf-8', 'replace') if fmt.value == 8 else None
        finally:
            self._x.XFree(data)

    # windows
    def create_window(self, title, width, height, x, y, click_through=False):
        window = X11Window(self, title, width, height, x, y, click_through)
        self.windows[window.id] = window
        self._x.XFlush(self.dpy)
        return window
