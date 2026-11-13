"""The slice of Xlib, XShape and XRandR that the X11 backend uses, declared for ctypes.

Plain `ctypes` instead of python-xlib or GTK: the backend needs a few dozen calls and nothing else.
Every prototype is declared (the default `int` return type would cut 64-bit pointers and XIDs),
and the optional libraries come back as None when they are not installed.
"""
import ctypes
from ctypes import POINTER as P, c_char_p, c_int as i, c_long as l, c_uint as ui, c_ulong as ul, c_void_p as vp

# event masks
BUTTON_PRESS_MASK, BUTTON_RELEASE_MASK, POINTER_MOTION_MASK, BUTTON_MOTION_MASK = 1 << 2, 1 << 3, 1 << 6, 1 << 13
EXPOSURE_MASK, STRUCTURE_NOTIFY_MASK, PROPERTY_CHANGE_MASK = 1 << 15, 1 << 17, 1 << 22

# event types
BUTTON_PRESS, BUTTON_RELEASE, MOTION_NOTIFY, EXPOSE = 4, 5, 6, 12
DESTROY_NOTIFY, MAP_NOTIFY, CONFIGURE_NOTIFY, PROPERTY_NOTIFY, CLIENT_MESSAGE = 17, 19, 22, 28, 33

# XCreateWindow
INPUT_OUTPUT, ALLOC_NONE, TRUE_COLOR, Z_PIXMAP = 1, 0, 4, 2
CW_BACK_PIXMAP, CW_BORDER_PIXEL, CW_EVENT_MASK, CW_COLORMAP = 1 << 0, 1 << 3, 1 << 11, 1 << 13
VISUAL_SCREEN_MASK, VISUAL_DEPTH_MASK, VISUAL_CLASS_MASK = 1 << 1, 1 << 2, 1 << 3
VISUAL_RED_MASK, VISUAL_GREEN_MASK, VISUAL_BLUE_MASK = 1 << 4, 1 << 5, 1 << 6

# window manager hints
INPUT_HINT, STATIC_GRAVITY = 1, 10
US_POSITION, P_POSITION, P_SIZE, P_MIN_SIZE, P_MAX_SIZE, P_WIN_GRAVITY = 1 << 0, 1 << 2, 1 << 3, 1 << 4, 1 << 5, 1 << 9
MWM_HINTS_DECORATIONS = 1 << 1
XA_ATOM, XA_CARDINAL, XA_STRING = 4, 6, 31

# extensions
SHAPE_INPUT, SHAPE_SET, SHAPE_UNSORTED = 2, 0, 0
RR_SCREEN_CHANGE_MASK, RR_CRTC_CHANGE_MASK, RR_OUTPUT_CHANGE_MASK = 1 << 0, 1 << 1, 1 << 2
BAD_WINDOW, BAD_DRAWABLE = 3, 9

ERROR_HANDLER = ctypes.CFUNCTYPE(i, vp, vp)


class XSetWindowAttributes(ctypes.Structure):
    _fields_ = [('background_pixmap', ul), ('background_pixel', ul), ('border_pixmap', ul), ('border_pixel', ul),
                ('bit_gravity', i), ('win_gravity', i), ('backing_store', i), ('backing_planes', ul),
                ('backing_pixel', ul), ('save_under', i), ('event_mask', l), ('do_not_propagate_mask', l),
                ('override_redirect', i), ('colormap', ul), ('cursor', ul)]


class XSizeHints(ctypes.Structure):
    _fields_ = [('flags', l), ('x', i), ('y', i), ('width', i), ('height', i), ('min_width', i), ('min_height', i),
                ('max_width', i), ('max_height', i), ('width_inc', i), ('height_inc', i), ('min_aspect_x', i),
                ('min_aspect_y', i), ('max_aspect_x', i), ('max_aspect_y', i), ('base_width', i),
                ('base_height', i), ('win_gravity', i)]


