"""The Windows backend: layered windows, a Win32 message loop, a tray icon and global hotkeys.

Plain ctypes (user32, gdi32, shell32, kernel32, shcore), so the only extra dependency is pycairo, which
the engine needs anyway. The whole module imports on any system (the tests rely on it); the DLLs are
loaded only when `Win32Backend()` is created, and only on Windows.

A widget is a `WS_EX_LAYERED` pop-up whose pixels go to `UpdateLayeredWindow`: a pycairo ARGB32 surface is
premultiplied BGRA, exactly what a top-down 32 bpp DIB with `AC_SRC_ALPHA` wants, so a frame is one memcpy
per row and no per-pixel conversion. Windows are `WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE`: no taskbar button,
not in Alt+Tab, never focused by a click.

One hidden top-level window (the control window) owns everything that is not a widget: the wake-up message
for `call_soon()`, the tray icon, the hotkeys and the broadcasts about display changes. It must be a real
top-level window, not a message-only one: only top-level windows receive broadcasts such as
`WM_DISPLAYCHANGE` and `TaskbarCreated`.
"""
import ctypes
import ctypes.wintypes as wt
import itertools
import math
import sys
import threading
import traceback
from collections import deque
from pathlib import Path
from types import SimpleNamespace

from ..core.util import log
from .base import LEFT, MIDDLE, RIGHT, Backend, TimerQueue, Window

LRESULT = ctypes.c_ssize_t   # LONG_PTR: 64 bits on 64-bit Windows, which is where ctypes defaults bite
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t

# window messages
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_MOVE = 0x0003
WM_QUIT = 0x0012
WM_QUERYENDSESSION = 0x0011
WM_ERASEBKGND = 0x0014
WM_ENDSESSION = 0x0016
WM_SETTINGCHANGE = 0x001A
WM_MOUSEACTIVATE = 0x0021
WM_WINDOWPOSCHANGING = 0x0046
WM_DISPLAYCHANGE = 0x007E
WM_NCDESTROY = 0x0082
WM_TIMER = 0x0113
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_CAPTURECHANGED = 0x0215
WM_HOTKEY = 0x0312
WM_DPICHANGED = 0x02E0
WM_APP = 0x8000
WM_APP_WAKE = WM_APP + 1     # call_soon(): something is queued
WM_APP_QUIT = WM_APP + 2     # quit(): leave the loop, also from inside a modal menu loop
WM_TRAY = WM_APP + 3         # callback message of the tray icons (wParam = icon id)

MA_NOACTIVATE = 3

# window styles and positioning
WS_POPUP = 0x80000000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
SW_SHOWNOACTIVATE = 4
HWND_BOTTOM = 1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x2, 0x4, 0x10
ULW_ALPHA = 0x2
AC_SRC_OVER, AC_SRC_ALPHA = 0, 1

# loop
PM_REMOVE = 1
QS_ALLINPUT = 0x04FF
MAX_WAIT_MS = 500            # also how long Ctrl+C in a console can take: Python sees signals between waits
PUMP_TIMER = 1               # SetTimer id that keeps timers running while a menu has its own loop
PUMP_MS = 50
SCREEN_DEBOUNCE = 0.25       # seconds; a display change arrives as a burst of messages

# screen
SPI_GETWORKAREA = 0x0030
SM_CXSCREEN, SM_CYSCREEN, SM_CXSMICON, SM_CYSMICON = 0, 1, 49, 50
LOGPIXELSX = 88
MDT_EFFECTIVE_DPI = 0
MONITOR_DEFAULTTOPRIMARY = 1
PER_MONITOR_AWARE_V2 = -4    # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 is the handle value -4
PROCESS_PER_MONITOR_DPI_AWARE = 2

# gdi
BI_RGB = 0
DIB_RGB_COLORS = 0

# resources, tray, menus
IDI_APPLICATION = 32512
IDC_ARROW = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x10
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO = 0x1
MF_STRING, MF_SEPARATOR = 0x0, 0x800
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

_CLASS_IDS = itertools.count(1)   # one window class per backend, so two backends never share a WNDPROC

_FUNCTYPE = getattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE)   # stdcall on 32-bit Windows
WNDPROC = _FUNCTYPE(LRESULT, wt.HWND, wt.UINT, WPARAM, LPARAM)


