"""The X11 backend against a private Xvfb.

Xvfb is started with `-screen 0 1280x800x24+32`. The backend needs a depth-32 TrueColor visual for
ARGB windows; current Xvfb offers one next to the 24-bit default on a plain `x24` screen as well, `+32`
asks for 32-bit pixmaps too and is there for older versions. An `x8` screen has no such visual, which
one test uses to check the error. There is no window manager and no compositor, so transparency itself
cannot be seen; the tests read the pixels, alpha byte included, back from the server. Input is injected with XTest through a second connection, and window-manager behaviour is
imitated by a small reparenting `FakeWM`.
"""
import ctypes
import ctypes.util
import os
import queue
import select
import shutil
import subprocess
import threading
import time
import unittest
from ctypes import POINTER as P, c_char_p, c_int as i, c_long as l, c_uint as ui, c_ulong as ul, c_void_p as vp
from unittest import mock

from deskwidgets.platform import x11_xlib as xl

try:
    import cairo
    from deskwidgets.platform import x11
except ImportError:   # no pycairo: the whole module is skipped
    cairo = x11 = None

MISSING = ('Xvfb is not installed' if not shutil.which('Xvfb') else
           'pycairo is not installed' if cairo is None else
           'libXtst is not installed' if not ctypes.util.find_library('Xtst') else '')
SERVER = None

EXTRA = {   # what the tests need on top of the backend's own bindings
    'XGetImage': (P(xl.XImage), [vp, ul, i, i, ui, ui, ul, i]),
    'XGetWindowAttributes': (i, [vp, ul, vp]),
    'XQueryTree': (i, [vp, ul, P(ul), P(ul), P(P(ul)), P(ui)]),
    'XInternAtom': (ul, [vp, c_char_p, i]),
    'XSendEvent': (i, [vp, ul, i, l, vp]),
    'XSetForeground': (i, [vp, vp, ul]),
    'XFillRectangle': (i, [vp, ul, vp, i, i, ui, ui]),
    'XClearArea': (i, [vp, ul, i, i, ui, ui, i]),
    'XDeleteProperty': (i, [vp, ul, ul]),
    'XCreateSimpleWindow': (ul, [vp, ul, i, i, ui, ui, ui, ul, ul]),
    'XReparentWindow': (i, [vp, ul, ul, i, i]),
    'XMoveResizeWindow': (i, [vp, ul, i, i, ui, ui]),
}


class XWindowAttributes(ctypes.Structure):
    _fields_ = [('x', i), ('y', i), ('width', i), ('height', i), ('border_width', i), ('depth', i), ('visual', vp),
                ('root', ul), ('klass', i), ('bit_gravity', i), ('win_gravity', i), ('backing_store', i),
                ('backing_planes', ul), ('backing_pixel', ul), ('save_under', i), ('colormap', ul),
                ('map_installed', i), ('map_state', i), ('all_event_masks', l), ('your_event_mask', l),
                ('do_not_propagate_mask', l), ('override_redirect', i), ('screen', vp)]