class XWMHints(ctypes.Structure):
    _fields_ = [('flags', l), ('input', i), ('initial_state', i), ('icon_pixmap', ul), ('icon_window', ul),
                ('icon_x', i), ('icon_y', i), ('icon_mask', ul), ('window_group', ul)]


class XClassHint(ctypes.Structure):
    _fields_ = [('res_name', c_char_p), ('res_class', c_char_p)]


class XVisualInfo(ctypes.Structure):
    _fields_ = [('visual', vp), ('visualid', ul), ('screen', i), ('depth', i), ('klass', i), ('red_mask', ul),
                ('green_mask', ul), ('blue_mask', ul), ('colormap_size', i), ('bits_per_rgb', i)]


class XImage(ctypes.Structure):
    _fields_ = [('width', i), ('height', i), ('xoffset', i), ('format', i), ('data', vp), ('byte_order', i),
                ('bitmap_unit', i), ('bitmap_bit_order', i), ('bitmap_pad', i), ('depth', i),
                ('bytes_per_line', i), ('bits_per_pixel', i), ('red_mask', ul), ('green_mask', ul),
                ('blue_mask', ul), ('obdata', vp), ('funcs', vp * 6)]


class XRRMonitorInfo(ctypes.Structure):
    _fields_ = [('name', ul), ('primary', i), ('automatic', i), ('noutput', i), ('x', i), ('y', i),
                ('width', i), ('height', i), ('mwidth', i), ('mheight', i), ('outputs', vp)]


class XErrorEvent(ctypes.Structure):
    _fields_ = [('type', i), ('display', vp), ('resourceid', ul), ('serial', ul), ('error_code', ctypes.c_ubyte),
                ('request_code', ctypes.c_ubyte), ('minor_code', ctypes.c_ubyte)]


# The event structures start with the same five fields; `window` is the window the event is reported on
# (for the StructureNotify events that is `event`, which equals `window` for our own windows).
_HEAD = [('type', i), ('serial', ul), ('send_event', i), ('display', vp), ('window', ul)]


class XAnyEvent(ctypes.Structure):
    _fields_ = _HEAD


class XButtonEvent(ctypes.Structure):
    _fields_ = _HEAD + [('root', ul), ('subwindow', ul), ('time', ul), ('x', i), ('y', i), ('x_root', i),
                        ('y_root', i), ('state', ui), ('button', ui), ('same_screen', i)]


class XMotionEvent(ctypes.Structure):
    _fields_ = _HEAD + [('root', ul), ('subwindow', ul), ('time', ul), ('x', i), ('y', i), ('x_root', i),
                        ('y_root', i), ('state', ui), ('is_hint', ctypes.c_char), ('same_screen', i)]


class XConfigureEvent(ctypes.Structure):
    _fields_ = _HEAD + [('configured', ul), ('x', i), ('y', i), ('width', i), ('height', i), ('border_width', i),
                        ('above', ul), ('override_redirect', i)]


class XExposeEvent(ctypes.Structure):
    _fields_ = _HEAD + [('x', i), ('y', i), ('width', i), ('height', i), ('count', i)]


class XPropertyEvent(ctypes.Structure):
    _fields_ = _HEAD + [('atom', ul), ('time', ul), ('state', i)]


class XClientMessageEvent(ctypes.Structure):
    _fields_ = _HEAD + [('message_type', ul), ('format', i), ('data', l * 5)]


class XEvent(ctypes.Union):
    _fields_ = [('type', i), ('xany', XAnyEvent), ('xbutton', XButtonEvent), ('xmotion', XMotionEvent),
                ('xconfigure', XConfigureEvent), ('xexpose', XExposeEvent), ('xproperty', XPropertyEvent),
                ('xclient', XClientMessageEvent), ('pad', l * 24)]