# Structures use fixed-width integers (DWORD = 32 bits, as on Windows), so their layout is the same
# wherever the tests run; wchar_t is the exception, 16 bits only on Windows.
class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [('cbSize', wt.UINT), ('style', wt.UINT), ('lpfnWndProc', WNDPROC),
                ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int), ('hInstance', wt.HINSTANCE),
                ('hIcon', wt.HICON), ('hCursor', wt.HANDLE), ('hbrBackground', wt.HBRUSH),
                ('lpszMenuName', wt.LPCWSTR), ('lpszClassName', wt.LPCWSTR), ('hIconSm', wt.HICON)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [('BlendOp', ctypes.c_ubyte), ('BlendFlags', ctypes.c_ubyte),
                ('SourceConstantAlpha', ctypes.c_ubyte), ('AlphaFormat', ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_int32), ('biHeight', ctypes.c_int32),
                ('biPlanes', ctypes.c_uint16), ('biBitCount', ctypes.c_uint16),
                ('biCompression', ctypes.c_uint32), ('biSizeImage', ctypes.c_uint32),
                ('biXPelsPerMeter', ctypes.c_int32), ('biYPelsPerMeter', ctypes.c_int32),
                ('biClrUsed', ctypes.c_uint32), ('biClrImportant', ctypes.c_uint32)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', ctypes.c_uint32 * 1)]


class WINDOWPOS(ctypes.Structure):
    _fields_ = [('hwnd', wt.HWND), ('hwndInsertAfter', wt.HWND), ('x', ctypes.c_int), ('y', ctypes.c_int),
                ('cx', ctypes.c_int), ('cy', ctypes.c_int), ('flags', wt.UINT)]


class ICONINFO(ctypes.Structure):
    _fields_ = [('fIcon', ctypes.c_int32), ('xHotspot', ctypes.c_uint32), ('yHotspot', ctypes.c_uint32),
                ('hbmMask', wt.HBITMAP), ('hbmColor', wt.HBITMAP)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint32), ('hWnd', wt.HWND), ('uID', wt.UINT), ('uFlags', wt.UINT),
                ('uCallbackMessage', wt.UINT), ('hIcon', wt.HICON), ('szTip', ctypes.c_wchar * 128),
                ('dwState', ctypes.c_uint32), ('dwStateMask', ctypes.c_uint32),
                ('szInfo', ctypes.c_wchar * 256), ('uTimeout', wt.UINT),
                ('szInfoTitle', ctypes.c_wchar * 64), ('dwInfoFlags', ctypes.c_uint32),
                ('guidItem', ctypes.c_ubyte * 16), ('hBalloonIcon', wt.HICON)]


_USER32 = {
    'RegisterClassExW': (wt.WORD, [ctypes.POINTER(WNDCLASSEXW)]),
    'CreateWindowExW': (wt.HWND, [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]),
    'DestroyWindow': (wt.BOOL, [wt.HWND]),
    'DefWindowProcW': (LRESULT, [wt.HWND, wt.UINT, WPARAM, LPARAM]),
    'ShowWindow': (wt.BOOL, [wt.HWND, ctypes.c_int]),
    'SetWindowPos': (wt.BOOL, [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               wt.UINT]),
    'UpdateLayeredWindow': (wt.BOOL, [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE),
                                      wt.HDC, ctypes.POINTER(wt.POINT), wt.DWORD,
                                      ctypes.POINTER(BLENDFUNCTION), wt.DWORD]),
    'GetWindowRect': (wt.BOOL, [wt.HWND, ctypes.POINTER(wt.RECT)]),
    'ClientToScreen': (wt.BOOL, [wt.HWND, ctypes.POINTER(wt.POINT)]),
    'GetCursorPos': (wt.BOOL, [ctypes.POINTER(wt.POINT)]),
    'GetDC': (wt.HDC, [wt.HWND]),
    'ReleaseDC': (ctypes.c_int, [wt.HWND, wt.HDC]),
    'SetCapture': (wt.HWND, [wt.HWND]),
    'GetCapture': (wt.HWND, []),
    'ReleaseCapture': (wt.BOOL, []),
    'PeekMessageW': (wt.BOOL, [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]),
    'TranslateMessage': (wt.BOOL, [ctypes.POINTER(wt.MSG)]),
    'DispatchMessageW': (LRESULT, [ctypes.POINTER(wt.MSG)]),
    'MsgWaitForMultipleObjects': (wt.DWORD, [wt.DWORD, ctypes.POINTER(wt.HANDLE), wt.BOOL, wt.DWORD,
                                             wt.DWORD]),
    'PostMessageW': (wt.BOOL, [wt.HWND, wt.UINT, WPARAM, LPARAM]),
    'PostQuitMessage': (None, [ctypes.c_int]),
    'SetTimer': (ctypes.c_size_t, [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]),
    'KillTimer': (wt.BOOL, [wt.HWND, ctypes.c_size_t]),
    'RegisterWindowMessageW': (wt.UINT, [wt.LPCWSTR]),
    'RegisterHotKey': (wt.BOOL, [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]),
    'UnregisterHotKey': (wt.BOOL, [wt.HWND, ctypes.c_int]),
    'LoadCursorW': (wt.HANDLE, [wt.HINSTANCE, ctypes.c_void_p]),
    'LoadIconW': (wt.HICON, [wt.HINSTANCE, ctypes.c_void_p]),
    'LoadImageW': (wt.HANDLE, [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]),
    'DestroyIcon': (wt.BOOL, [wt.HICON]),
    'CreateIconIndirect': (wt.HICON, [ctypes.POINTER(ICONINFO)]),
    'CreatePopupMenu': (wt.HMENU, []),
    'AppendMenuW': (wt.BOOL, [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]),
    'TrackPopupMenu': (wt.BOOL, [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND,
                                 ctypes.c_void_p]),
    'DestroyMenu': (wt.BOOL, [wt.HMENU]),
    'SetForegroundWindow': (wt.BOOL, [wt.HWND]),
    'SystemParametersInfoW': (wt.BOOL, [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT]),
    'GetSystemMetrics': (ctypes.c_int, [ctypes.c_int]),
    'GetDoubleClickTime': (wt.UINT, []),
    'MonitorFromPoint': (wt.HANDLE, [wt.POINT, wt.DWORD]),
    'SetProcessDPIAware': (wt.BOOL, []),
}
_USER32_OPTIONAL = {   # newer than Windows 7
    'SetProcessDpiAwarenessContext': (wt.BOOL, [wt.HANDLE]),
    'GetDpiForSystem': (wt.UINT, []),
}
_GDI32 = {
    'CreateCompatibleDC': (wt.HDC, [wt.HDC]),
    'DeleteDC': (wt.BOOL, [wt.HDC]),
    'CreateDIBSection': (wt.HBITMAP, [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                      ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]),
    'CreateBitmap': (wt.HBITMAP, [ctypes.c_int, ctypes.c_int, wt.UINT, wt.UINT, ctypes.c_void_p]),
    'SelectObject': (wt.HANDLE, [wt.HDC, wt.HANDLE]),
    'DeleteObject': (wt.BOOL, [wt.HANDLE]),
    'GetDeviceCaps': (ctypes.c_int, [wt.HDC, ctypes.c_int]),
}
_SHELL32 = {
    'Shell_NotifyIconW': (wt.BOOL, [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]),
}
_KERNEL32 = {
    'GetModuleHandleW': (wt.HMODULE, [wt.LPCWSTR]),
}
_SHCORE_OPTIONAL = {   # Windows 8.1+
    'GetDpiForMonitor': (ctypes.c_long, [wt.HANDLE, ctypes.c_int, ctypes.POINTER(wt.UINT),
                                         ctypes.POINTER(wt.UINT)]),
    'SetProcessDpiAwareness': (ctypes.c_long, [ctypes.c_int]),
}


