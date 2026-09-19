"""Just enough Xlib (through ctypes) to find the widget windows, read where they are and move them.
No python-xlib needed. Works under Xwayland and on X11 desktops."""
import ctypes


class X11:
    """Finds deskwidget:<id> windows (via _NET_CLIENT_LIST) and reads/sets their position."""
    ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def __init__(self):
        x = ctypes.CDLL('libX11.so.6')
        vp, ul, i, P = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.POINTER
        x.XOpenDisplay.restype, x.XOpenDisplay.argtypes = vp, [ctypes.c_char_p]
        x.XDefaultRootWindow.restype, x.XDefaultRootWindow.argtypes = ul, [vp]
        x.XInternAtom.restype, x.XInternAtom.argtypes = ul, [vp, ctypes.c_char_p, i]
        x.XGetWindowProperty.restype = i
        x.XGetWindowProperty.argtypes = [vp, ul, ul, ctypes.c_long, ctypes.c_long, i, ul,
                                         P(ul), P(i), P(ul), P(ul), P(vp)]
        x.XFetchName.restype, x.XFetchName.argtypes = i, [vp, ul, P(vp)]
        x.XFree.argtypes = [vp]
        x.XTranslateCoordinates.restype = i
        x.XTranslateCoordinates.argtypes = [vp, ul, ul, i, i, P(i), P(i), P(ul)]
        x.XDisplayWidth.restype, x.XDisplayWidth.argtypes = i, [vp, i]
        x.XDisplayHeight.restype, x.XDisplayHeight.argtypes = i, [vp, i]
        x.XMoveWindow.argtypes = [vp, ul, i, i]
        x.XFlush.argtypes = [vp]
        x.XSetErrorHandler.restype, x.XSetErrorHandler.argtypes = vp, [self.ERROR_HANDLER]
        self.x = x
        # a widget window can vanish between two calls: ignore BadWindow instead of exiting
        self._on_error = self.ERROR_HANDLER(lambda dpy, event: 0)
        x.XSetErrorHandler(self._on_error)
        self.dpy = x.XOpenDisplay(None)
        if not self.dpy:
            raise RuntimeError('cannot open the X display (is DISPLAY set?)')
        self.root = x.XDefaultRootWindow(self.dpy)
        self.client_list = x.XInternAtom(self.dpy, b'_NET_CLIENT_LIST', 0)

    def screen_size(self):
        return self.x.XDisplayWidth(self.dpy, 0), self.x.XDisplayHeight(self.dpy, 0)

    def widget_windows(self):
        x, c = self.x, ctypes
        typ, fmt, n, after, prop = c.c_ulong(), c.c_int(), c.c_ulong(), c.c_ulong(), c.c_void_p()
        XA_WINDOW = 33
        if x.XGetWindowProperty(self.dpy, self.root, self.client_list, 0, 4096, 0, XA_WINDOW,
                                c.byref(typ), c.byref(fmt), c.byref(n), c.byref(after), c.byref(prop)) != 0 \
                or not prop.value:
            return {}
        windows = c.cast(prop, c.POINTER(c.c_ulong * n.value)).contents[:]
        x.XFree(prop)
        found = {}
        for win in windows:
            name = c.c_void_p()
            if x.XFetchName(self.dpy, win, c.byref(name)) and name.value:
                title = c.string_at(name.value).decode('utf-8', 'replace')
                x.XFree(name)
                if title.startswith('deskwidget:'):
                    found[title[len('deskwidget:'):]] = win
        return found

    def position(self, win):
        c = ctypes
        px, py, child = c.c_int(), c.c_int(), c.c_ulong()
        if not self.x.XTranslateCoordinates(self.dpy, win, self.root, 0, 0,
                                            c.byref(px), c.byref(py), c.byref(child)):
            return None
        return px.value, py.value

    def move(self, win, x, y):
        self.x.XMoveWindow(self.dpy, win, x, y)
        self.x.XFlush(self.dpy)