XLIB = {
    'XOpenDisplay': (vp, [c_char_p]),
    'XCloseDisplay': (i, [vp]),
    'XConnectionNumber': (i, [vp]),
    'XDefaultScreen': (i, [vp]),
    'XRootWindow': (ul, [vp, i]),
    'XInternAtoms': (i, [vp, P(c_char_p), i, i, P(ul)]),
    'XGetVisualInfo': (P(XVisualInfo), [vp, l, P(XVisualInfo), P(i)]),
    'XCreateColormap': (ul, [vp, ul, vp, i]),
    'XFreeColormap': (i, [vp, ul]),
    'XCreateWindow': (ul, [vp, ul, i, i, ui, ui, ui, i, ui, vp, ul, P(XSetWindowAttributes)]),
    'XDestroyWindow': (i, [vp, ul]),
    'XMapWindow': (i, [vp, ul]),
    'XMoveWindow': (i, [vp, ul, i, i]),
    'XResizeWindow': (i, [vp, ul, ui, ui]),
    'XSelectInput': (i, [vp, ul, l]),
    'XChangeProperty': (i, [vp, ul, ul, ul, i, i, vp, i]),
    'XSetWMNormalHints': (None, [vp, ul, P(XSizeHints)]),
    'XSetWMHints': (i, [vp, ul, P(XWMHints)]),
    'XSetClassHint': (i, [vp, ul, P(XClassHint)]),
    'XSetWMProtocols': (i, [vp, ul, P(ul), i]),
    'XCreateGC': (vp, [vp, ul, ul, vp]),
    'XFreeGC': (i, [vp, vp]),
    'XCreateImage': (P(XImage), [vp, vp, ui, i, i, vp, ui, ui, i, i]),
    'XDestroyImage': (i, [P(XImage)]),
    'XPutImage': (i, [vp, ul, vp, P(XImage), i, i, i, i, ui, ui]),
    'XGetGeometry': (i, [vp, ul, P(ul), P(i), P(i), P(ui), P(ui), P(ui), P(ui)]),
    'XTranslateCoordinates': (i, [vp, ul, ul, i, i, P(i), P(i), P(ul)]),
    'XGetWindowProperty': (i, [vp, ul, ul, l, l, i, ul, P(ul), P(i), P(ul), P(ul), P(vp)]),
    'XPending': (i, [vp]),
    'XNextEvent': (i, [vp, P(XEvent)]),
    'XFlush': (i, [vp]),
    'XSync': (i, [vp, i]),
    'XFree': (i, [vp]),
    'XGetAtomName': (vp, [vp, ul]),
    'XSetErrorHandler': (vp, [ERROR_HANDLER]),
}
XEXT = {
    'XShapeQueryExtension': (i, [vp, P(i), P(i)]),
    'XShapeQueryVersion': (i, [vp, P(i), P(i)]),
    'XShapeCombineRectangles': (None, [vp, ul, i, i, i, vp, i, i, i]),
}
XRANDR = {
    'XRRQueryExtension': (i, [vp, P(i), P(i)]),
    'XRRQueryVersion': (i, [vp, P(i), P(i)]),
    'XRRSelectInput': (None, [vp, ul, i]),
    'XRRGetMonitors': (P(XRRMonitorInfo), [vp, ul, i, P(i)]),
    'XRRFreeMonitors': (None, [P(XRRMonitorInfo)]),
}


def _bind(library, table, required):
    """Loads `library` and declares every function of `table`. Returns None when the library or one of
    its functions is missing and `required` is false (older libXrandr, no libXext)."""
    try:
        lib = ctypes.CDLL(library)
        for name, (restype, argtypes) in table.items():
            function = getattr(lib, name)
            function.restype, function.argtypes = restype, argtypes
    except (OSError, AttributeError):
        if required:
            raise
        return None
    return lib


def load_xlib():
    try:
        return _bind('libX11.so.6', XLIB, True)
    except (OSError, AttributeError) as e:
        raise RuntimeError(f'libX11 is needed to draw the widgets on Linux: {e}') from e


def load_xext():
    return _bind('libXext.so.6', XEXT, False)


def load_xrandr():
    return _bind('libXrandr.so.2', XRANDR, False)