def _bind(lib, table, optional=False):
    """The functions of `table` with their argtypes/restype set, as attributes of a namespace.
    Without declared types ctypes passes handles as 32-bit ints, which breaks on 64-bit Windows."""
    functions = {}
    for name, (restype, argtypes) in table.items():
        try:
            function = getattr(lib, name)
        except AttributeError:
            if not optional:
                raise
            functions[name] = None
            continue
        function.restype, function.argtypes = restype, argtypes
        functions[name] = function
    return SimpleNamespace(**functions)


def load_api():
    """The Win32 functions this module uses. Windows only."""
    if sys.platform != 'win32':
        raise RuntimeError('Windows only')
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    api = SimpleNamespace(
        user32=_bind(user32, _USER32),
        gdi32=_bind(ctypes.WinDLL('gdi32', use_last_error=True), _GDI32),
        shell32=_bind(ctypes.WinDLL('shell32', use_last_error=True), _SHELL32),
        kernel32=_bind(ctypes.WinDLL('kernel32', use_last_error=True), _KERNEL32))
    for name, function in vars(_bind(user32, _USER32_OPTIONAL, optional=True)).items():
        setattr(api.user32, name, function)
    try:
        api.shcore = _bind(ctypes.WinDLL('shcore', use_last_error=True), _SHCORE_OPTIONAL, optional=True)
    except OSError:
        api.shcore = SimpleNamespace(**dict.fromkeys(_SHCORE_OPTIONAL))
    return api


def last_error():
    return getattr(ctypes, 'get_last_error', lambda: 0)()


def lparam_point(lparam):
    """(x, y) from an LPARAM holding two signed 16-bit values. Negative on a monitor to the left of or
    above the primary one, and while the mouse is captured outside the window."""
    return (ctypes.c_short(lparam & 0xFFFF).value, ctypes.c_short((lparam >> 16) & 0xFFFF).value)


def wait_ms(timeout):
    """Milliseconds for MsgWaitForMultipleObjects from a TimerQueue timeout (seconds or None)."""
    if timeout is None:
        return MAX_WAIT_MS
    return min(MAX_WAIT_MS, int(math.ceil(timeout * 1000)))


_HOTKEY_MODIFIERS = {'ctrl': MOD_CONTROL, 'control': MOD_CONTROL, 'alt': MOD_ALT, 'shift': MOD_SHIFT,
                     'win': MOD_WIN, 'super': MOD_WIN, 'meta': MOD_WIN, 'cmd': MOD_WIN}
_HOTKEY_KEYS = {'space': 0x20, 'enter': 0x0D, 'return': 0x0D, 'tab': 0x09, 'esc': 0x1B, 'escape': 0x1B,
                'backspace': 0x08, 'delete': 0x2E, 'del': 0x2E, 'insert': 0x2D, 'ins': 0x2D,
                'home': 0x24, 'end': 0x23, 'pageup': 0x21, 'pgup': 0x21, 'pagedown': 0x22, 'pgdn': 0x22,
                'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28, 'pause': 0x13,
                'print': 0x2C, 'printscreen': 0x2C, 'prtsc': 0x2C}


