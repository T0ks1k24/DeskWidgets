"""The Windows backend. Most tests run anywhere: the Win32 functions are replaced by a recording fake,
so the message handling, the loop and the tray logic are checked on Linux too. The last class talks to
the real Win32 API and runs only on Windows (or under Wine)."""
import ctypes
import itertools
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import cairo

from deskwidgets.platform import win32 as w
from deskwidgets.platform.base import LEFT, MIDDLE, RIGHT

WIN64 = ctypes.sizeof(ctypes.c_void_p) == 8


def pack(x, y):
    """MAKELPARAM for two signed 16-bit coordinates."""
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def make_api(screen=(0, 0, 1920, 1040), dpi=96):
    """A stand-in for `load_api()`: remembers where windows are, who has the mouse capture and which
    bitmaps were created, and calls back into the window procedure like Windows does."""
    api = MagicMock(name='api')
    user32, gdi32 = api.user32, api.gdi32
    api.state = state = MagicMock(name='state')
    state.hwnds = itertools.count(100)
    state.origin = {}
    state.capture = None
    state.backend = None
    state.bitmaps = []
    state.order = []
    state.screen = screen

    def create_window(ex_style, cls, title, style, x, y, cx, cy, *rest):
        hwnd = next(state.hwnds)
        state.origin[hwnd] = (x, y)
        return hwnd

    def set_pos(hwnd, after, x, y, cx, cy, flags):
        state.order.append('pos')
        if not flags & w.SWP_NOMOVE:
            state.origin[hwnd] = (x, y)
            state.backend._dispatch(hwnd, w.WM_MOVE, 0, pack(x, y))
        return 1

    def client_to_screen(hwnd, ref):
        ox, oy = state.origin[hwnd]
        ref._obj.x += ox
        ref._obj.y += oy
        return 1

    def window_rect(hwnd, ref):
        x, y = state.origin[hwnd]
        ref._obj.left, ref._obj.top, ref._obj.right, ref._obj.bottom = x, y, x + 100, y + 50
        return 1

    def set_capture(hwnd):
        state.capture = hwnd

    def release_capture():
        hwnd, state.capture = state.capture, None
        if hwnd is not None:
            state.backend._dispatch(hwnd, w.WM_CAPTURECHANGED, 0, 0)
        return 1

    def destroy_window(hwnd):
        state.backend._dispatch(hwnd, w.WM_DESTROY, 0, 0)
        state.backend._dispatch(hwnd, w.WM_NCDESTROY, 0, 0)
        return 1

    def create_dib(hdc, info, usage, bits_ref, section, offset):
        header = info._obj.bmiHeader
        buffer = ctypes.create_string_buffer(header.biWidth * -header.biHeight * 4)
        bitmap = 5000 + len(state.bitmaps)
        state.bitmaps.append((bitmap, buffer))
        bits_ref._obj.value = ctypes.addressof(buffer)
        return bitmap

    def work_area(action, param, ref, flags):
        x, y, width, height = state.screen
        ref._obj.left, ref._obj.top, ref._obj.right, ref._obj.bottom = x, y, x + width, y + height
        return 1

    def dpi_for_monitor(monitor, kind, x_ref, y_ref):
        x_ref._obj.value = y_ref._obj.value = state.dpi
        return 0

    state.dpi = dpi
    user32.CreateWindowExW.side_effect = create_window
    user32.SetWindowPos.side_effect = set_pos
    user32.ClientToScreen.side_effect = client_to_screen
    user32.GetWindowRect.side_effect = window_rect
    user32.SetCapture.side_effect = set_capture
    user32.GetCapture.side_effect = lambda: state.capture
    user32.ReleaseCapture.side_effect = release_capture
    user32.DestroyWindow.side_effect = destroy_window
    user32.SystemParametersInfoW.side_effect = work_area
    user32.UpdateLayeredWindow.side_effect = lambda *a: state.order.append('update') or 1
    user32.ShowWindow.side_effect = lambda *a: state.order.append('show') or 1
    user32.RegisterClassExW.return_value = 1
    user32.LoadCursorW.return_value = 3
    user32.RegisterWindowMessageW.return_value = 0xC123
    user32.PeekMessageW.return_value = 0
    user32.GetSystemMetrics.return_value = 16
    user32.GetDoubleClickTime.return_value = 500
    user32.DefWindowProcW.return_value = 0
    user32.SetProcessDpiAwarenessContext.return_value = 1
    user32.GetDpiForSystem = None
    api.shcore.GetDpiForMonitor = MagicMock(side_effect=dpi_for_monitor)
    api.shcore.SetProcessDpiAwareness = None
    user32.MonitorFromPoint.return_value = 1
    user32.LoadIconW.return_value = 7
    api.kernel32.GetModuleHandleW.return_value = 1
    gdi32.CreateDIBSection.side_effect = create_dib
    gdi32.CreateCompatibleDC.return_value = 900
    gdi32.CreateBitmap.return_value = 6001
    gdi32.SelectObject.return_value = 901
    api.shell32.Shell_NotifyIconW.return_value = 1
    return api


def surface(width, height, stride=None, fill=None):
    """An ARGB32 surface; with `stride` its rows are padded (Cairo allows that for data surfaces)."""
    if stride is None:
        image = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        stride = image.get_stride()
        data = image.get_data()
    else:
        data = bytearray(stride * height)
        image = cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, width, height, stride)
    for row in range(height):
        for col in range(width):
            offset = row * stride + col * 4
            pixel = bytes([(row * 16 + col) & 0xFF, (row * 16 + col + 1) & 0xFF, 0x40, 0xFF]) \
                if fill is None else bytes(fill)
            data[offset:offset + 4] = pixel
    image.mark_dirty()
    return image