def declare(lib, table):
    for name, (restype, argtypes) in table.items():
        function = getattr(lib, name)
        function.restype, function.argtypes = restype, argtypes
    return lib


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class XServer:
    """A throwaway Xvfb on a display number it picks itself (`-displayfd`), so parallel runs do not collide."""

    def __init__(self, screen='1280x800x24+32'):
        read, write = os.pipe()
        self.proc = subprocess.Popen(['Xvfb', '-displayfd', str(write), '-screen', '0', screen, '-nolisten', 'tcp',
                                      '-noreset'], pass_fds=(write,), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(write)
        try:
            number = os.read(read, 32).decode().strip() if select.select([read], [], [], 30)[0] else ''
        finally:
            os.close(read)
        if not number:
            self.stop()
            raise unittest.SkipTest(f'Xvfb {screen} did not start')
        self.display = ':' + number

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


def setUpModule():
    global SERVER
    if not MISSING:
        ctypes.CDLL('libX11.so.6').XInitThreads()   # the tests use one connection per thread
        SERVER = XServer()


def tearDownModule():
    if SERVER is not None:
        SERVER.stop()


class XClient:
    """Another client of the same X server: injects input and inspects windows like any other program would."""

    def __init__(self, display):
        self.x = declare(xl.load_xlib(), EXTRA)
        self.tst = ctypes.CDLL('libXtst.so.6')
        self.tst.XTestFakeMotionEvent.argtypes = [vp, i, i, i, ul]
        self.tst.XTestFakeButtonEvent.argtypes = [vp, ui, i, ul]
        self.ext = ctypes.CDLL('libXext.so.6')
        self.ext.XShapeGetRectangles.restype = vp
        self.ext.XShapeGetRectangles.argtypes = [vp, ul, i, P(i), P(i)]
        self.dpy = self.x.XOpenDisplay(display.encode())
        assert self.dpy, f'cannot open {display}'
        self.root = self.x.XRootWindow(self.dpy, 0)

    def close(self):
        self.x.XCloseDisplay(self.dpy)

    def sync(self):
        self.x.XSync(self.dpy, 0)

    def atom(self, name):
        return self.x.XInternAtom(self.dpy, name.encode(), 0)

    # input
    def motion(self, x, y):
        self.tst.XTestFakeMotionEvent(self.dpy, 0, x, y, 0)
        self.x.XFlush(self.dpy)

    def button(self, button, press):
        self.tst.XTestFakeButtonEvent(self.dpy, button, int(press), 0)
        self.x.XFlush(self.dpy)

    def click(self, button):
        self.button(button, True)
        self.button(button, False)

    # windows
    def children(self):
        root, parent, kids, count = ul(), ul(), P(ul)(), ui()
        self.x.XQueryTree(self.dpy, self.root, *map(ctypes.byref, (root, parent, kids, count)))
        found = kids[:count.value] if kids else []
        if kids:
            self.x.XFree(kids)
        return found

    def geometry(self, window):
        root, x, y, w, h, border, depth = ul(), i(), i(), ui(), ui(), ui(), ui()
        self.x.XGetGeometry(self.dpy, window, *map(ctypes.byref, (root, x, y, w, h, border, depth)))
        return {'x': x.value, 'y': y.value, 'width': w.value, 'height': h.value, 'depth': depth.value}

    def attributes(self, window):
        attrs = XWindowAttributes()
        self.x.XGetWindowAttributes(self.dpy, window, ctypes.byref(attrs))
        return attrs

    def rows(self, window, w, h):
        """The window's pixels, one `bytes` per row, as the server stores them (BGRA for a 32-bit window)."""
        image = self.x.XGetImage(self.dpy, window, 0, 0, w, h, ul(-1).value, xl.Z_PIXMAP)
        if not image:
            return None
        try:
            im = image.contents
            return [ctypes.string_at(im.data + row * im.bytes_per_line, w * 4) for row in range(h)]
        finally:
            self.x.XDestroyImage(image)

    def property(self, window, name):
        kind, fmt, length, left, data = ul(), i(), ul(), ul(), vp()
        self.x.XGetWindowProperty(self.dpy, window, self.atom(name), 0, 1024, 0, 0,
                                  *map(ctypes.byref, (kind, fmt, length, left, data)))
        if not data.value:
            return None
        try:
            if fmt.value == 8:
                return ctypes.string_at(data.value, length.value)
            return [v & 0xFFFFFFFF for v in (ctypes.c_ulong * length.value).from_address(data.value)]   # CARD32s
        finally:
            self.x.XFree(data)

    def set_resources(self, text):
        raw = text.encode()
        self.x.XChangeProperty(self.dpy, self.root, self.atom('RESOURCE_MANAGER'), xl.XA_STRING, 8, 0, raw, len(raw))
        self.sync()

    def clear_resources(self):
        self.x.XDeleteProperty(self.dpy, self.root, self.atom('RESOURCE_MANAGER'))
        self.sync()

    def input_rectangles(self, window):
        """How many rectangles the window's input shape has (0 = nothing under the pointer reaches it)."""
        count, ordering = i(), i()
        rects = self.ext.XShapeGetRectangles(self.dpy, window, xl.SHAPE_INPUT, ctypes.byref(count),
                                             ctypes.byref(ordering))
        if rects:
            self.x.XFree(rects)
        return count.value

    def delete_message(self, window):
        event = xl.XEvent()
        message = event.xclient
        message.type, message.window, message.format = xl.CLIENT_MESSAGE, window, 32
        message.message_type = self.atom('WM_PROTOCOLS')
        message.data[0], message.data[1] = self.atom('WM_DELETE_WINDOW'), 0
        self.x.XSendEvent(self.dpy, window, 0, 0, ctypes.byref(event))
        self.sync()

    def damage(self, window, w, h):
        """Paints over the window and asks it to repaint, as an uncovered window would have to."""
        gc = self.x.XCreateGC(self.dpy, window, 0, None)
        self.x.XSetForeground(self.dpy, gc, 0xFF123456)
        self.x.XFillRectangle(self.dpy, window, gc, 0, 0, w, h)
        self.x.XFreeGC(self.dpy, gc)
        self.x.XClearArea(self.dpy, window, 0, 0, 0, 0, 1)
        self.sync()


class FakeWM:
    """A tiny reparenting window manager: every new window gets a frame that puts the client area at
    (BORDER_X, BORDER_Y) inside it, as a title bar and borders would. With `place_at` it also ignores the
    position the program asked for. It tells clients where they are with synthetic ConfigureNotify events,
    the way ICCCM 4.1.5 says a window manager must."""
    BORDER_X, BORDER_Y = 6, 24
    MAP_REQUEST, CONFIGURE_REQUEST = 20, 23
    SUBSTRUCTURE_NOTIFY, SUBSTRUCTURE_REDIRECT = 1 << 19, 1 << 20

    class MapRequest(ctypes.Structure):
        _fields_ = xl._HEAD + [('client', ul)]   # the head's `window` is the parent

    class ConfigureRequest(ctypes.Structure):
        _fields_ = xl._HEAD + [('client', ul), ('x', i), ('y', i), ('width', i), ('height', i), ('border', i),
                               ('above', ul), ('detail', i), ('mask', ul)]

    def __init__(self, display, place_at=None):
        self.c = XClient(display)
        self.place_at = place_at
        self.frames = {}
        self.commands = queue.Queue()
        self.stopping = threading.Event()
        self.c.x.XSelectInput(self.c.dpy, self.c.root, self.SUBSTRUCTURE_REDIRECT | self.SUBSTRUCTURE_NOTIFY)
        self.c.sync()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        self.thread.join(5)
        self.c.close()

    def move(self, client, x, y):
        """What a Super+drag does: the frame moves, the client is told its new root position."""
        done = threading.Event()
        self.commands.put((client, x, y, done))
        assert done.wait(5), 'the fake window manager did not answer'

    def loop(self):
        x, dpy, event = self.c.x, self.c.dpy, xl.XEvent()
        poller = select.poll()
        poller.register(x.XConnectionNumber(dpy), select.POLLIN)
        while not self.stopping.is_set():
            while x.XPending(dpy):
                x.XNextEvent(dpy, ctypes.byref(event))
                if event.type == self.MAP_REQUEST:
                    self.frame(ctypes.cast(ctypes.byref(event), P(self.MapRequest)).contents)
                elif event.type == self.CONFIGURE_REQUEST:
                    self.configure(ctypes.cast(ctypes.byref(event), P(self.ConfigureRequest)).contents)
            while not self.commands.empty():
                client, px, py, done = self.commands.get()
                self.place(client, px, py)
                done.set()
            poller.poll(10)

    def frame(self, request):
        client = request.client
        x, dpy = self.c.x, self.c.dpy
        geometry = self.c.geometry(client)
        px, py = self.place_at or (geometry['x'], geometry['y'])
        frame = x.XCreateSimpleWindow(dpy, self.c.root, px - self.BORDER_X, py - self.BORDER_Y,
                                      geometry['width'] + 2 * self.BORDER_X, geometry['height'] + self.BORDER_Y + 6,
                                      0, 0, 0)
        self.frames[client] = frame
        x.XSelectInput(dpy, frame, self.SUBSTRUCTURE_REDIRECT)   # the client's later moves come as requests
        x.XReparentWindow(dpy, client, frame, self.BORDER_X, self.BORDER_Y)
        x.XMapWindow(dpy, client)
        x.XMapWindow(dpy, frame)
        self.tell(client, px, py)

    def configure(self, request):
        if request.client in self.frames:
            if request.mask & 3:   # CWX | CWY
                self.place(request.client, request.x, request.y)
        else:
            self.c.x.XMoveWindow(self.c.dpy, request.client, request.x, request.y)

    def place(self, client, px, py):
        self.c.x.XMoveWindow(self.c.dpy, self.frames[client], px - self.BORDER_X, py - self.BORDER_Y)
        self.tell(client, px, py)

    def tell(self, client, px, py):
        size = self.c.geometry(client)
        event = xl.XEvent()
        configure = event.xconfigure
        configure.type, configure.window, configure.configured = xl.CONFIGURE_NOTIFY, client, client
        configure.x, configure.y, configure.width, configure.height = px, py, size['width'], size['height']
        self.c.x.XSendEvent(self.c.dpy, client, 0, xl.STRUCTURE_NOTIFY_MASK, ctypes.byref(event))
        self.c.sync()


class Loop:
    """Runs a backend's loop on its own thread. `call()` runs something on that thread and returns the result,
    which is also how every window method is used while the loop runs (Xlib is not thread-safe)."""

    def __init__(self, backend):
        self.backend = backend
        self.thread = threading.Thread(target=backend.run, daemon=True)
        self.thread.start()

    def call(self, function, *args, timeout=5):
        done, box = threading.Event(), {}

        def job():
            try:
                box['value'] = function(*args)
            except BaseException as e:
                box['error'] = e
            done.set()
        self.backend.call_soon(job)
        if not done.wait(timeout):
            raise AssertionError('the loop did not run the callback')
        if 'error' in box:
            raise box['error']
        return box.get('value')

    def stop(self):
        self.backend.quit()
        self.thread.join(5)
        return not self.thread.is_alive()


def drawing(w=64, h=48, stride=None):
    """A surface with four quadrants: opaque red, half-transparent green (premultiplied), opaque blue, clear."""
    if stride:
        surface = cairo.ImageSurface.create_for_data(bytearray(stride * h), cairo.FORMAT_ARGB32, w, h, stride)
    else:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surface)
    for (qx, qy), color in (((0, 0), (1, 0, 0, 1)), ((1, 0), (0, 1, 0, 0.5)), ((0, 1), (0, 0, 1, 1))):
        cr.set_source_rgba(*color)
        cr.rectangle(qx * w // 2, qy * h // 2, w // 2, h // 2)
        cr.fill()
    surface.flush()
    return surface


def surface_rows(surface):
    data, stride = bytes(surface.get_data()), surface.get_stride()
    return [data[row * stride:row * stride + surface.get_width() * 4] for row in range(surface.get_height())]


class Recorder:
    def __init__(self, window):
        self.events = []
        window.on_button = lambda *a: self.events.append(('button',) + a)
        window.on_motion = lambda *a: self.events.append(('motion',) + a)
        window.on_moved = lambda *a: self.events.append(('moved',) + a)
        window.on_closed = lambda: self.events.append(('closed',))

    def kinds(self, kind):
        return [e[1:] for e in list(self.events) if e[0] == kind]


@unittest.skipIf(MISSING, MISSING)
class X11TestCase(unittest.TestCase):
    def setUp(self):
        self.backend = x11.X11Backend(display=SERVER.display)
        self.client = XClient(SERVER.display)
        self.loop = None
        self.addCleanup(self.shutdown)

    def shutdown(self):
        if self.loop is not None:
            self.assertTrue(self.loop.stop(), 'run() did not return after quit()')
        self.backend.close()
        self.client.close()

    def start_loop(self):
        self.loop = Loop(self.backend)

    def on_loop(self, function, *args):
        return self.loop.call(function, *args) if self.loop else function(*args)

    def window(self, w=64, h=48, x=100, y=100, **kwargs):
        window = self.on_loop(lambda: self.backend.create_window('deskwidget:test', w, h, x, y, **kwargs))
        self.recorder = window.recorder = Recorder(window)
        return window

    def present(self, window, surface):
        self.on_loop(window.present, surface)

    def pointer_to(self, x, y):
        """Moves the pointer away first, so entering the target is a real movement for the server."""
        self.client.motion(0, 0)
        self.client.motion(x, y)

    def shows(self, window, surface):
        expected = surface_rows(surface)
        wait_until(lambda: self.client.rows(window.id, surface.get_width(), surface.get_height()) == expected)
        self.assertEqual(self.client.rows(window.id, surface.get_width(), surface.get_height()), expected)


class PresentTests(X11TestCase):
    def test_window_is_hidden_until_the_first_present(self):
        window = self.window()
        self.assertIn(window.id, self.client.children())
        attrs = self.client.attributes(window.id)
        self.assertEqual((attrs.depth, attrs.map_state, attrs.override_redirect), (32, 0, 0))   # IsUnmapped
        self.present(window, drawing())
        self.assertEqual(self.client.attributes(window.id).map_state, 2)   # IsViewable

    def test_pixels_arrive_unshifted_with_their_alpha(self):
        window = self.window()
        surface = drawing()
        self.present(window, surface)
        self.shows(window, surface)
        rows = self.client.rows(window.id, 64, 48)
        pixel = lambda x, y: int.from_bytes(rows[y][x * 4:x * 4 + 4], 'little')
        self.assertEqual(pixel(5, 5), 0xFFFF0000)    # opaque red
        self.assertEqual(pixel(50, 5), 0x80008000)   # premultiplied half green: alpha and colour kept
        self.assertEqual(pixel(5, 40), 0xFF0000FF)   # opaque blue
        self.assertEqual(pixel(50, 40), 0x00000000)  # transparent

    def test_rows_wider_than_the_image_are_read_with_their_stride(self):
        window = self.window(w=30, h=20)
        surface = drawing(30, 20, stride=30 * 4 + 12)
        self.assertEqual(surface.get_stride(), 132)
        self.present(window, surface)
        self.shows(window, surface)

    def test_a_different_size_resizes_the_window(self):
        window = self.window()
        self.present(window, drawing(64, 48))
        bigger = drawing(100, 90)
        self.present(window, bigger)
        self.assertEqual((window.width, window.height), (100, 90))
        self.assertTrue(wait_until(lambda: self.client.geometry(window.id)['width'] == 100))
        self.assertEqual(self.client.geometry(window.id)['height'], 90)
        self.shows(window, bigger)
        self.assertEqual(self.client.property(window.id, 'WM_NORMAL_HINTS')[7:9], [100, 90])   # max size follows

    def test_the_picture_is_repainted_on_expose(self):
        window = self.window()
        surface = drawing()
        self.start_loop()
        self.present(window, surface)
        self.shows(window, surface)
        self.client.damage(window.id, 64, 48)
        self.shows(window, surface)

    def test_later_frames_replace_the_picture_and_the_surface_may_be_reused(self):
        window = self.window()
        surface = drawing()
        self.present(window, surface)
        cr = cairo.Context(surface)
        cr.set_source_rgba(1, 1, 0, 1)
        cr.paint()
        self.present(window, surface)
        self.shows(window, surface)
        surface.finish()   # the backend keeps its own copy for later repaints
        self.start_loop()
        self.client.damage(window.id, 64, 48)
        self.assertTrue(wait_until(lambda: self.client.rows(window.id, 64, 48)[3][:4] == b'\x00\xff\xff\xff'))

    def test_only_argb32_surfaces_are_accepted(self):
        window = self.window()
        with self.assertRaises(ValueError):
            self.present(window, cairo.ImageSurface(cairo.FORMAT_RGB24, 10, 10))

    def test_no_below_state_when_it_would_hide_the_widgets_from_the_pointer(self):
        with mock.patch.dict(os.environ, {'DESKWIDGETS_KEEP_BELOW': '0'}):
            window = self.window(w=80, h=60, x=30, y=40)
        c, atom = self.client, self.client.atom
        self.assertEqual(c.property(window.id, '_NET_WM_STATE'),
                         [atom(n) for n in ('_NET_WM_STATE_STICKY', '_NET_WM_STATE_SKIP_TASKBAR',
                                            '_NET_WM_STATE_SKIP_PAGER')])

    def test_window_hints(self):
        with mock.patch.dict(os.environ, {'DESKWIDGETS_KEEP_BELOW': '1'}):
            window = self.window(w=80, h=60, x=30, y=40)
        c, atom = self.client, self.client.atom
        self.assertEqual(c.property(window.id, '_NET_WM_STATE'),
                         [atom(n) for n in ('_NET_WM_STATE_BELOW', '_NET_WM_STATE_STICKY',
                                            '_NET_WM_STATE_SKIP_TASKBAR', '_NET_WM_STATE_SKIP_PAGER')])
        self.assertEqual(c.property(window.id, '_NET_WM_DESKTOP'), [0xFFFFFFFF])
        self.assertEqual(c.property(window.id, '_NET_WM_WINDOW_TYPE'), [atom('_NET_WM_WINDOW_TYPE_NORMAL')])
        self.assertEqual(c.property(window.id, '_MOTIF_WM_HINTS')[:3], [2, 0, 0])   # decorations: none
        self.assertEqual(c.property(window.id, 'WM_CLASS'), b'deskwidget\0DeskWidget\0')
        self.assertEqual(c.property(window.id, 'WM_NAME'), b'deskwidget:test')
        self.assertEqual(c.property(window.id, '_NET_WM_NAME'), b'deskwidget:test')
        self.assertEqual(c.property(window.id, 'WM_PROTOCOLS'), [atom('WM_DELETE_WINDOW')])
        wm_hints = c.property(window.id, 'WM_HINTS')
        self.assertEqual((wm_hints[0] & xl.INPUT_HINT, wm_hints[1]), (xl.INPUT_HINT, 0))   # never take the focus
        size = c.property(window.id, 'WM_NORMAL_HINTS')
        flags = xl.US_POSITION | xl.P_POSITION | xl.P_MIN_SIZE | xl.P_MAX_SIZE
        self.assertEqual(size[0] & flags, flags)
        self.assertEqual(size[1:3], [30, 40])
        self.assertEqual(size[5:9], [80, 60, 80, 60])   # min = max = the window size
        self.assertEqual(c.geometry(window.id)['depth'], 32)

    def test_non_ascii_titles(self):
        window = self.on_loop(lambda: self.backend.create_window('deskwidget:Погода', 10, 10, 0, 0))
        self.assertEqual(self.client.property(window.id, '_NET_WM_NAME').decode(), 'deskwidget:Погода')


class PlacementTests(X11TestCase):
    def test_move_and_position_round_trip(self):
        window = self.window(x=100, y=120)
        self.assertEqual(window.position(), (100, 120))
        self.present(window, drawing())
        self.assertEqual(window.position(), (100, 120))
        window.move(300, 210)
        self.assertEqual(window.position(), (300, 210))
        self.assertEqual({k: self.client.geometry(window.id)[k] for k in 'xy'}, {'x': 300, 'y': 210})
        window.move(-20, -10)   # partly off screen is allowed
        self.assertEqual(window.position(), (-20, -10))

    def test_own_moves_are_not_reported_but_other_clients_moves_are(self):
        window = self.window(x=100, y=100)
        self.present(window, drawing())
        self.start_loop()
        time.sleep(x11.SELF_MOVE_GRACE + 0.1)
        self.on_loop(window.move, 200, 150)
        self.on_loop(window.move, 220, 170)
        time.sleep(x11.SELF_MOVE_GRACE + 0.1)
        self.assertEqual(self.recorder.kinds('moved'), [])
        self.client.x.XMoveWindow(self.client.dpy, window.id, 400, 300)   # like a window manager's Super+drag
        self.client.sync()
        self.assertTrue(wait_until(lambda: self.recorder.kinds('moved')))
        self.assertEqual(self.recorder.kinds('moved'), [(400, 300)])
        self.assertEqual(self.on_loop(window.position), (400, 300))

    def test_a_burst_of_outside_moves_ends_with_the_final_position(self):
        window = self.window(x=100, y=100)
        self.present(window, drawing())
        self.start_loop()
        time.sleep(x11.SELF_MOVE_GRACE + 0.1)
        for step in range(1, 21):
            self.client.x.XMoveWindow(self.client.dpy, window.id, 100 + step * 10, 100 + step * 5)
        self.client.sync()
        self.assertTrue(wait_until(lambda: self.recorder.kinds('moved') and self.recorder.kinds('moved')[-1] == (300, 200)))

    def test_closed_window_has_no_position(self):
        window = self.window()
        window.close()
        self.assertIsNone(window.position())
        window.move(5, 5)
        self.present(window, drawing())   # both are ignored


class FakeWMTests(X11TestCase):
    def wm(self, **kwargs):
        wm = FakeWM(SERVER.display, **kwargs)
        self.addCleanup(wm.stop)
        return wm

    def test_a_manager_that_ignores_the_requested_position_is_asked_again_once_mapped(self):
        self.wm(place_at=(0, 0))
        window = self.window(x=240, y=180)
        self.start_loop()
        self.present(window, drawing())
        self.assertTrue(wait_until(lambda: self.on_loop(window.position) == (240, 180)))

    def test_position_is_in_root_coordinates_behind_a_frame(self):
        self.wm()
        window = self.window(x=240, y=180)
        self.start_loop()
        self.present(window, drawing())
        self.assertTrue(wait_until(lambda: self.on_loop(window.position) == (240, 180)))
        self.on_loop(window.move, 300, 260)
        self.assertTrue(wait_until(lambda: self.on_loop(window.position) == (300, 260)))

    def test_moves_by_the_manager_are_reported_in_root_coordinates(self):
        wm = self.wm()
        window = self.window(x=240, y=180)
        self.start_loop()
        self.present(window, drawing())
        self.assertTrue(wait_until(lambda: self.on_loop(window.position) == (240, 180)))
        time.sleep(x11.SELF_MOVE_GRACE + 0.1)
        self.assertEqual(self.recorder.kinds('moved'), [])
        wm.move(window.id, 500, 400)   # Super+drag
        self.assertTrue(wait_until(lambda: self.recorder.kinds('moved')))
        self.assertEqual(self.recorder.kinds('moved'), [(500, 400)])
        self.on_loop(window.move, 520, 410)   # our own move through the manager is not reported
        time.sleep(0.3)
        self.assertEqual(self.recorder.kinds('moved'), [(500, 400)])
        self.assertEqual(self.on_loop(window.position), (520, 410))


@unittest.skipIf(x11 is None, 'pycairo is missing')
class LayerTests(unittest.TestCase):
    def test_widgets_are_not_forced_below_on_gnome_wayland(self):
        patch = mock.patch
        gnome_wayland = {'XDG_SESSION_TYPE': 'wayland', 'XDG_CURRENT_DESKTOP': 'ubuntu:GNOME'}
        with patch.dict(os.environ, gnome_wayland, clear=True):
            self.assertFalse(x11.keep_below())
        with patch.dict(os.environ, {**gnome_wayland, 'DESKWIDGETS_KEEP_BELOW': '1'}, clear=True):
            self.assertTrue(x11.keep_below())
        for env in ({'XDG_SESSION_TYPE': 'x11', 'XDG_CURRENT_DESKTOP': 'ubuntu:GNOME'},
                    {'XDG_SESSION_TYPE': 'wayland', 'XDG_CURRENT_DESKTOP': 'KDE'}, {}):
            with patch.dict(os.environ, env, clear=True):
                self.assertTrue(x11.keep_below(), env)


class MouseTests(X11TestCase):
    def test_motion_and_buttons_inside_the_window(self):
        window = self.window(w=200, h=150, x=100, y=100)
        self.present(window, drawing(200, 150))
        self.start_loop()
        self.pointer_to(150, 130)
        self.assertTrue(wait_until(lambda: self.recorder.kinds('motion')))
        self.assertEqual(self.recorder.kinds('motion')[-1], (50, 30, 150, 130))
        self.client.button(1, True)
        self.client.button(1, False)
        self.client.click(3)
        self.assertTrue(wait_until(lambda: len(self.recorder.kinds('button')) == 4))
        self.assertEqual(self.recorder.kinds('button'), [(1, True, 50, 30, 150, 130), (1, False, 50, 30, 150, 130),
                                                         (3, True, 50, 30, 150, 130), (3, False, 50, 30, 150, 130)])

    def test_scroll_wheel_is_ignored(self):
        window = self.window(w=200, h=150, x=100, y=100)
        self.present(window, drawing(200, 150))
        self.start_loop()
        self.pointer_to(150, 130)
        for wheel in (4, 5, 6, 7):
            self.client.click(wheel)
        self.client.click(2)   # a marker: events arrive in order
        self.assertTrue(wait_until(lambda: len(self.recorder.kinds('button')) >= 2))
        time.sleep(0.1)
        self.assertEqual([b[0] for b in self.recorder.kinds('button')], [2, 2])

    def test_motion_with_a_button_held_outside_the_window_and_release_there(self):
        window = self.window(w=200, h=150, x=100, y=100)
        self.present(window, drawing(200, 150))
        self.start_loop()
        self.pointer_to(150, 130)
        self.client.button(1, True)
        self.client.motion(600, 500)
        self.assertTrue(wait_until(lambda: self.recorder.kinds('motion') and self.recorder.kinds('motion')[-1][2:] == (600, 500)))
        self.assertEqual(self.recorder.kinds('motion')[-1], (500, 400, 600, 500))   # x, y are relative to the window
        self.client.motion(0, 700)
        self.client.button(1, False)
        self.assertTrue(wait_until(lambda: len(self.recorder.kinds('button')) == 2))
        self.assertEqual(self.recorder.kinds('button')[-1], (1, False, -100, 600, 0, 700))

    def test_a_dragging_service_can_move_the_window_from_on_motion(self):
        window = self.window(w=100, h=80, x=100, y=100)
        self.present(window, drawing(100, 80))
        self.start_loop()
        press = {}

        def on_button(button, pressed, x, y, rx, ry):
            if pressed:
                press.update(root=(rx, ry), win=window.position())

        def on_motion(x, y, rx, ry):
            if press:
                window.move(press['win'][0] + rx - press['root'][0], press['win'][1] + ry - press['root'][1])
        window.on_button, window.on_motion = on_button, on_motion
        window.on_moved = lambda x, y: self.recorder.events.append(('moved', x, y))
        self.pointer_to(120, 120)
        self.client.button(1, True)
        for step in range(1, 11):
            self.client.motion(120 + step * 30, 120 + step * 10)
        self.assertTrue(wait_until(lambda: self.on_loop(window.position) == (400, 200)))
        self.client.button(1, False)
        time.sleep(0.4)
        self.assertEqual(self.recorder.kinds('moved'), [])   # the service's own moves are not "moved by the user"

    def test_click_through_windows_do_not_see_the_mouse(self):
        overlay = self.window(w=200, h=200, x=400, y=300, click_through=True)
        other = self.window(w=100, h=100, x=700, y=100)
        overlay_events, other_events = overlay.recorder, other.recorder
        self.present(overlay, drawing(200, 200))
        self.present(other, drawing(100, 100))
        self.assertEqual(self.client.input_rectangles(overlay.id), 0)
        self.assertEqual(self.client.input_rectangles(other.id), 1)
        self.start_loop()
        self.pointer_to(500, 400)
        self.client.click(1)
        self.pointer_to(750, 150)
        self.client.click(1)
        self.assertTrue(wait_until(lambda: len(other_events.kinds('button')) == 2))
        self.assertEqual(overlay_events.events, [])


class LoopTests(X11TestCase):
    def test_timers_fire_in_order_and_cancel_works(self):
        order = []
        self.backend.call_later(0.15, lambda: order.append('c'))
        self.backend.call_later(0.05, lambda: order.append('a'))
        self.backend.call_later(0.1, lambda: order.append('x')).cancel()
        self.backend.call_later(0.1, lambda: order.append('b'))
        self.backend.call_later(0.25, self.backend.quit)
        self.backend.call_later(5, self.backend.quit)   # a bug must not hang the suite
        started = time.monotonic()
        self.backend.run()
        self.assertEqual(order, ['a', 'b', 'c'])
        self.assertLess(time.monotonic() - started, 2)

    def test_a_failing_callback_does_not_stop_the_loop(self):
        seen = []
        self.backend.call_soon(lambda: 1 / 0)
        self.backend.call_later(0.01, lambda: {}['missing'])
        self.backend.call_later(0.02, lambda: seen.append('timer'))
        self.backend.call_soon(lambda: seen.append('soon'))
        self.backend.call_later(0.1, self.backend.quit)
        with mock.patch.object(x11, 'log') as log:
            self.backend.run()
        self.assertEqual(sorted(seen), ['soon', 'timer'])
        messages = ' '.join(call.args[0] for call in log.call_args_list)
        self.assertIn('ZeroDivisionError', messages)
        self.assertIn('KeyError', messages)

    def test_call_soon_from_another_thread_wakes_a_sleeping_loop(self):
        self.start_loop()   # no timers: the loop sleeps until something wakes it
        time.sleep(0.1)
        started = time.monotonic()
        thread = self.loop.call(threading.get_ident)
        self.assertNotEqual(thread, threading.get_ident())
        self.assertEqual(thread, self.loop.thread.ident)
        self.assertLess(time.monotonic() - started, 1)

    def test_quit_from_another_thread_ends_run(self):
        self.start_loop()
        time.sleep(0.1)
        self.assertTrue(self.loop.stop())
        self.loop = None

    def test_quit_before_run_ends_it_at_once(self):
        self.backend.quit()
        self.backend.run()

    def test_run_closes_the_windows_it_still_owns(self):
        window = self.window()
        self.backend.call_later(0.05, self.backend.quit)
        self.backend.run()
        self.assertTrue(window.closed)
        self.assertNotIn(window.id, self.client.children())

    def test_call_soon_after_close_is_harmless(self):
        self.backend.close()
        self.backend.call_soon(lambda: None)
        self.backend.close()


class CloseTests(X11TestCase):
    def test_close_destroys_the_window_and_may_be_repeated(self):
        window = self.window()
        self.present(window, drawing())
        self.assertIn(window.id, self.client.children())
        window.close()
        window.close()
        self.assertNotIn(window.id, self.client.children())
        self.assertEqual(self.backend.windows, {})

    def test_a_window_destroyed_by_someone_else_reports_on_closed(self):
        window = self.window()
        self.present(window, drawing())
        self.start_loop()
        self.client.x.XDestroyWindow(self.client.dpy, window.id)
        self.client.sync()
        self.assertTrue(wait_until(lambda: self.recorder.kinds('closed')))
        self.assertEqual(self.recorder.kinds('closed'), [()])
        self.on_loop(window.close)   # harmless afterwards
        self.assertEqual(self.recorder.kinds('closed'), [()])

    def test_the_manager_asking_the_window_to_close(self):
        window = self.window()
        self.present(window, drawing())
        self.start_loop()
        self.client.delete_message(window.id)
        self.assertTrue(wait_until(lambda: self.recorder.kinds('closed')))
        self.assertNotIn(window.id, self.client.children())

    def test_a_callback_that_closes_its_window(self):
        window = self.window(w=200, h=150, x=100, y=100)
        self.present(window, drawing(200, 150))
        window.on_button = lambda *args: window.close()
        window.on_motion = lambda *args: self.recorder.events.append(('motion',))
        self.start_loop()
        self.pointer_to(150, 130)
        self.client.click(1)
        self.assertTrue(wait_until(lambda: window.closed))
        self.assertNotIn(window.id, self.client.children())


class ScreenTests(X11TestCase):
    def test_work_area_is_the_whole_screen_without_monitors_set_up(self):
        self.assertEqual(self.backend.work_area(), (0, 0, 1280, 800))

    @unittest.skipUnless(shutil.which('xrandr'), 'xrandr is not installed')
    def test_work_area_is_the_primary_monitor(self):
        self.monitors(('*left', '640/200x800/200+0+0'), ('right', '640/200x800/200+640+0'))
        self.assertEqual(self.backend.work_area(), (0, 0, 640, 800))
        self.monitors(('left', '640/200x800/200+0+0'), ('*right', '640/200x800/200+640+0'))
        self.assertEqual(self.backend.work_area(), (640, 0, 640, 800))

    def test_without_monitors_the_whole_screen_is_the_one_monitor(self):
        found = self.backend.monitors()
        self.assertEqual([(m.id, m.primary, m.rect) for m in found], [('screen', True, (0, 0, 1280, 800))])

    @unittest.skipUnless(shutil.which('xrandr'), 'xrandr is not installed')
    def test_every_monitor_is_listed_with_its_name_primary_first(self):
        self.monitors(('left', '640/200x800/200+0+0'), ('*right', '640/200x800/200+640+0'))
        found = self.backend.monitors()
        self.assertEqual([(m.id, m.primary, m.rect) for m in found],
                         [('right', True, (640, 0, 640, 800)), ('left', False, (0, 0, 640, 800))])
        self.assertTrue(all(m.scale == 1.0 for m in found))

    @unittest.skipUnless(shutil.which('xrandr'), 'xrandr is not installed')
    def test_with_no_primary_marked_the_monitor_at_the_origin_is_the_primary(self):
        self.monitors(('right', '640/200x800/200+640+0'), ('left', '640/200x800/200+0+0'))
        self.assertEqual([(m.id, m.primary) for m in self.backend.monitors()], [('left', True), ('right', False)])

    @unittest.skipUnless(shutil.which('xrandr'), 'xrandr is not installed')
    def test_plugging_a_monitor_in_is_reported(self):
        self.monitors(('*left', '640/200x800/200+0+0'))
        changes = []
        self.backend.on_screen_changed = lambda: changes.append([m.id for m in self.backend.monitors()])
        self.start_loop()
        self.monitors(('*left', '640/200x800/200+0+0'), ('right', '640/200x800/200+640+0'))
        self.assertTrue(wait_until(lambda: changes and changes[-1] == ['left', 'right']))

    def monitors(self, *specs):
        env = {**os.environ, 'DISPLAY': SERVER.display}
        for name in ('left', 'right'):
            subprocess.run(['xrandr', '--delmonitor', name], env=env, capture_output=True)
        self.addCleanup(lambda: [subprocess.run(['xrandr', '--delmonitor', name.lstrip('*')], env=env,
                                                capture_output=True) for name, _ in specs])
        for name, geometry in specs:
            subprocess.run(['xrandr', '--setmonitor', name, geometry, 'none'], env=env, check=True)

    def test_dpi_scale_follows_xft_dpi(self):
        self.addCleanup(self.client.clear_resources)
        self.assertEqual(self.backend.dpi_scale(), 1.0)
        for resources, scale in (('Xft.dpi:\t144\n', 1.5), ('*font: fixed\nXft.dpi: 192\nXft.hinting: 1\n', 2.0),
                                 ('Xft.dpi: 96', 1.0), ('Xft.dpi: 72', 1.0), ('Xft.dpi: 480', 3.0),
                                 ('Xft.dpi: crazy', 1.0), ('Xft.antialias: 1\n', 1.0)):
            self.client.set_resources(resources)
            self.assertEqual(self.backend.dpi_scale(), scale, resources)

    def test_parse_xft_dpi(self):
        self.assertEqual(x11.parse_xft_dpi('Xft.dpi:\t120.5\n'), 120.5)
        self.assertIsNone(x11.parse_xft_dpi('my.Xft.dpi: 120'))
        self.assertIsNone(x11.parse_xft_dpi(None))

    def test_screen_changes_are_reported_once_per_real_change(self):
        self.addCleanup(self.client.clear_resources)
        changes = []
        self.backend.on_screen_changed = lambda: changes.append((self.backend.work_area(), self.backend.dpi_scale()))
        self.start_loop()
        self.client.set_resources('Xft.dpi: 96\n')   # same scale as before: nothing changed
        time.sleep(0.3)
        self.assertEqual(changes, [])
        self.client.set_resources('Xft.dpi: 144\n')
        self.assertTrue(wait_until(lambda: changes))
        self.assertEqual(changes, [((0, 0, 1280, 800), 1.5)])

    @unittest.skipUnless(shutil.which('xrandr'), 'xrandr is not installed')
    def test_a_monitor_change_is_reported(self):
        changes = []
        self.backend.on_screen_changed = lambda: changes.append(self.backend.work_area())
        self.start_loop()
        self.monitors(('*left', '640/200x800/200+0+0'))
        self.assertTrue(wait_until(lambda: changes))
        self.assertEqual(changes[-1], (0, 0, 640, 800))


@unittest.skipIf(MISSING, MISSING)
class StartupTests(unittest.TestCase):
    def test_unreachable_display(self):
        with self.assertRaisesRegex(RuntimeError, 'DISPLAY'):
            x11.X11Backend(display=':987')

    def test_no_display_in_the_environment(self):
        with mock.patch.dict(os.environ):
            os.environ.pop('DISPLAY', None)
            with self.assertRaisesRegex(RuntimeError, 'DISPLAY'):
                x11.X11Backend()

    def test_server_without_a_32_bit_visual(self):
        server = XServer('640x480x8')   # 8-bit screens have no 32-bit visual
        self.addCleanup(server.stop)
        with self.assertRaisesRegex(RuntimeError, '32-bit'):
            x11.X11Backend(display=server.display)


if __name__ == '__main__':
    unittest.main()