def parse_hotkey(spec):
    """'ctrl+alt+w' -> (MOD_CONTROL | MOD_ALT, virtual-key code of W); None if it is not a usable hotkey.

    Exactly one key plus at least one modifier: a global hotkey without a modifier would swallow that key
    in every application."""
    if not isinstance(spec, str):
        return None
    modifiers, keys = 0, []
    for part in spec.lower().split('+'):
        part = part.strip()
        if part in _HOTKEY_MODIFIERS:
            modifiers |= _HOTKEY_MODIFIERS[part]
        elif part:
            keys.append(part)
        else:
            return None
    if modifiers == 0 or len(keys) != 1:
        return None
    key = keys[0]
    if key in _HOTKEY_KEYS:
        return modifiers, _HOTKEY_KEYS[key]
    if len(key) == 1 and key.isascii() and key.isalnum():
        return modifiers, ord(key.upper())
    if key[0] == 'f' and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return modifiers, 0x6F + int(key[1:])
    return None


def buffer_address(buffer):
    """(address, keep-alive) of a bytes-like object, writable or not."""
    try:
        view = (ctypes.c_char * memoryview(buffer).nbytes).from_buffer(buffer)
    except TypeError:
        view = ctypes.create_string_buffer(bytes(buffer), len(buffer))
    return ctypes.addressof(view), view


def copy_rows(dst, dst_stride, src, src_stride, row_bytes, rows):
    """Copies `rows` rows of `row_bytes` bytes from `src` (a bytes-like object with `src_stride` bytes
    per row) to address `dst` (rows `dst_stride` apart). One memmove when both layouts are packed."""
    if rows < 1 or row_bytes < 1:
        return
    if src_stride < row_bytes or dst_stride < row_bytes:
        raise ValueError('stride is smaller than a row')
    if memoryview(src).nbytes < src_stride * (rows - 1) + row_bytes:
        raise ValueError('source buffer is smaller than the image')
    address, _keep = buffer_address(src)
    if src_stride == dst_stride == row_bytes:
        ctypes.memmove(dst, address, row_bytes * rows)
        return
    for row in range(rows):
        ctypes.memmove(dst + row * dst_stride, address + row * src_stride, row_bytes)