class ParseHotkeyTests(unittest.TestCase):
    def test_valid_specs(self):
        cases = {
            'ctrl+alt+w': (w.MOD_CONTROL | w.MOD_ALT, ord('W')),
            'Win+Alt+W': (w.MOD_WIN | w.MOD_ALT, ord('W')),
            'ctrl+shift+f5': (w.MOD_CONTROL | w.MOD_SHIFT, 0x74),
            ' super + 7 ': (w.MOD_WIN, ord('7')),
            'control+space': (w.MOD_CONTROL, 0x20),
            'alt+F24': (w.MOD_ALT, 0x87),
            'ctrl+alt+delete': (w.MOD_CONTROL | w.MOD_ALT, 0x2E),
            'ctrl+meta+left': (w.MOD_CONTROL | w.MOD_WIN, 0x25),
        }
        for spec, expected in cases.items():
            self.assertEqual(w.parse_hotkey(spec), expected, spec)

    def test_unusable_specs(self):
        for spec in ['', 'w', 'ctrl', 'ctrl+', '+w', 'ctrl++w', 'ctrl+alt+w+e', 'ctrl+f25', 'ctrl+f0',
                     'ctrl+nonsense', 'ctrl+%', 'ctrl+é', None, 5]:
            self.assertIsNone(w.parse_hotkey(spec), repr(spec))


class LparamTests(unittest.TestCase):
    def test_signed_coordinates(self):
        for x, y in [(0, 0), (5, 7), (-1, -1), (-1920, 300), (2500, -400), (32767, -32768)]:
            self.assertEqual(w.lparam_point(pack(x, y)), (x, y))

    def test_sign_extended_lparam(self):
        # a 64-bit LPARAM built by sign-extending a 32-bit value is negative as a Python int
        self.assertEqual(w.lparam_point(-1), (-1, -1))
        self.assertEqual(w.lparam_point(ctypes.c_int32(pack(-30, 10)).value), (-30, 10))


class WaitTests(unittest.TestCase):
    def test_wait_ms(self):
        self.assertEqual(w.wait_ms(0), 0)
        self.assertEqual(w.wait_ms(0.0001), 1)
        self.assertEqual(w.wait_ms(0.0201), 21)
        self.assertEqual(w.wait_ms(3600), w.MAX_WAIT_MS)
        self.assertEqual(w.wait_ms(None), w.MAX_WAIT_MS)