def create_dib(api, width, height):
    """(HBITMAP, address of its pixels) of a top-down 32 bpp DIB section."""
    info = BITMAPINFO()
    header = info.bmiHeader
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth, header.biHeight = width, -height   # negative height = top-down, like Cairo
    header.biPlanes, header.biBitCount, header.biCompression = 1, 32, BI_RGB
    bits = ctypes.c_void_p()
    bitmap = api.gdi32.CreateDIBSection(None, ctypes.byref(info), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
    if not bitmap or not bits.value:
        raise OSError(f'CreateDIBSection({width}x{height}) failed (error {last_error()})')
    return bitmap, bits.value


class Dib:
    """A top-down 32 bpp DIB selected into a memory DC: where a frame is staged for UpdateLayeredWindow.
    A window keeps one while its size stays the same."""

    def __init__(self, api, width, height):
        self._api = api
        self.width, self.height, self.stride = width, height, width * 4
        self.bitmap, self.bits = create_dib(api, width, height)
        self.dc = api.gdi32.CreateCompatibleDC(None)
        if not self.dc:
            api.gdi32.DeleteObject(self.bitmap)
            raise OSError(f'CreateCompatibleDC failed (error {last_error()})')
        self._old = api.gdi32.SelectObject(self.dc, self.bitmap)

    def fill(self, data, stride):
        copy_rows(self.bits, self.stride, data, stride, self.width * 4, self.height)

    def close(self):
        gdi32 = self._api.gdi32
        gdi32.SelectObject(self.dc, self._old)
        gdi32.DeleteObject(self.bitmap)
        gdi32.DeleteDC(self.dc)


def icon_from_png(api, path, size):
    """An HICON from a PNG, or None. Scaled to fit `size` x `size`, keeping the proportions.

    The PNG is read by pycairo, so no extra imaging library is needed; the HICON is built from a 32 bpp
    DIB with `CreateIconIndirect`, which keeps the alpha channel."""
    try:
        import cairo
        source = cairo.ImageSurface.create_from_png(str(path))
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    except Exception:
        return None
    context = cairo.Context(surface)
    scale = min(size / source.get_width(), size / source.get_height())
    context.translate((size - source.get_width() * scale) / 2, (size - source.get_height() * scale) / 2)
    context.scale(scale, scale)
    context.set_source_surface(source, 0, 0)
    context.get_source().set_filter(cairo.FILTER_BEST)
    context.paint()
    surface.flush()
    color = mask = None
    try:
        color, bits = create_dib(api, size, size)
        copy_rows(bits, size * 4, surface.get_data(), surface.get_stride(), size * 4, size)
        # an all-zero 1 bpp mask: the alpha of the colour bitmap decides what is visible
        zeros = ctypes.create_string_buffer(((size + 15) // 16) * 2 * size)
        mask = api.gdi32.CreateBitmap(size, size, 1, 1, ctypes.addressof(zeros))
        info = ICONINFO(True, 0, 0, mask, color)
        return api.user32.CreateIconIndirect(ctypes.byref(info)) or None
    except (OSError, ValueError):
        return None
    finally:
        for bitmap in (color, mask):
            if bitmap:
                api.gdi32.DeleteObject(bitmap)


def load_icon(api, path, size):
    """(HICON, owned). `path` may be an .ico or a .png; anything missing or unreadable gives the stock
    application icon, which Windows owns (it must not be destroyed)."""
    if path:
        loaders = [lambda: api.user32.LoadImageW(None, str(path), IMAGE_ICON, size, size, LR_LOADFROMFILE),
                   lambda: icon_from_png(api, path, size)]
        if Path(str(path)).suffix.lower() == '.png':
            loaders.reverse()
        for loader in loaders:
            icon = loader()
            if icon:
                return icon, True
    return api.user32.LoadIconW(None, IDI_APPLICATION), False


class CallQueue:
    """What `call_soon()` hands over from any thread. `put()` says whether the loop needs a wake-up
    message: only the first call after a `drain()` does, so a burst of calls cannot overflow the
    10000-message limit of a Windows message queue."""

    def __init__(self):
        self._items = deque()
        self._lock = threading.Lock()
        self._wake_sent = False

    def put(self, callback):
        with self._lock:
            self._items.append(callback)
            wake, self._wake_sent = not self._wake_sent, True
        return wake

    def drain(self):
        with self._lock:
            items, self._items = list(self._items), deque()
            self._wake_sent = False
        return items


_BUTTON_MESSAGES = {
    WM_LBUTTONDOWN: (LEFT, True), WM_LBUTTONUP: (LEFT, False),
    WM_RBUTTONDOWN: (RIGHT, True), WM_RBUTTONUP: (RIGHT, False),
    WM_MBUTTONDOWN: (MIDDLE, True), WM_MBUTTONUP: (MIDDLE, False),
}


class Win32Window(Window):
    """A layered pop-up window. Created hidden; the first `present()` shows it.

    Capture: while a mouse button is held the window captures the mouse, so a drag keeps delivering
    `on_motion` outside the window. If something else takes the capture away mid-drag, the held buttons
    are reported as released so the service never waits for a button-up that cannot come."""

    def __init__(self, backend, title, width, height, x, y, click_through=False):
        self._backend = backend
        self._api = backend.api
        self.hwnd = None
        self._dib = None
        self._shown = False
        self._closing = False
        self._failed = False
        self._own_moves = 0     # >0 while this class itself moves or resizes the window
        self._held = set()
        self._last = (0, 0)
        ex_style = WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        if click_through:
            ex_style |= WS_EX_TRANSPARENT
        hwnd = self._api.user32.CreateWindowExW(ex_style, backend.window_class, title, WS_POPUP, x, y,
                                                width, height, None, None, backend.hinstance, None)
        if not hwnd:
            raise OSError(f'CreateWindowExW failed (error {last_error()})')
        self.hwnd = hwnd
        backend.attach(hwnd, self._message)

    def present(self, surface):
        if self.hwnd is None:
            return
        width, height = surface.get_width(), surface.get_height()
        if width < 1 or height < 1:
            return
        surface.flush()
        if self._dib is None or (self._dib.width, self._dib.height) != (width, height):
            dib = Dib(self._api, width, height)
            if self._dib is not None:
                self._dib.close()
            self._dib = dib
        self._dib.fill(surface.get_data(), surface.get_stride())
        user32 = self._api.user32
        size = wt.SIZE(width, height)
        origin = wt.POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        self._own_moves += 1
        try:
            # no destination point: the window stays where it is, only its size follows the surface
            ok = user32.UpdateLayeredWindow(self.hwnd, None, None, ctypes.byref(size), self._dib.dc,
                                            ctypes.byref(origin), 0, ctypes.byref(blend), ULW_ALPHA)
            if not ok and not self._failed:
                self._failed = True
                log(f'UpdateLayeredWindow failed (error {last_error()})')
            if ok and not self._shown:
                self._shown = True
                user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
                user32.SetWindowPos(self.hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                                    SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)
        finally:
            self._own_moves -= 1

    def move(self, x, y):
        if self.hwnd is None:
            return
        self._own_moves += 1
        try:
            self._api.user32.SetWindowPos(self.hwnd, None, x, y, 0, 0,
                                          SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        finally:
            self._own_moves -= 1

    def position(self):
        if self.hwnd is None:
            return None
        rect = wt.RECT()
        if not self._api.user32.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            return None
        return (rect.left, rect.top)

    def close(self):
        if self.hwnd is None:
            return
        self._closing = True
        if not self._api.user32.DestroyWindow(self.hwnd):
            log(f'DestroyWindow failed (error {last_error()})')

    def _call(self, callback, *args):
        if callback is not None:
            try:
                callback(*args)
            except Exception:
                self._backend.report('window callback')

    def _root(self, x, y):
        point = wt.POINT(x, y)
        if self.hwnd is not None and self._api.user32.ClientToScreen(self.hwnd, ctypes.byref(point)):
            return point.x, point.y
        return x, y

    def _message(self, msg, wparam, lparam):
        """Handles one window message; None leaves it to DefWindowProc."""
        if msg in _BUTTON_MESSAGES:
            button, pressed = _BUTTON_MESSAGES[msg]
            x, y = self._last = lparam_point(lparam)
            self._button(button, pressed, x, y)
            return 0
        if msg == WM_MOUSEMOVE:
            x, y = self._last = lparam_point(lparam)
            if self.on_motion is not None:
                self._call(self.on_motion, x, y, *self._root(x, y))
            return 0
        if msg == WM_WINDOWPOSCHANGING:
            if lparam:
                pos = WINDOWPOS.from_address(lparam)
                if not pos.flags & SWP_NOZORDER:
                    pos.hwndInsertAfter = HWND_BOTTOM   # a click, or anything else, never raises a widget
            return 0
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_CAPTURECHANGED:
            held, self._held = sorted(self._held), set()
            for button in held:
                x, y = self._last
                self._call(self.on_button, button, False, x, y, *self._root(x, y))
            return 0
        if msg == WM_MOVE:
            if not self._own_moves:
                self._call(self.on_moved, *lparam_point(lparam))
            return 0
        if msg == WM_DPICHANGED:
            self._backend.check_screen()
            return None
        if msg == WM_DESTROY:
            self.hwnd = None
            if self._dib is not None:
                self._dib.close()
                self._dib = None
            if not self._closing:
                self._call(self.on_closed)
            return 0
        return None

    def _button(self, button, pressed, x, y):
        user32 = self._api.user32
        if pressed:
            if not self._held:
                user32.SetCapture(self.hwnd)
            self._held.add(button)
        else:
            self._held.discard(button)
            if not self._held and user32.GetCapture() == self.hwnd:
                user32.ReleaseCapture()
        self._call(self.on_button, button, pressed, x, y, *self._root(x, y))


class Win32Tray:
    """A notification-area icon with a popup menu.

    A left click opens the menu only after the double-click time has passed: a double click runs the
    first item instead, and the menu's own modal loop would swallow its second click."""

    def __init__(self, backend, tooltip, icon_path, items):
        self.uid = backend.new_id()
        self._backend = backend
        self._api = backend.api
        self._tooltip = str(tooltip)[:127]
        self._items = list(items)
        self._click_timer = None
        self._skip_release = False
        self._menu_open = False
        self._closed = False
        size = self._api.user32.GetSystemMetrics(SM_CXSMICON) or 16
        self._icon, self._owns_icon = load_icon(self._api, icon_path, size)
        self.add()

    def _data(self, flags):
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._backend.control
        data.uID = self.uid
        data.uFlags = flags
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self._icon
        data.szTip = self._tooltip
        return data

    def _notify(self, command, data):
        return self._api.shell32.Shell_NotifyIconW(command, ctypes.byref(data))

    def add(self):
        """(Re-)adds the icon; called again when Explorer restarts and the taskbar is rebuilt."""
        if not self._closed and not self._notify(NIM_ADD, self._data(NIF_MESSAGE | NIF_ICON | NIF_TIP)):
            log(f'tray icon was not added (error {last_error()})')

    def notify(self, title, body):
        """A balloon (a toast on Windows 10/11) next to the icon."""
        if self._closed:
            return
        data = self._data(NIF_INFO)
        data.szInfoTitle = str(title)[:63]
        data.szInfo = str(body)[:255]
        data.dwInfoFlags = NIIF_INFO
        self._notify(NIM_MODIFY, data)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._cancel_click()
        self._notify(NIM_DELETE, self._data(0))
        if self._owns_icon and self._icon:
            self._api.user32.DestroyIcon(self._icon)
        self._backend.forget_tray(self.uid)

    def event(self, code):
        """A mouse message on the icon (`code` is the message the shell forwards)."""
        if code == WM_RBUTTONUP:
            self._cancel_click()
            self.show_menu()
        elif code == WM_LBUTTONUP:
            if self._skip_release:
                self._skip_release = False
            elif self._items:
                self._cancel_click()
                delay = self._api.user32.GetDoubleClickTime() / 1000
                self._click_timer = self._backend.call_later(delay, self.show_menu)
        elif code == WM_LBUTTONDBLCLK:
            self._cancel_click()
            self._skip_release = True   # the button-up that ends the double click is not a new click
            first = next((item for item in self._items if item is not None), None)
            if first is not None:
                self._backend.guard(first[1], 'tray item')

    def _cancel_click(self):
        if self._click_timer is not None:
            self._click_timer.cancel()
            self._click_timer = None

    def show_menu(self):
        self._click_timer = None
        if self._menu_open or self._closed or not self._items:
            return
        user32 = self._api.user32
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        command = 0
        try:
            for index, item in enumerate(self._items, 1):
                if item is None:
                    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                else:
                    user32.AppendMenuW(menu, MF_STRING, index, str(item[0]).replace('&', '&&'))
            point = wt.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            control = self._backend.control
            # MSDN: without this the menu does not close when the user clicks elsewhere; the WM_NULL
            # afterwards lets the owner's thread leave the menu state
            user32.SetForegroundWindow(control)
            self._menu_open = True
            self._backend.pump_timers(True)
            try:
                command = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                                point.x, point.y, 0, control, None)
            finally:
                self._menu_open = False
                self._backend.pump_timers(False)
            user32.PostMessageW(control, WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(menu)
        if 0 < command <= len(self._items) and self._items[command - 1] is not None:
            self._backend.guard(self._items[command - 1][1], 'tray item')


class Win32Backend(Backend):
    """The message loop, the screen and the windows. Create it, and call `run()`, on one thread."""

    def __init__(self, api=None):
        self.api = api if api is not None else load_api()   # raises RuntimeError off Windows
        self.timers = TimerQueue()
        self._calls = CallQueue()
        self._handlers = {}          # hwnd -> handler(msg, wparam, lparam) -> result or None
        self._stopping = False
        self._ids = itertools.count(1)
        self._trays = {}
        self._hotkeys = {}
        self._screen_timer = None
        self._wndproc = WNDPROC(self._dispatch)   # Windows keeps this raw pointer for the process lifetime
        self._set_dpi_aware()
        self.hinstance = self.api.kernel32.GetModuleHandleW(None)
        self.window_class = f'DeskWidgets.Window.{next(_CLASS_IDS)}'
        self._register_class()
        self.control = self._create_control_window()
        self._taskbar_created = self.api.user32.RegisterWindowMessageW('TaskbarCreated')
        self._screen = self._screen_state()

    # setup
    def _set_dpi_aware(self):
        """Physical pixels everywhere. Best available call first; any of them fails harmlessly when the
        process is already DPI aware (a manifest, or a host that set it)."""
        user32, shcore = self.api.user32, self.api.shcore
        if user32.SetProcessDpiAwarenessContext and \
                user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(PER_MONITOR_AWARE_V2)):
            return
        if shcore.SetProcessDpiAwareness and \
                shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE) == 0:
            return
        user32.SetProcessDPIAware()

    def _register_class(self):
        user32 = self.api.user32
        wndclass = WNDCLASSEXW()
        wndclass.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wndclass.lpfnWndProc = self._wndproc
        wndclass.hInstance = self.hinstance
        wndclass.hCursor = user32.LoadCursorW(None, IDC_ARROW)
        wndclass.lpszClassName = self.window_class
        if not user32.RegisterClassExW(ctypes.byref(wndclass)):
            raise OSError(f'RegisterClassExW failed (error {last_error()})')

    def _create_control_window(self):
        hwnd = self.api.user32.CreateWindowExW(WS_EX_TOOLWINDOW, self.window_class, 'DeskWidgets', WS_POPUP,
                                               0, 0, 0, 0, None, None, self.hinstance, None)
        if not hwnd:
            raise OSError(f'CreateWindowExW failed (error {last_error()})')
        self.attach(hwnd, self._control_message)
        return hwnd

    # window procedure
    def attach(self, hwnd, handler):
        self._handlers[hwnd] = handler

    def _dispatch(self, hwnd, msg, wparam, lparam):
        """The one window procedure. An exception here would print a traceback and return garbage to
        Windows, so everything is caught."""
        result = None
        handler = self._handlers.get(hwnd)
        if handler is not None:
            try:
                result = handler(msg, wparam, lparam)
            except Exception:
                self.report(f'window message {msg:#06x}')
            if msg == WM_NCDESTROY:
                self._handlers.pop(hwnd, None)
        if result is None:
            return self.api.user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        return int(result)

    def _control_message(self, msg, wparam, lparam):
        if msg == WM_APP_WAKE:
            self._flush()
            return 0
        if msg == WM_APP_QUIT:
            self.api.user32.PostQuitMessage(0)
            return 0
        if msg == WM_TIMER:
            if wparam == PUMP_TIMER:
                self._flush()
            return 0
        if msg == WM_TRAY:
            tray = self._trays.get(wparam)
            if tray is not None:
                tray.event(lparam & 0xFFFF)
            return 0
        if msg == WM_HOTKEY:
            callback = self._hotkeys.get(wparam)
            if callback is not None:
                self.guard(callback, 'hotkey')
            return 0
        if msg == WM_DISPLAYCHANGE:
            self.screen_changed()
            return None
        if msg in (WM_SETTINGCHANGE, WM_DPICHANGED):
            self.check_screen()
            return None
        if msg == WM_QUERYENDSESSION:
            return 1
        if msg == WM_ENDSESSION:
            if wparam:
                self.quit()
            return 0
        if msg == self._taskbar_created and msg:
            for tray in list(self._trays.values()):
                tray.add()
            return 0
        return None

    def report(self, where):
        log(f'{where} failed:\n{traceback.format_exc().rstrip()}')

    def guard(self, callback, where):
        """Calls `callback()`; an exception is logged, not raised."""
        try:
            callback()
        except Exception:
            self.report(where)

    # loop
    def run(self):
        user32 = self.api.user32
        msg = wt.MSG()
        try:
            while not self._stopping:
                self._flush()
                user32.MsgWaitForMultipleObjects(0, None, False, wait_ms(self.timers.next_timeout()),
                                                 QS_ALLINPUT)
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    if msg.message == WM_QUIT:
                        self._stopping = True
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                    if self._stopping:
                        break
        finally:
            self._cleanup()
            self._discard_quit_messages()
            self._stopping = False

    def _discard_quit_messages(self):
        """A quit request that was not consumed would end the next `run()` at once."""
        msg = wt.MSG()
        user32 = self.api.user32
        while user32.PeekMessageW(ctypes.byref(msg), self.control, WM_APP_QUIT, WM_APP_QUIT, PM_REMOVE):
            pass
        while user32.PeekMessageW(ctypes.byref(msg), None, WM_QUIT, WM_QUIT, PM_REMOVE):
            pass

    def _flush(self):
        for callback in self._calls.drain():
            self.guard(callback, 'callback')
        self.timers.run_due()

    def _cleanup(self):
        """The icon would otherwise stay in the notification area until the mouse passes over it."""
        for tray in list(self._trays.values()):
            tray.close()
        for hotkey_id in list(self._hotkeys):
            self.api.user32.UnregisterHotKey(self.control, hotkey_id)
        self._hotkeys.clear()

    def quit(self):
        self._stopping = True
        self._wake(WM_APP_QUIT)

    def call_soon(self, callback):
        if self._calls.put(callback):
            self._wake(WM_APP_WAKE)

    def call_later(self, seconds, callback):
        return self.timers.add(seconds, lambda: self.guard(callback, 'timer'))

    def _wake(self, message):
        """Posted to the control window rather than the thread: thread messages are dropped by the
        modal loops of menus, window messages are delivered by them."""
        self.api.user32.PostMessageW(self.control, message, 0, 0)

    def pump_timers(self, on):
        """Keeps `call_later` timers running while a menu's modal loop owns the thread."""
        if on:
            self.api.user32.SetTimer(self.control, PUMP_TIMER, PUMP_MS, None)
        else:
            self.api.user32.KillTimer(self.control, PUMP_TIMER)

    # screen
    def work_area(self):
        rect = wt.RECT()
        user32 = self.api.user32
        if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        return (0, 0, user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN))

    def dpi_scale(self):
        return min(3.0, max(1.0, self._dpi() / 96))

    def _dpi(self):
        """DPI of the primary monitor: its own current value first (it follows a change in Settings),
        the system value as the fallback."""
        user32, shcore = self.api.user32, self.api.shcore
        if shcore.GetDpiForMonitor:
            monitor = user32.MonitorFromPoint(wt.POINT(0, 0), MONITOR_DEFAULTTOPRIMARY)
            dpi_x, dpi_y = wt.UINT(), wt.UINT()
            if monitor and shcore.GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x),
                                                   ctypes.byref(dpi_y)) == 0 and dpi_x.value:
                return dpi_x.value
        if user32.GetDpiForSystem:
            dpi = user32.GetDpiForSystem()
            if dpi:
                return dpi
        dc = user32.GetDC(None)
        if not dc:
            return 96
        try:
            return self.api.gdi32.GetDeviceCaps(dc, LOGPIXELSX) or 96
        finally:
            user32.ReleaseDC(None, dc)

    def _screen_state(self):
        return (self.work_area(), self.dpi_scale())

    def screen_changed(self):
        """Schedules `on_screen_changed`; several messages in a row make one call."""
        if self._screen_timer is not None:
            self._screen_timer.cancel()
        self._screen_timer = self.call_later(SCREEN_DEBOUNCE, self._report_screen)

    def check_screen(self):
        """For messages that mostly mean something else (any setting changed): reports only a real change
        of the work area or the DPI."""
        if self._screen_state() != self._screen:
            self.screen_changed()

    def _report_screen(self):
        self._screen_timer = None
        self._screen = self._screen_state()
        if self.on_screen_changed is not None:
            self.on_screen_changed()

    # windows and desktop integration
    def create_window(self, title, width, height, x, y, click_through=False):
        return Win32Window(self, title, width, height, x, y, click_through)

    def new_id(self):
        return next(self._ids)

    def forget_tray(self, uid):
        self._trays.pop(uid, None)

    def create_tray(self, tooltip, icon_path, items):
        try:
            tray = Win32Tray(self, tooltip, icon_path, items)
        except Exception:
            self.report('tray icon')
            return None
        self._trays[tray.uid] = tray
        return tray

    def register_hotkey(self, spec, callback):
        parsed = parse_hotkey(spec)
        if parsed is None:
            log(f'hotkey {spec!r} is not understood (use e.g. ctrl+alt+w)')
            return False
        modifiers, key = parsed
        hotkey_id = self.new_id()
        if not self.api.user32.RegisterHotKey(self.control, hotkey_id, modifiers | MOD_NOREPEAT, key):
            log(f'hotkey {spec!r} was refused (error {last_error()}); another program may use it')
            return False
        self._hotkeys[hotkey_id] = callback
        return True