class CallQueueTests(unittest.TestCase):
    def test_only_the_first_call_after_a_drain_asks_for_a_wake_up(self):
        queue = w.CallQueue()
        first, second, third = (lambda: 1), (lambda: 2), (lambda: 3)
        self.assertTrue(queue.put(first))
        self.assertFalse(queue.put(second))
        self.assertEqual(queue.drain(), [first, second])
        self.assertEqual(queue.drain(), [])
        self.assertTrue(queue.put(third))

    def test_many_threads(self):
        queue = w.CallQueue()
        wakes = []

        def producer(n):
            for i in range(200):
                if queue.put((n, i)):
                    wakes.append(1)

        threads = [threading.Thread(target=producer, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        items = queue.drain()
        self.assertEqual(len(items), 800)
        for n in range(4):   # order inside one producer is kept
            self.assertEqual([i for who, i in items if who == n], list(range(200)))
        self.assertEqual(len(wakes), 1)


class CopyRowsTests(unittest.TestCase):
    def test_padded_source_rows(self):
        width, height, stride = 3, 4, 16   # 12 bytes of pixels, 4 bytes of padding per row
        src = bytearray(range(stride * height))
        dst = ctypes.create_string_buffer(width * 4 * height)
        w.copy_rows(ctypes.addressof(dst), width * 4, src, stride, width * 4, height)
        expected = b''.join(bytes(src[r * stride:r * stride + 12]) for r in range(height))
        self.assertEqual(dst.raw, expected)

    def test_padded_destination_rows_and_untouched_padding(self):
        src = bytes(range(24))
        dst = ctypes.create_string_buffer(b'\xEE' * 40)
        w.copy_rows(ctypes.addressof(dst), 20, src, 12, 12, 2)
        self.assertEqual(dst.raw[0:12], src[0:12])
        self.assertEqual(dst.raw[12:20], b'\xEE' * 8)
        self.assertEqual(dst.raw[20:32], src[12:24])
        self.assertEqual(dst.raw[32:40], b'\xEE' * 8)

    def test_packed_layout_is_one_copy(self):
        src = bytes(range(48))
        dst = ctypes.create_string_buffer(48)
        with patch.object(w.ctypes, 'memmove', wraps=ctypes.memmove) as memmove:
            w.copy_rows(ctypes.addressof(dst), 12, src, 12, 12, 4)
        self.assertEqual(memmove.call_count, 1)
        self.assertEqual(dst.raw, src)

    def test_read_only_and_cairo_buffers(self):
        image = surface(3, 2)
        dst = ctypes.create_string_buffer(24)
        w.copy_rows(ctypes.addressof(dst), 12, image.get_data(), image.get_stride(), 12, 2)
        self.assertEqual(dst.raw, bytes(image.get_data()))
        w.copy_rows(ctypes.addressof(dst), 12, bytes(range(24)), 12, 12, 2)
        self.assertEqual(dst.raw, bytes(range(24)))

    def test_refuses_what_would_read_out_of_bounds(self):
        dst = ctypes.create_string_buffer(64)
        with self.assertRaises(ValueError):
            w.copy_rows(ctypes.addressof(dst), 12, bytes(20), 12, 12, 2)
        with self.assertRaises(ValueError):
            w.copy_rows(ctypes.addressof(dst), 12, bytes(64), 8, 12, 2)

    def test_empty_image_is_a_no_op(self):
        w.copy_rows(0, 0, b'', 0, 0, 0)


class StructTests(unittest.TestCase):
    @unittest.skipUnless(WIN64, '64-bit layouts')
    def test_layouts_match_the_windows_headers(self):
        self.assertEqual(ctypes.sizeof(w.BLENDFUNCTION), 4)
        self.assertEqual(ctypes.sizeof(w.BITMAPINFOHEADER), 40)
        self.assertEqual(ctypes.sizeof(w.ICONINFO), 32)
        self.assertEqual(ctypes.sizeof(w.WINDOWPOS), 40)
        self.assertEqual(ctypes.sizeof(w.WNDCLASSEXW), 80)

    @unittest.skipUnless(sys.platform == 'win32' and WIN64, 'wchar_t is 2 bytes only on Windows')
    def test_notifyicondata_size(self):
        self.assertEqual(ctypes.sizeof(w.NOTIFYICONDATAW), 976)


class FakeWindowsTests(unittest.TestCase):
    """Backend, windows and tray driven through the fake API."""

    def setUp(self):
        self.api = make_api()
        self.backend = w.Win32Backend(self.api)
        self.api.state.backend = self.backend
        self.user32 = self.api.user32
        log = patch('deskwidgets.platform.win32.log')
        self.log = log.start()
        self.addCleanup(log.stop)

    def window(self, **kwargs):
        window = self.backend.create_window('Test', 100, 50, 10, 20, **kwargs)
        window.events = []
        window.on_button = lambda *a: window.events.append(('button',) + a)
        window.on_motion = lambda *a: window.events.append(('motion',) + a)
        window.on_moved = lambda *a: window.events.append(('moved',) + a)
        window.on_closed = lambda *a: window.events.append(('closed',) + a)
        return window

    def send(self, window, msg, wparam=0, lparam=0):
        return self.backend._dispatch(window.hwnd, msg, wparam, lparam)

    # construction
    def test_off_windows_the_real_backend_refuses(self):
        if sys.platform == 'win32':
            self.skipTest('on Windows it works')
        with self.assertRaisesRegex(RuntimeError, 'Windows only'):
            w.Win32Backend()

    def test_dpi_awareness_prefers_per_monitor_v2(self):
        call = self.user32.SetProcessDpiAwarenessContext.call_args[0][0]
        self.assertEqual(call.value & 0xFF, 0xFC)   # the handle value -4
        self.user32.SetProcessDPIAware.assert_not_called()

    def test_dpi_awareness_falls_back(self):
        api = make_api()
        api.user32.SetProcessDpiAwarenessContext = None
        api.shcore.SetProcessDpiAwareness = MagicMock(return_value=0)
        w.Win32Backend(api)
        api.shcore.SetProcessDpiAwareness.assert_called_once_with(2)
        api.user32.SetProcessDPIAware.assert_not_called()
        api = make_api()
        api.user32.SetProcessDpiAwarenessContext = None
        w.Win32Backend(api)
        api.user32.SetProcessDPIAware.assert_called_once_with()

    def test_window_styles(self):
        self.window()
        ex_style, _cls, title, style = self.user32.CreateWindowExW.call_args_list[-1][0][:4]
        self.assertEqual(ex_style, w.WS_EX_LAYERED | w.WS_EX_TOOLWINDOW | w.WS_EX_NOACTIVATE)
        self.assertEqual((title, style), ('Test', w.WS_POPUP))
        self.window(click_through=True)
        ex_style = self.user32.CreateWindowExW.call_args_list[-1][0][0]
        self.assertTrue(ex_style & w.WS_EX_TRANSPARENT)

    def test_two_backends_use_different_window_classes(self):
        other = w.Win32Backend(make_api())
        self.assertNotEqual(self.backend.window_class, other.window_class)

    # screen
    def test_work_area_and_scale(self):
        self.api.state.screen = (-1920, 0, 1920, 1040)
        self.assertEqual(self.backend.work_area(), (-1920, 0, 1920, 1040))
        for dpi, scale in [(96, 1.0), (120, 1.25), (144, 1.5), (384, 3.0), (48, 1.0)]:
            self.api.state.dpi = dpi
            self.assertEqual(self.backend.dpi_scale(), scale)

    def test_scale_falls_back_to_system_dpi_then_device_caps(self):
        self.api.shcore.GetDpiForMonitor = None
        self.user32.GetDpiForSystem = MagicMock(return_value=144)
        self.assertEqual(self.backend.dpi_scale(), 1.5)
        self.user32.GetDpiForSystem = None
        self.user32.GetDC.return_value = 77
        self.api.gdi32.GetDeviceCaps.return_value = 192
        self.assertEqual(self.backend.dpi_scale(), 2.0)
        self.user32.ReleaseDC.assert_called_once_with(None, 77)

    def test_screen_changes_are_debounced_into_one_call(self):
        calls = []
        self.backend.on_screen_changed = lambda: calls.append(1)
        with patch.object(w, 'SCREEN_DEBOUNCE', 0.05):
            for _ in range(5):
                self.backend._dispatch(self.backend.control, w.WM_DISPLAYCHANGE, 32, pack(2560, 1440))
            self.backend.timers.run_due()
            self.assertEqual(calls, [])
            time.sleep(0.08)
            self.backend.timers.run_due()
        self.assertEqual(calls, [1])

    def test_setting_change_reports_only_real_changes(self):
        calls = []
        self.backend.on_screen_changed = lambda: calls.append(1)
        with patch.object(w, 'SCREEN_DEBOUNCE', 0):
            self.backend._dispatch(self.backend.control, w.WM_SETTINGCHANGE, 0, 0)
            self.backend.timers.run_due()
            self.assertEqual(calls, [])
            self.api.state.screen = (0, 0, 1920, 1000)   # the taskbar grew
            self.backend._dispatch(self.backend.control, w.WM_SETTINGCHANGE, w.SPI_GETWORKAREA, 0)
            self.backend.timers.run_due()
            self.assertEqual(calls, [1])
            self.backend._dispatch(self.backend.control, w.WM_SETTINGCHANGE, 0, 0)
            self.backend.timers.run_due()
            self.assertEqual(calls, [1])

    def test_screen_change_without_a_callback_is_harmless(self):
        with patch.object(w, 'SCREEN_DEBOUNCE', 0):
            self.backend._dispatch(self.backend.control, w.WM_DISPLAYCHANGE, 32, 0)
            self.backend.timers.run_due()
        self.log.assert_not_called()

    def test_dpi_change_of_a_widget_is_checked_not_assumed(self):
        calls = []
        self.backend.on_screen_changed = lambda: calls.append(1)
        window = self.window()
        with patch.object(w, 'SCREEN_DEBOUNCE', 0):
            self.send(window, w.WM_DPICHANGED, 0x00C000C0, 0)   # dragged to another monitor, same primary DPI
            self.backend.timers.run_due()
            self.assertEqual(calls, [])
            self.api.state.dpi = 144
            self.send(window, w.WM_DPICHANGED, 0x00900090, 0)
            self.backend.timers.run_due()
        self.assertEqual(calls, [1])

    def test_end_session(self):
        self.assertEqual(self.backend._dispatch(self.backend.control, w.WM_QUERYENDSESSION, 0, 0), 1)
        self.backend._dispatch(self.backend.control, w.WM_ENDSESSION, 0, 0)   # cancelled: keep running
        self.assertFalse(self.backend._stopping)
        self.backend._dispatch(self.backend.control, w.WM_ENDSESSION, 1, 0)
        self.assertTrue(self.backend._stopping)

    # mouse
    def test_buttons_and_motion_report_window_and_screen_coordinates(self):
        window = self.window()
        self.send(window, w.WM_LBUTTONDOWN, 1, pack(5, 7))
        self.send(window, w.WM_MOUSEMOVE, 1, pack(9, 8))
        self.send(window, w.WM_LBUTTONUP, 0, pack(9, 8))
        self.send(window, w.WM_RBUTTONDOWN, 2, pack(1, 2))
        self.send(window, w.WM_RBUTTONUP, 0, pack(1, 2))
        self.send(window, w.WM_MBUTTONDOWN, 16, pack(3, 4))
        self.send(window, w.WM_MBUTTONUP, 0, pack(3, 4))
        self.assertEqual(window.events, [
            ('button', LEFT, True, 5, 7, 15, 27),
            ('motion', 9, 8, 19, 28),
            ('button', LEFT, False, 9, 8, 19, 28),
            ('button', RIGHT, True, 1, 2, 11, 22),
            ('button', RIGHT, False, 1, 2, 11, 22),
            ('button', MIDDLE, True, 3, 4, 13, 24),
            ('button', MIDDLE, False, 3, 4, 13, 24),
        ])

    def test_negative_coordinates_while_dragging_outside_the_window(self):
        window = self.window()
        self.api.state.origin[window.hwnd] = (-1920, -300)   # a monitor left of and above the primary one
        self.send(window, w.WM_LBUTTONDOWN, 1, pack(5, 7))
        self.send(window, w.WM_MOUSEMOVE, 1, pack(-40, -12))
        self.assertEqual(window.events[-1], ('motion', -40, -12, -1960, -312))

    def test_capture_is_held_from_the_first_press_to_the_last_release(self):
        window = self.window()
        self.send(window, w.WM_LBUTTONDOWN, 1, pack(5, 7))
        self.assertEqual(self.api.state.capture, window.hwnd)
        self.send(window, w.WM_RBUTTONDOWN, 3, pack(5, 7))
        self.user32.SetCapture.assert_called_once_with(window.hwnd)
        self.send(window, w.WM_LBUTTONUP, 2, pack(5, 7))
        self.assertEqual(self.api.state.capture, window.hwnd)
        self.send(window, w.WM_RBUTTONUP, 0, pack(5, 7))
        self.assertIsNone(self.api.state.capture)
        self.assertEqual([e[0:3] for e in window.events if e[0] == 'button'],
                         [('button', LEFT, True), ('button', RIGHT, True),
                          ('button', LEFT, False), ('button', RIGHT, False)])

    def test_stolen_capture_releases_held_buttons(self):
        window = self.window()
        self.send(window, w.WM_LBUTTONDOWN, 1, pack(5, 7))
        self.send(window, w.WM_MOUSEMOVE, 1, pack(6, 8))
        window.events.clear()
        self.api.state.capture = None
        self.send(window, w.WM_CAPTURECHANGED, 0, 0)
        self.assertEqual(window.events, [('button', LEFT, False, 6, 8, 16, 28)])
        self.send(window, w.WM_CAPTURECHANGED, 0, 0)
        self.assertEqual(len(window.events), 1)

    def test_clicking_never_activates_the_window(self):
        window = self.window()
        self.assertEqual(self.send(window, w.WM_MOUSEACTIVATE, 0, 0), w.MA_NOACTIVATE)

    def test_missing_callbacks_are_fine(self):
        window = self.backend.create_window('Test', 10, 10, 0, 0)
        for msg in (w.WM_LBUTTONDOWN, w.WM_MOUSEMOVE, w.WM_LBUTTONUP, w.WM_MOVE, w.WM_DESTROY):
            self.send(window, msg, 0, pack(1, 1))
        self.log.assert_not_called()

    def test_exceptions_in_callbacks_are_logged_not_raised(self):
        window = self.window()
        window.on_motion = lambda *a: 1 / 0
        self.assertEqual(self.send(window, w.WM_MOUSEMOVE, 0, pack(1, 1)), 0)
        self.assertIn('ZeroDivisionError', self.log.call_args[0][0])

    def test_exceptions_in_the_window_procedure_fall_back_to_defwindowproc(self):
        window = self.window()
        self.backend._handlers[window.hwnd] = lambda *a: 1 / 0
        self.user32.DefWindowProcW.return_value = 42
        self.assertEqual(self.send(window, w.WM_USER if hasattr(w, 'WM_USER') else 0x400, 1, 2), 42)
        self.assertIn('ZeroDivisionError', self.log.call_args[0][0])

    # position, z-order, closing
    def test_move_is_cheap_and_silent_but_outside_moves_are_reported(self):
        window = self.window()
        window.move(300, 400)
        flags = self.user32.SetWindowPos.call_args[0][6]
        self.assertEqual(flags, w.SWP_NOSIZE | w.SWP_NOZORDER | w.SWP_NOACTIVATE)
        self.assertEqual(window.events, [])
        self.assertEqual(window.position(), (300, 400))
        self.send(window, w.WM_MOVE, 0, pack(-50, 60))
        self.assertEqual(window.events, [('moved', -50, 60)])

    def test_window_pos_changing_keeps_the_window_at_the_bottom(self):
        window = self.window()
        pos = w.WINDOWPOS(window.hwnd, 12345, 0, 0, 10, 10, w.SWP_NOACTIVATE)
        self.assertEqual(self.send(window, w.WM_WINDOWPOSCHANGING, 0, ctypes.addressof(pos)), 0)
        self.assertEqual(pos.hwndInsertAfter, w.HWND_BOTTOM)
        pos = w.WINDOWPOS(window.hwnd, 12345, 0, 0, 10, 10, w.SWP_NOZORDER)
        self.send(window, w.WM_WINDOWPOSCHANGING, 0, ctypes.addressof(pos))
        self.assertEqual(pos.hwndInsertAfter, 12345)

    def test_own_close_is_silent_but_destruction_from_outside_is_reported(self):
        window = self.window()
        window.close()
        window.close()
        self.assertEqual(window.events, [])
        self.assertIsNone(window.position())
        self.assertEqual(self.user32.DestroyWindow.call_count, 1)
        window.move(1, 1)
        window.present(surface(2, 2))
        self.user32.UpdateLayeredWindow.assert_not_called()
        other = self.window()
        self.send(other, w.WM_DESTROY, 0, 0)
        self.assertEqual(other.events, [('closed',)])
        other.close()   # nothing left to destroy

    def test_handler_is_removed_with_the_window(self):
        window = self.window()
        hwnd = window.hwnd
        window.close()
        self.assertNotIn(hwnd, self.backend._handlers)

    # presenting
    def test_first_present_updates_then_shows_without_activating_then_sends_to_bottom(self):
        window = self.window()
        state = self.api.state
        window.present(surface(4, 3))
        self.assertEqual(state.order, ['update', 'show', 'pos'])
        self.user32.ShowWindow.assert_called_once_with(window.hwnd, w.SW_SHOWNOACTIVATE)
        args = self.user32.SetWindowPos.call_args[0]
        self.assertEqual(args, (window.hwnd, w.HWND_BOTTOM, 0, 0, 0, 0,
                                w.SWP_NOACTIVATE | w.SWP_NOMOVE | w.SWP_NOSIZE))
        update = self.user32.UpdateLayeredWindow.call_args[0]
        self.assertEqual((update[0], update[1], update[2]), (window.hwnd, None, None))
        self.assertEqual((update[3]._obj.cx, update[3]._obj.cy), (4, 3))
        self.assertEqual((update[5]._obj.x, update[5]._obj.y), (0, 0))
        self.assertEqual(update[6], 0)
        blend = update[7]._obj
        self.assertEqual((blend.BlendOp, blend.SourceConstantAlpha, blend.AlphaFormat),
                         (w.AC_SRC_OVER, 255, w.AC_SRC_ALPHA))
        self.assertEqual(update[8], w.ULW_ALPHA)
        del state.order[:]
        window.present(surface(4, 3))
        self.assertEqual(state.order, ['update'])
        self.assertEqual(window.events, [])   # resizing and showing are our own doing

    def test_dib_is_top_down_32bpp_and_reused_until_the_size_changes(self):
        window = self.window()
        window.present(surface(4, 3))
        window.present(surface(4, 3))
        self.assertEqual(self.api.gdi32.CreateDIBSection.call_count, 1)
        header = self.api.gdi32.CreateDIBSection.call_args[0][1]._obj.bmiHeader
        self.assertEqual((header.biWidth, header.biHeight, header.biBitCount, header.biCompression),
                         (4, -3, 32, w.BI_RGB))
        window.present(surface(6, 2))
        self.assertEqual(self.api.gdi32.CreateDIBSection.call_count, 2)
        self.assertEqual(self.api.gdi32.DeleteObject.call_count, 1)   # the old bitmap
        self.assertEqual(self.api.gdi32.DeleteDC.call_count, 1)
        update = self.user32.UpdateLayeredWindow.call_args[0]
        self.assertEqual((update[3]._obj.cx, update[3]._obj.cy), (6, 2))
        self.assertEqual(update[4], 900)   # the memory DC holding the bitmap

    def test_pixels_are_copied_row_by_row_respecting_the_cairo_stride(self):
        window = self.window()
        image = surface(3, 2, stride=16)
        window.present(image)
        _bitmap, buffer = self.api.state.bitmaps[-1]
        data = bytes(image.get_data())
        self.assertEqual(buffer.raw, data[0:12] + data[16:28])
        self.assertEqual(buffer.raw[0:4], bytes([0, 1, 0x40, 0xFF]))

    def test_destroying_the_window_frees_the_bitmap(self):
        window = self.window()
        window.present(surface(2, 2))
        window.close()
        self.api.gdi32.DeleteObject.assert_called_once()
        self.api.gdi32.DeleteDC.assert_called_once()

    def test_failed_update_is_logged_once_and_the_window_stays_hidden(self):
        window = self.window()
        self.user32.UpdateLayeredWindow.side_effect = lambda *a: 0
        window.present(surface(2, 2))
        window.present(surface(2, 2))
        self.assertEqual(self.log.call_count, 1)
        self.user32.ShowWindow.assert_not_called()

    def test_empty_surface_is_ignored(self):
        window = self.window()
        window.present(MagicMock(get_width=lambda: 0, get_height=lambda: 5))
        self.user32.UpdateLayeredWindow.assert_not_called()

    # loop
    def loop_fakes(self):
        wake = threading.Event()

        def wait(count, handles, all_, ms, mask):
            wake.wait(ms / 1000)
            wake.clear()
            return 0

        def post(hwnd, msg, wparam, lparam):
            wake.set()
            return 1

        self.user32.MsgWaitForMultipleObjects.side_effect = wait
        self.user32.PostMessageW.side_effect = post
        watchdog = threading.Timer(10, self.backend.quit)
        watchdog.daemon = True
        watchdog.start()
        self.addCleanup(watchdog.cancel)

    def test_loop_runs_call_soon_from_threads_timers_and_quits(self):
        self.loop_fakes()
        order = []
        loop_thread = threading.get_ident()
        self.backend.call_later(0.05, lambda: order.append(('timer', threading.get_ident())))
        late = self.backend.call_later(0.02, lambda: order.append('cancelled'))
        late.cancel()
        threading.Timer(0.02, lambda: self.backend.call_soon(
            lambda: order.append(('soon', threading.get_ident())))).start()
        self.backend.call_later(0.2, self.backend.quit)
        started = time.monotonic()
        self.backend.run()
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(order, [('soon', loop_thread), ('timer', loop_thread)])

    def test_quit_from_another_thread_wakes_the_loop(self):
        self.loop_fakes()
        threading.Timer(0.05, self.backend.quit).start()
        started = time.monotonic()
        self.backend.run()
        self.assertLess(time.monotonic() - started, 2)
        self.user32.PostMessageW.assert_any_call(self.backend.control, w.WM_APP_QUIT, 0, 0)

    def test_quit_message_calls_post_quit_message_on_the_loop_thread(self):
        self.backend._dispatch(self.backend.control, w.WM_APP_QUIT, 0, 0)
        self.user32.PostQuitMessage.assert_called_once_with(0)

    def test_wm_quit_ends_the_loop(self):
        messages = iter([True, False])

        def peek(ref, hwnd, low, high, flags):
            if next(messages, False):
                ref._obj.message = w.WM_QUIT
                return 1
            return 0

        self.user32.PeekMessageW.side_effect = peek
        self.user32.MsgWaitForMultipleObjects.return_value = 0
        self.backend.run()
        self.assertFalse(self.backend._stopping)   # consumed: the next run() starts normally

    def test_messages_are_translated_and_dispatched(self):
        calls = iter([1, 0])

        def peek(ref, hwnd, low, high, flags):
            ref._obj.message = 0x400
            return next(calls, 0)

        self.user32.PeekMessageW.side_effect = peek
        self.user32.DispatchMessageW.side_effect = lambda ref: self.backend.quit()
        self.user32.MsgWaitForMultipleObjects.return_value = 0
        self.backend.run()
        self.user32.TranslateMessage.assert_called_once()
        self.user32.DispatchMessageW.assert_called_once()

    def test_quit_before_run_returns_at_once(self):
        self.backend.quit()
        self.backend.run()
        self.user32.MsgWaitForMultipleObjects.assert_not_called()

    def test_a_finished_loop_can_run_again_and_forgets_unconsumed_quit_messages(self):
        self.loop_fakes()
        self.backend.call_later(0.01, self.backend.quit)
        self.backend.run()
        filters = [c[0][1:4] for c in self.user32.PeekMessageW.call_args_list[-2:]]
        self.assertEqual(filters, [(self.backend.control, w.WM_APP_QUIT, w.WM_APP_QUIT),
                                   (None, w.WM_QUIT, w.WM_QUIT)])
        ran = []
        self.backend.call_later(0.01, lambda: (ran.append(1), self.backend.quit()))
        self.backend.run()
        self.assertEqual(ran, [1])

    def test_loop_survives_failing_callbacks(self):
        self.loop_fakes()
        ran = []
        self.backend.call_soon(lambda: 1 / 0)
        self.backend.call_later(0, lambda: 1 / 0)
        self.backend.call_soon(lambda: ran.append('soon'))
        self.backend.call_later(0.03, lambda: (ran.append('timer'), self.backend.quit()))
        self.backend.run()
        self.assertEqual(ran, ['soon', 'timer'])
        self.assertEqual(sum('ZeroDivisionError' in c[0][0] for c in self.log.call_args_list), 2)

    def test_wake_up_message_runs_queued_calls_inside_a_modal_loop(self):
        ran = []
        self.backend.call_soon(lambda: ran.append(1))
        self.backend._dispatch(self.backend.control, w.WM_APP_WAKE, 0, 0)
        self.assertEqual(ran, [1])
        self.backend.call_later(0, lambda: ran.append(2))
        self.backend._dispatch(self.backend.control, w.WM_TIMER, w.PUMP_TIMER, 0)
        self.assertEqual(ran, [1, 2])

    def test_wait_is_bounded_by_the_next_timer(self):
        waits = []
        self.user32.MsgWaitForMultipleObjects.side_effect = \
            lambda *a: waits.append(a[3]) or self.backend.quit() or 0
        self.backend.call_later(0.1, lambda: None)
        self.backend.run()
        self.assertTrue(0 < waits[0] <= 100)

    # hotkeys
    def test_hotkey_registration_and_dispatch(self):
        self.user32.RegisterHotKey.return_value = 1
        pressed = []
        self.assertTrue(self.backend.register_hotkey('ctrl+alt+w', lambda: pressed.append(1)))
        hwnd, hotkey_id, modifiers, key = self.user32.RegisterHotKey.call_args[0]
        self.assertEqual((hwnd, modifiers, key),
                         (self.backend.control, w.MOD_CONTROL | w.MOD_ALT | w.MOD_NOREPEAT, ord('W')))
        self.backend._dispatch(self.backend.control, w.WM_HOTKEY, hotkey_id, 0)
        self.assertEqual(pressed, [1])
        self.backend._dispatch(self.backend.control, w.WM_HOTKEY, hotkey_id + 99, 0)
        self.assertEqual(pressed, [1])

    def test_hotkeys_get_distinct_ids(self):
        self.user32.RegisterHotKey.return_value = 1
        self.backend.register_hotkey('ctrl+alt+w', lambda: None)
        self.backend.register_hotkey('ctrl+alt+g', lambda: None)
        ids = [c[0][1] for c in self.user32.RegisterHotKey.call_args_list]
        self.assertEqual(len(set(ids)), 2)

    def test_hotkey_refused_by_windows_or_not_parsable(self):
        self.user32.RegisterHotKey.return_value = 0
        self.assertFalse(self.backend.register_hotkey('ctrl+alt+w', lambda: None))
        self.assertFalse(self.backend.register_hotkey('nonsense', lambda: None))
        self.assertFalse(self.backend._hotkeys)
        self.assertEqual(self.user32.RegisterHotKey.call_count, 1)

    def test_failing_hotkey_callback_is_logged(self):
        self.user32.RegisterHotKey.return_value = 1
        self.backend.register_hotkey('ctrl+alt+w', lambda: 1 / 0)
        hotkey_id = self.user32.RegisterHotKey.call_args[0][1]
        self.backend._dispatch(self.backend.control, w.WM_HOTKEY, hotkey_id, 0)
        self.assertIn('ZeroDivisionError', self.log.call_args[0][0])

    # tray
    def tray(self, items=None):
        self.calls = []
        items = items if items is not None else [('Open', lambda: self.calls.append('open')), None,
                                                 ('Quit & close', lambda: self.calls.append('quit'))]
        return self.backend.create_tray('DeskWidgets tooltip', None, items)

    def notify_calls(self):
        return [(c[0][0], c[0][1]._obj) for c in self.api.shell32.Shell_NotifyIconW.call_args_list]

    def test_tray_icon_is_added_with_callback_message_icon_and_tooltip(self):
        tray = self.tray()
        (command, data), = self.notify_calls()
        self.assertEqual(command, w.NIM_ADD)
        self.assertEqual((data.hWnd, data.uID, data.uCallbackMessage),
                         (self.backend.control, tray.uid, w.WM_TRAY))
        self.assertEqual(data.uFlags, w.NIF_MESSAGE | w.NIF_ICON | w.NIF_TIP)
        self.assertEqual((data.szTip, data.hIcon), ('DeskWidgets tooltip', 7))   # 7: the stock icon
        self.assertEqual(data.cbSize, ctypes.sizeof(w.NOTIFYICONDATAW))

    def test_a_missing_icon_file_falls_back_to_the_stock_icon(self):
        self.user32.LoadImageW.return_value = None
        self.tray_with_icon('/no/such/icon.ico')
        self.tray_with_icon('/no/such/icon.png')
        self.user32.DestroyIcon.assert_not_called()

    def tray_with_icon(self, path):
        tray = self.backend.create_tray('t', path, [])
        data = self.notify_calls()[-1][1]
        self.assertEqual(data.hIcon, 7)
        tray.close()
        return tray

    def test_ico_files_are_loaded_by_windows_and_destroyed_with_the_tray(self):
        self.user32.LoadImageW.return_value = 55
        tray = self.backend.create_tray('t', 'C:/icons/app.ico', [])
        args = self.user32.LoadImageW.call_args[0]
        self.assertEqual((args[1], args[2], args[5]), ('C:/icons/app.ico', w.IMAGE_ICON, w.LR_LOADFROMFILE))
        self.assertEqual(self.notify_calls()[-1][1].hIcon, 55)
        tray.close()
        self.user32.DestroyIcon.assert_called_once_with(55)

    def test_png_icon_is_built_from_a_cairo_surface(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'icon.png')
            surface(8, 4, fill=(0, 0, 255, 255)).write_to_png(path)
            self.api.user32.CreateIconIndirect.return_value = 66
            icon = w.icon_from_png(self.api, path, 16)
        self.assertEqual(icon, 66)
        info = self.api.user32.CreateIconIndirect.call_args[0][0]._obj
        self.assertTrue(info.fIcon)
        _bitmap, buffer = self.api.state.bitmaps[-1]
        pixels = buffer.raw
        self.assertEqual(len(pixels), 16 * 16 * 4)
        centre = pixels[(8 * 16 + 8) * 4:(8 * 16 + 8) * 4 + 4]
        self.assertEqual(centre, bytes([0, 0, 255, 255]))
        corner = pixels[0:4]   # the 8x4 image is letterboxed: the top rows stay transparent
        self.assertEqual(corner, bytes(4))
        self.assertEqual(self.api.gdi32.DeleteObject.call_count, 2)   # CreateIconIndirect copies both

    def test_unreadable_png_gives_none(self):
        self.assertIsNone(w.icon_from_png(self.api, '/no/such/icon.png', 16))

    def test_notify_balloon(self):
        tray = self.tray()
        tray.notify('Title' * 20, 'Body' * 100)
        command, data = self.notify_calls()[-1]
        self.assertEqual((command, data.uFlags), (w.NIM_MODIFY, w.NIF_INFO))
        self.assertEqual(len(data.szInfoTitle), 63)
        self.assertEqual(len(data.szInfo), 255)
        self.assertEqual(data.dwInfoFlags, w.NIIF_INFO)

    def test_close_removes_the_icon_once(self):
        tray = self.tray()
        tray.close()
        tray.close()
        tray.notify('a', 'b')
        commands = [c for c, _ in self.notify_calls()]
        self.assertEqual(commands, [w.NIM_ADD, w.NIM_DELETE])
        self.assertNotIn(tray.uid, self.backend._trays)

    def test_icon_is_added_again_when_explorer_restarts(self):
        self.tray()
        self.backend._dispatch(self.backend.control, 0xC123, 0, 0)
        self.assertEqual([c for c, _ in self.notify_calls()], [w.NIM_ADD, w.NIM_ADD])

    def test_failed_add_is_logged_and_retried_on_taskbar_created(self):
        self.api.shell32.Shell_NotifyIconW.return_value = 0
        tray = self.tray()
        self.assertIsNotNone(tray)
        self.assertIn('not added', self.log.call_args[0][0])

    def test_right_click_shows_the_menu_and_runs_the_chosen_item(self):
        tray = self.tray()
        self.user32.TrackPopupMenu.return_value = 3
        self.user32.CreatePopupMenu.return_value = 800
        self.backend._dispatch(self.backend.control, w.WM_TRAY, tray.uid, w.WM_RBUTTONUP)
        self.assertEqual(self.calls, ['quit'])
        appended = [c[0] for c in self.user32.AppendMenuW.call_args_list]
        self.assertEqual(appended, [(800, w.MF_STRING, 1, 'Open'), (800, w.MF_SEPARATOR, 0, None),
                                    (800, w.MF_STRING, 3, 'Quit && close')])
        flags = self.user32.TrackPopupMenu.call_args[0][1]
        self.assertTrue(flags & w.TPM_RETURNCMD)
        self.user32.SetForegroundWindow.assert_called_once_with(self.backend.control)
        self.user32.PostMessageW.assert_any_call(self.backend.control, w.WM_NULL, 0, 0)
        self.user32.DestroyMenu.assert_called_once_with(800)
        self.assertEqual(self.user32.SetTimer.call_args[0][:2], (self.backend.control, w.PUMP_TIMER))
        self.user32.KillTimer.assert_called_once_with(self.backend.control, w.PUMP_TIMER)

    def test_dismissed_menu_runs_nothing(self):
        tray = self.tray()
        self.user32.TrackPopupMenu.return_value = 0
        tray.show_menu()
        self.assertEqual(self.calls, [])

    def test_menu_is_not_reentered_and_survives_errors(self):
        tray = self.tray()
        self.user32.TrackPopupMenu.side_effect = lambda *a: tray.show_menu() or 0
        tray.show_menu()
        self.assertEqual(self.user32.TrackPopupMenu.call_count, 1)
        self.user32.TrackPopupMenu.side_effect = OSError('boom')
        with self.assertRaises(OSError):
            tray.show_menu()
        self.user32.DestroyMenu.assert_called()
        self.user32.KillTimer.assert_called()

    def test_left_click_opens_the_menu_after_the_double_click_time(self):
        tray = self.tray()
        self.user32.TrackPopupMenu.return_value = 0
        self.backend._dispatch(self.backend.control, w.WM_TRAY, tray.uid, w.WM_LBUTTONUP)
        self.user32.TrackPopupMenu.assert_not_called()
        delay = self.backend.timers.next_timeout()
        self.assertTrue(0.3 < delay <= 0.5)
        with patch('time.monotonic', return_value=time.monotonic() + 1):
            self.backend.timers.run_due()
        self.user32.TrackPopupMenu.assert_called_once()

    def test_double_click_runs_the_first_item_instead_of_the_menu(self):
        tray = self.tray()
        for code in (w.WM_LBUTTONUP, w.WM_LBUTTONDBLCLK, w.WM_LBUTTONUP):
            self.backend._dispatch(self.backend.control, w.WM_TRAY, tray.uid, code)
        self.assertEqual(self.calls, ['open'])
        self.assertIsNone(self.backend.timers.next_timeout())   # no menu is pending
        self.backend._dispatch(self.backend.control, w.WM_TRAY, tray.uid, w.WM_LBUTTONUP)
        self.assertIsNotNone(self.backend.timers.next_timeout())

    def test_double_click_skips_separators_and_empty_menus(self):
        tray = self.tray(items=[None, ('Second', lambda: self.calls.append('second'))])
        tray.event(w.WM_LBUTTONDBLCLK)
        self.assertEqual(self.calls, ['second'])
        empty = self.tray(items=[])
        empty.event(w.WM_LBUTTONDBLCLK)
        empty.event(w.WM_LBUTTONUP)
        empty.event(w.WM_RBUTTONUP)
        self.user32.TrackPopupMenu.assert_not_called()

    def test_tray_of_a_stopped_loop_is_removed(self):
        tray = self.tray()
        self.backend.register_hotkey('ctrl+alt+w', lambda: None)
        self.backend.quit()
        self.backend.run()
        self.assertEqual([c for c, _ in self.notify_calls()][-1], w.NIM_DELETE)
        self.assertNotIn(tray.uid, self.backend._trays)
        self.user32.UnregisterHotKey.assert_called_once()

    def test_create_tray_failure_returns_none(self):
        self.api.shell32.Shell_NotifyIconW.side_effect = OSError('boom')
        self.assertIsNone(self.tray())


@unittest.skipUnless(sys.platform == 'win32', 'needs the real Win32 API (Windows or Wine)')
class RealWin32Tests(unittest.TestCase):
    """The real API: right argtypes (a wrong one corrupts 64-bit handles), the real message loop."""

    @classmethod
    def setUpClass(cls):
        cls.backend = w.Win32Backend()

    def post(self, window, msg, wparam, lparam):
        return self.backend.api.user32.PostMessageW(window.hwnd, msg, wparam, lparam)

    def test_screen_metrics(self):
        x, y, width, height = self.backend.work_area()
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)
        self.assertTrue(1.0 <= self.backend.dpi_scale() <= 3.0)

    def test_window_present_move_and_close(self):
        window = self.backend.create_window('DeskWidgets test', 64, 48, 100, 120)
        self.assertEqual(window.position(), (100, 120))
        image = surface(64, 48)
        window.present(image)
        window.present(image)
        window.present(surface(80, 40))
        window.move(-30, 50)   # negative: another monitor to the left
        self.assertEqual(window.position(), (-30, 50))
        window.close()
        window.close()
        self.assertIsNone(window.position())

    def test_mouse_messages_reach_the_callbacks(self):
        window = self.backend.create_window('DeskWidgets mouse', 100, 100, 200, 200)
        window.present(surface(100, 100))
        events = []
        window.on_button = lambda *a: events.append(('button',) + a)
        window.on_motion = lambda *a: events.append(('motion',) + a)
        self.post(window, w.WM_LBUTTONDOWN, 1, pack(5, 6))
        self.post(window, w.WM_MOUSEMOVE, 1, pack(-7, 8))
        self.post(window, w.WM_LBUTTONUP, 0, pack(-7, 8))
        self.backend.call_later(0.3, self.backend.quit)
        self.backend.run()
        window.close()
        self.assertEqual(events, [('button', LEFT, True, 5, 6, 205, 206),
                                  ('motion', -7, 8, 193, 208),
                                  ('button', LEFT, False, -7, 8, 193, 208)])

    def test_call_soon_from_a_thread_and_timers(self):
        order = []
        threading.Timer(0.05, lambda: self.backend.call_soon(lambda: order.append('soon'))).start()
        self.backend.call_later(0.1, lambda: order.append('timer'))
        self.backend.call_later(0.3, self.backend.quit)
        self.backend.run()
        self.assertEqual(order, ['soon', 'timer'])

    def test_hotkeys(self):
        self.assertFalse(self.backend.register_hotkey('nonsense', lambda: None))
        self.assertTrue(self.backend.register_hotkey('ctrl+alt+shift+f11', lambda: None))
        self.assertFalse(self.backend.register_hotkey('ctrl+alt+shift+f11', lambda: None))   # already taken

    def test_tray(self):
        tray = self.backend.create_tray('DeskWidgets test', 'does-not-exist.png',
                                        [('One', lambda: None), None])
        self.assertIsNotNone(tray)
        tray.notify('Title', 'Body')
        tray.close()
        tray.close()

    def test_png_icon_becomes_an_hicon(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'icon.png')
            surface(16, 16, fill=(10, 20, 200, 255)).write_to_png(path)
            icon = w.icon_from_png(self.backend.api, path, 16)
        self.assertTrue(icon)
        self.backend.api.user32.DestroyIcon(icon)

    def test_notifyicondata_matches_the_headers(self):
        self.assertEqual(ctypes.sizeof(w.NOTIFYICONDATAW), 976 if WIN64 else 956)


if __name__ == '__main__':
    unittest.main()
