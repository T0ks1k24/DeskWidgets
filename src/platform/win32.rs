//! The Windows backend: layered windows, a Win32 message loop, a tray icon and global hotkeys.
//!
//! A widget is a `WS_EX_LAYERED` pop-up whose pixels go to `UpdateLayeredWindow`: the engine's premultiplied
//! RGBA is turned into premultiplied BGRA, exactly what a top-down 32 bpp DIB with `AC_SRC_ALPHA` wants.
//! Windows are `WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE`: no taskbar button, not in Alt+Tab, never focused by a click.
//!
//! One hidden top-level window (the control window) owns everything that is not a widget: the wake-up message
//! for the `Waker`, the tray icon, the hotkey and the broadcasts about display changes. It must be a real
//! top-level window, not a message-only one: only top-level windows receive broadcasts such as
//! `WM_DISPLAYCHANGE` and `TaskbarCreated`.
//!
//! The window procedure only records what happened (`EVENTS`); the loop in `run()` hands the events to the app.
use std::cell::{Cell, RefCell};
use std::collections::{HashMap, VecDeque};
use std::ffi::c_void;
use std::path::Path;
use std::rc::Rc;
use std::sync::Arc;
use std::time::{Duration, Instant};

use tiny_skia::Pixmap;
use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::{COLORREF, HWND, LPARAM, LRESULT, POINT, RECT, SIZE, WPARAM};
use windows::Win32::Graphics::Gdi::{
    ClientToScreen, CreateBitmap, CreateCompatibleDC, CreateDIBSection, DeleteDC, DeleteObject, GetDC, GetDeviceCaps, MonitorFromPoint, ReleaseDC, SelectObject, AC_SRC_ALPHA,
    AC_SRC_OVER, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, BLENDFUNCTION, DIB_RGB_COLORS, HBITMAP, HDC, HGDIOBJ, LOGPIXELSX, MONITOR_DEFAULTTOPRIMARY,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::HiDpi::{GetDpiForMonitor, SetProcessDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, MDT_EFFECTIVE_DPI};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetCapture, GetDoubleClickTime, RegisterHotKey, ReleaseCapture, SetCapture, UnregisterHotKey, HOT_KEY_MODIFIERS, MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, MOD_WIN,
};
use windows::Win32::UI::Shell::{Shell_NotifyIconW, NIF_ICON, NIF_INFO, NIF_MESSAGE, NIF_TIP, NIIF_INFO, NIM_ADD, NIM_DELETE, NIM_MODIFY, NOTIFYICONDATAW};
use windows::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreateIconIndirect, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyIcon, DestroyMenu, DestroyWindow, DispatchMessageW, GetCursorPos,
    GetSystemMetrics, GetWindowRect, LoadCursorW, LoadIconW, MsgWaitForMultipleObjects, PeekMessageW, PostMessageW,
    PostQuitMessage, RegisterClassExW, RegisterWindowMessageW, SetForegroundWindow, SetWindowPos, ShowWindow, SystemParametersInfoW, TrackPopupMenu,
    TranslateMessage, UpdateLayeredWindow, HICON, HMENU, ICONINFO, IDC_ARROW, IDI_APPLICATION, MF_SEPARATOR, MF_STRING, MSG, PM_REMOVE,
    QS_ALLINPUT, SM_CXSCREEN, SM_CXSMICON, SM_CYSCREEN, SPI_GETWORKAREA, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SW_SHOWNOACTIVATE,
    SYSTEM_PARAMETERS_INFO_UPDATE_FLAGS, TPM_NONOTIFY, TPM_RETURNCMD, TPM_RIGHTBUTTON, ULW_ALPHA, WINDOWPOS, WM_CAPTURECHANGED, WM_DESTROY,
    WM_DISPLAYCHANGE, WM_DPICHANGED, WM_ENDSESSION, WM_ERASEBKGND, WM_HOTKEY, WM_LBUTTONDBLCLK, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MBUTTONDOWN,
    WM_MBUTTONUP, WM_MOUSEACTIVATE, WM_MOUSEMOVE, WM_MOVE, WM_NCDESTROY, WM_NULL, WM_QUERYENDSESSION, WM_QUIT, WM_RBUTTONDOWN, WM_RBUTTONUP,
    WM_SETTINGCHANGE, WM_WINDOWPOSCHANGING, WM_APP, WNDCLASSEXW, WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT, WS_POPUP,
    WINDOW_EX_STYLE, WINDOW_STYLE, HWND_BOTTOM, MA_NOACTIVATE, IMAGE_ICON, LR_LOADFROMFILE, LoadImageW, UPDATE_LAYERED_WINDOW_FLAGS,
};

use super::{App, Backend, Button, Event, Host, TimerId, TimerQueue, WakeFlag, Waker, WinId};
use crate::core::util::log;

const WM_APP_WAKE: u32 = WM_APP + 1;
const WM_APP_QUIT: u32 = WM_APP + 2;
const WM_TRAY: u32 = WM_APP + 3;
const MAX_WAIT_MS: u32 = 500;
const SCREEN_DEBOUNCE: Duration = Duration::from_millis(250);
/// Timer tokens at or above this are the backend's own (the tray's double-click timer).
const INTERNAL_TOKEN: u64 = 1 << 62;
const TOKEN_TRAY_MENU: u64 = INTERNAL_TOKEN + 1;

fn hwnd_key(hwnd: HWND) -> isize {
    hwnd.0 as isize
}

fn hwnd_from(key: isize) -> HWND {
    HWND(key as *mut c_void)
}

fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(std::iter::once(0)).collect()
}

fn copy_wide(dst: &mut [u16], text: &str) {
    let max = dst.len().saturating_sub(1);
    let mut n = 0;
    for unit in text.encode_utf16().take(max) {
        dst[n] = unit;
        n += 1;
    }
    dst[n] = 0;
}

/// (x, y) from an LPARAM holding two signed 16-bit values. Negative on a monitor to the left of or
/// above the primary one, and while the mouse is captured outside the window.
fn lparam_point(lparam: LPARAM) -> (i32, i32) {
    let v = lparam.0 as usize;
    ((v & 0xFFFF) as u16 as i16 as i32, ((v >> 16) & 0xFFFF) as u16 as i16 as i32)
}

// ---------------------------------------------------------------------------------------------
/// A top-down 32 bpp DIB selected into a memory DC: where a frame is staged for UpdateLayeredWindow.
struct Dib {
    width: u32,
    height: u32,
    bitmap: HBITMAP,
    dc: HDC,
    old: HGDIOBJ,
    bits: *mut c_void,
}

impl Dib {
    fn new(width: u32, height: u32) -> Result<Dib, String> {
        unsafe {
            let mut info = BITMAPINFO::default();
            info.bmiHeader = BITMAPINFOHEADER {
                biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: width as i32,
                biHeight: -(height as i32), // negative height = top-down
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB.0,
                ..Default::default()
            };
            let mut bits: *mut c_void = std::ptr::null_mut();
            let bitmap = CreateDIBSection(None, &info, DIB_RGB_COLORS, &mut bits, None, 0).map_err(|e| format!("CreateDIBSection({width}x{height}): {e}"))?;
            let dc = CreateCompatibleDC(None);
            if dc.is_invalid() {
                let _ = DeleteObject(bitmap.into());
                return Err("CreateCompatibleDC failed".into());
            }
            let old = SelectObject(dc, bitmap.into());
            Ok(Dib { width, height, bitmap, dc, old, bits })
        }
    }

    /// Premultiplied RGBA to the premultiplied BGRA the layered window wants.
    fn fill(&self, rgba: &[u8]) {
        let len = (self.width * self.height * 4) as usize;
        let dst = unsafe { std::slice::from_raw_parts_mut(self.bits as *mut u8, len) };
        crate::engine::canvas::rgba_to_bgra(&rgba[..len.min(rgba.len())], dst);
    }
}

impl Drop for Dib {
    fn drop(&mut self) {
        unsafe {
            SelectObject(self.dc, self.old);
            let _ = DeleteObject(self.bitmap.into());
            let _ = DeleteDC(self.dc);
        }
    }
}

struct WinState {
    id: WinId,
    hwnd: HWND,
    dib: RefCell<Option<Dib>>,
    shown: Cell<bool>,
    closing: Cell<bool>,
    failed: Cell<bool>,
    own_moves: Cell<u32>,
    held: RefCell<Vec<Button>>,
    last: Cell<(i32, i32)>,
}

thread_local! {
    static WINDOWS: RefCell<HashMap<isize, Rc<WinState>>> = RefCell::new(HashMap::new());
    static EVENTS: RefCell<VecDeque<Event>> = RefCell::new(VecDeque::new());
    static CONTROL: Cell<isize> = const { Cell::new(0) };
    static TRAY: RefCell<Option<Tray>> = const { RefCell::new(None) };
    static HOTKEY: Cell<Option<i32>> = const { Cell::new(None) };
    static WAKE: RefCell<Option<Arc<WakeFlag>>> = const { RefCell::new(None) };
    static TASKBAR_CREATED: Cell<u32> = const { Cell::new(0) };
    static SCREEN_DIRTY: Cell<bool> = const { Cell::new(false) };
    static TRAY_CLICK: Cell<bool> = const { Cell::new(false) };
}

fn push(event: Event) {
    EVENTS.with(|e| e.borrow_mut().push_back(event));
}

fn state_of(hwnd: HWND) -> Option<Rc<WinState>> {
    WINDOWS.with(|w| w.borrow().get(&hwnd_key(hwnd)).cloned())
}

fn root_point(hwnd: HWND, x: i32, y: i32) -> (i32, i32) {
    let mut p = POINT { x, y };
    if unsafe { ClientToScreen(hwnd, &mut p) }.as_bool() {
        (p.x, p.y)
    } else {
        (x, y)
    }
}

fn button_event(state: &WinState, button: Button, pressed: bool, x: i32, y: i32) {
    unsafe {
        if pressed {
            if state.held.borrow().is_empty() {
                SetCapture(state.hwnd);
            }
            let mut held = state.held.borrow_mut();
            if !held.contains(&button) {
                held.push(button);
            }
        } else {
            state.held.borrow_mut().retain(|b| *b != button);
            if state.held.borrow().is_empty() && GetCapture() == state.hwnd {
                let _ = ReleaseCapture();
            }
        }
    }
    let (rx, ry) = root_point(state.hwnd, x, y);
    push(Event::Button { win: state.id, button, pressed, x, y, root_x: rx, root_y: ry });
}

unsafe extern "system" fn window_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    let control = CONTROL.with(|c| c.get());
    if hwnd_key(hwnd) == control {
        if let Some(result) = control_message(hwnd, msg, wparam, lparam) {
            return result;
        }
        return DefWindowProcW(hwnd, msg, wparam, lparam);
    }
    if let Some(state) = state_of(hwnd) {
        if let Some(result) = widget_message(&state, msg, wparam, lparam) {
            if msg == WM_NCDESTROY {
                WINDOWS.with(|w| w.borrow_mut().remove(&hwnd_key(hwnd)));
            }
            return result;
        }
        if msg == WM_NCDESTROY {
            WINDOWS.with(|w| w.borrow_mut().remove(&hwnd_key(hwnd)));
        }
    }
    DefWindowProcW(hwnd, msg, wparam, lparam)
}

fn widget_message(state: &Rc<WinState>, msg: u32, wparam: WPARAM, lparam: LPARAM) -> Option<LRESULT> {
    let _ = wparam;
    match msg {
        WM_LBUTTONDOWN | WM_LBUTTONUP | WM_RBUTTONDOWN | WM_RBUTTONUP | WM_MBUTTONDOWN | WM_MBUTTONUP => {
            let (button, pressed) = match msg {
                WM_LBUTTONDOWN => (Button::Left, true),
                WM_LBUTTONUP => (Button::Left, false),
                WM_RBUTTONDOWN => (Button::Right, true),
                WM_RBUTTONUP => (Button::Right, false),
                WM_MBUTTONDOWN => (Button::Middle, true),
                _ => (Button::Middle, false),
            };
            let (x, y) = lparam_point(lparam);
            state.last.set((x, y));
            button_event(state, button, pressed, x, y);
            Some(LRESULT(0))
        }
        WM_MOUSEMOVE => {
            let (x, y) = lparam_point(lparam);
            state.last.set((x, y));
            let (rx, ry) = root_point(state.hwnd, x, y);
            push(Event::Motion { win: state.id, x, y, root_x: rx, root_y: ry });
            Some(LRESULT(0))
        }
        WM_WINDOWPOSCHANGING => {
            if lparam.0 != 0 {
                let pos = unsafe { &mut *(lparam.0 as *mut WINDOWPOS) };
                if pos.flags.0 & SWP_NOZORDER.0 == 0 {
                    pos.hwndInsertAfter = HWND_BOTTOM; // a click, or anything else, never raises a widget
                }
            }
            Some(LRESULT(0))
        }
        WM_MOUSEACTIVATE => Some(LRESULT(MA_NOACTIVATE as isize)),
        WM_ERASEBKGND => Some(LRESULT(1)),
        WM_CAPTURECHANGED => {
            // something else took the capture mid-drag: report the held buttons as released
            let held: Vec<Button> = std::mem::take(&mut *state.held.borrow_mut());
            let (x, y) = state.last.get();
            for button in held {
                let (rx, ry) = root_point(state.hwnd, x, y);
                push(Event::Button { win: state.id, button, pressed: false, x, y, root_x: rx, root_y: ry });
            }
            Some(LRESULT(0))
        }
        WM_MOVE => {
            if state.own_moves.get() == 0 {
                let (x, y) = lparam_point(lparam);
                push(Event::Moved { win: state.id, x, y });
            }
            Some(LRESULT(0))
        }
        WM_DESTROY => {
            *state.dib.borrow_mut() = None;
            if !state.closing.get() {
                push(Event::Closed { win: state.id });
            }
            Some(LRESULT(0))
        }
        _ => None,
    }
}

fn control_message(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> Option<LRESULT> {
    match msg {
        WM_APP_WAKE => {
            WAKE.with(|w| {
                if let Some(flag) = w.borrow().as_ref() {
                    flag.set();
                }
            });
            push(Event::Wake);
            Some(LRESULT(0))
        }
        WM_APP_QUIT => {
            unsafe { PostQuitMessage(0) };
            Some(LRESULT(0))
        }
        WM_TRAY => {
            tray_event(hwnd, (lparam.0 as u32) & 0xFFFF);
            Some(LRESULT(0))
        }
        WM_HOTKEY => {
            push(Event::Hotkey);
            Some(LRESULT(0))
        }
        WM_DISPLAYCHANGE | WM_SETTINGCHANGE | WM_DPICHANGED => {
            SCREEN_DIRTY.with(|d| d.set(true));
            None
        }
        WM_QUERYENDSESSION => Some(LRESULT(1)),
        WM_ENDSESSION => {
            if wparam.0 != 0 {
                unsafe { PostQuitMessage(0) };
            }
            Some(LRESULT(0))
        }
        m if m != 0 && m == TASKBAR_CREATED.with(|t| t.get()) => {
            // Explorer restarted and the taskbar was rebuilt: the tray icon has to be added again
            TRAY.with(|t| {
                if let Some(tray) = t.borrow().as_ref() {
                    tray.add(hwnd);
                }
            });
            Some(LRESULT(0))
        }
        _ => None,
    }
}

// ---------------------------------------------------------------------------------------------
/// A notification-area icon with a popup menu.
///
/// A left click opens the menu only after the double-click time has passed: a double click runs the
/// first item instead, and the menu's own modal loop would swallow its second click.
struct Tray {
    icon: HICON,
    owns_icon: bool,
    tip: String,
    items: Vec<Option<String>>,
    skip_release: bool,
    menu_open: bool,
}

impl Tray {
    fn data(&self, hwnd: HWND, flags: windows::Win32::UI::Shell::NOTIFY_ICON_DATA_FLAGS) -> NOTIFYICONDATAW {
        let mut data = NOTIFYICONDATAW::default();
        data.cbSize = std::mem::size_of::<NOTIFYICONDATAW>() as u32;
        data.hWnd = hwnd;
        data.uID = 1;
        data.uFlags = flags;
        data.uCallbackMessage = WM_TRAY;
        data.hIcon = self.icon;
        copy_wide(&mut data.szTip, &self.tip);
        data
    }

    fn add(&self, hwnd: HWND) {
        let ok = unsafe { Shell_NotifyIconW(NIM_ADD, &self.data(hwnd, NIF_MESSAGE | NIF_ICON | NIF_TIP)) };
        if !ok.as_bool() {
            log("the tray icon was not added");
        }
    }

    fn notify(&self, hwnd: HWND, title: &str, body: &str) {
        let mut data = self.data(hwnd, NIF_INFO);
        copy_wide(&mut data.szInfoTitle, title);
        copy_wide(&mut data.szInfo, body);
        data.dwInfoFlags = NIIF_INFO;
        unsafe {
            let _ = Shell_NotifyIconW(NIM_MODIFY, &data);
        }
    }

    fn remove(&self, hwnd: HWND) {
        unsafe {
            let _ = Shell_NotifyIconW(NIM_DELETE, &self.data(hwnd, windows::Win32::UI::Shell::NOTIFY_ICON_DATA_FLAGS(0)));
            if self.owns_icon && !self.icon.is_invalid() {
                let _ = DestroyIcon(self.icon);
            }
        }
    }
}

fn tray_event(hwnd: HWND, code: u32) {
    match code {
        WM_RBUTTONUP => {
            TRAY_CLICK.with(|c| c.set(false));
            show_menu(hwnd);
        }
        WM_LBUTTONUP => {
            let skip = TRAY.with(|t| t.borrow_mut().as_mut().map(|tray| std::mem::replace(&mut tray.skip_release, false)).unwrap_or(false));
            if !skip {
                // the loop opens the menu once the double-click time has passed without a second click
                TRAY_CLICK.with(|c| c.set(true));
                push(Event::Timer(TOKEN_TRAY_MENU));
            }
        }
        WM_LBUTTONDBLCLK => {
            TRAY_CLICK.with(|c| c.set(false));
            TRAY.with(|t| {
                if let Some(tray) = t.borrow_mut().as_mut() {
                    tray.skip_release = true; // the button-up that ends the double click is not a new click
                }
            });
            if TRAY.with(|t| t.borrow().as_ref().map(|tray| tray.items.iter().any(Option::is_some)).unwrap_or(false)) {
                push(Event::TrayItem(0));
            }
        }
        _ => {}
    }
}

fn show_menu(hwnd: HWND) {
    let items = TRAY.with(|t| {
        let mut t = t.borrow_mut();
        let tray = t.as_mut()?;
        if tray.menu_open || tray.items.is_empty() {
            return None;
        }
        tray.menu_open = true;
        Some(tray.items.clone())
    });
    let Some(items) = items else { return };
    let mut chosen = 0usize;
    unsafe {
        if let Ok(menu) = CreatePopupMenu() {
            for (index, item) in items.iter().enumerate() {
                match item {
                    None => {
                        let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR::null());
                    }
                    Some(label) => {
                        let text = wide(&label.replace('&', "&&"));
                        let _ = AppendMenuW(menu, MF_STRING, index + 1, PCWSTR(text.as_ptr()));
                    }
                }
            }
            let mut point = POINT::default();
            let _ = GetCursorPos(&mut point);
            // without this the menu does not close when the user clicks elsewhere; the WM_NULL afterwards
            // lets the owner's thread leave the menu state
            let _ = SetForegroundWindow(hwnd);
            let command = TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, point.x, point.y, Some(0), hwnd, None);
            let _ = PostMessageW(Some(hwnd), WM_NULL, WPARAM(0), LPARAM(0));
            let _ = DestroyMenu(menu);
            chosen = command.0 as usize;
        }
    }
    TRAY.with(|t| {
        if let Some(tray) = t.borrow_mut().as_mut() {
            tray.menu_open = false;
        }
    });
    if chosen > 0 && chosen <= items.len() && items[chosen - 1].is_some() {
        push(Event::TrayItem(chosen - 1));
    }
}

/// An HICON from a PNG, or the stock application icon. Scaled to fit `size` x `size`.
fn load_icon(path: &Path, size: u32) -> (HICON, bool) {
    if let Ok(img) = image::open(path) {
        let rgba = img.resize(size, size, image::imageops::FilterType::Lanczos3).to_rgba8();
        let (w, h) = rgba.dimensions();
        if let Ok(dib) = Dib::new(w, h) {
            // an icon's colour bitmap keeps its alpha channel: straight BGRA
            let dst = unsafe { std::slice::from_raw_parts_mut(dib.bits as *mut u8, (w * h * 4) as usize) };
            for (s, d) in rgba.as_raw().chunks_exact(4).zip(dst.chunks_exact_mut(4)) {
                d.copy_from_slice(&[s[2], s[1], s[0], s[3]]);
            }
            unsafe {
                // an all-zero 1 bpp mask: the alpha of the colour bitmap decides what is visible
                let zeros = vec![0u8; (w.div_ceil(16) * 2 * h) as usize];
                let mask = CreateBitmap(w as i32, h as i32, 1, 1, Some(zeros.as_ptr() as *const c_void));
                let info = ICONINFO { fIcon: true.into(), xHotspot: 0, yHotspot: 0, hbmMask: mask, hbmColor: dib.bitmap };
                let icon = CreateIconIndirect(&info);
                let _ = DeleteObject(mask.into());
                if let Ok(icon) = icon {
                    return (icon, true);
                }
            }
        }
    }
    unsafe {
        if let Ok(icon) = LoadImageW(None, PCWSTR(wide(&path.to_string_lossy()).as_ptr()), IMAGE_ICON, size as i32, size as i32, LR_LOADFROMFILE) {
            if !icon.is_invalid() {
                return (HICON(icon.0), true);
            }
        }
        (LoadIconW(None, IDI_APPLICATION).unwrap_or_default(), false)
    }
}

// ---------------------------------------------------------------------------------------------
pub struct Win32Backend {
    hinstance: windows::Win32::Foundation::HINSTANCE,
    class_name: Vec<u16>,
    control: HWND,
    timers: TimerQueue,
    next_id: u64,
    stopping: bool,
    wake: Arc<WakeFlag>,
    screen: ((i32, i32, i32, i32), u64),
    screen_due: Option<Instant>,
}

impl Win32Backend {
    pub fn new() -> Result<Win32Backend, String> {
        unsafe {
            // physical pixels everywhere; fails harmlessly when the process is already DPI aware
            let _ = SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
            let hinstance = GetModuleHandleW(None).map_err(|e| e.to_string())?.into();
            let class_name = wide(&format!("DeskWidgets.Window.{}", std::process::id()));
            let class = WNDCLASSEXW {
                cbSize: std::mem::size_of::<WNDCLASSEXW>() as u32,
                lpfnWndProc: Some(window_proc),
                hInstance: hinstance,
                hCursor: LoadCursorW(None, IDC_ARROW).unwrap_or_default(),
                lpszClassName: PCWSTR(class_name.as_ptr()),
                ..Default::default()
            };
            if RegisterClassExW(&class) == 0 {
                return Err("RegisterClassExW failed".into());
            }
            let control = CreateWindowExW(
                WS_EX_TOOLWINDOW,
                PCWSTR(class_name.as_ptr()),
                w!("DeskWidgets"),
                WS_POPUP,
                0,
                0,
                0,
                0,
                None,
                None,
                Some(hinstance),
                None,
            )
            .map_err(|e| format!("CreateWindowExW: {e}"))?;
            CONTROL.with(|c| c.set(hwnd_key(control)));
            TASKBAR_CREATED.with(|t| t.set(RegisterWindowMessageW(w!("TaskbarCreated"))));
            let wake = Arc::new(WakeFlag::default());
            WAKE.with(|w| *w.borrow_mut() = Some(wake.clone()));
            let mut backend = Win32Backend {
                hinstance,
                class_name,
                control,
                timers: TimerQueue::new(),
                next_id: 1,
                stopping: false,
                wake,
                screen: ((0, 0, 0, 0), 0),
                screen_due: None,
            };
            backend.screen = (backend.work_area(), (backend.dpi_scale() * 1000.0) as u64);
            Ok(backend)
        }
    }

    fn dpi(&self) -> u32 {
        unsafe {
            // the primary monitor's own current value (it follows a change in Settings), the screen's as the fallback
            let monitor = MonitorFromPoint(POINT { x: 0, y: 0 }, MONITOR_DEFAULTTOPRIMARY);
            let (mut dx, mut dy) = (0u32, 0u32);
            if GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, &mut dx, &mut dy).is_ok() && dx > 0 {
                return dx;
            }
            let dc = GetDC(None);
            if dc.is_invalid() {
                return 96;
            }
            let dpi = GetDeviceCaps(Some(dc), LOGPIXELSX);
            ReleaseDC(None, dc);
            if dpi > 0 { dpi as u32 } else { 96 }
        }
    }

    fn pump(&mut self, app: &mut dyn App) {
        // events recorded by the window procedure
        loop {
            let event = EVENTS.with(|e| e.borrow_mut().pop_front());
            let Some(event) = event else { break };
            match event {
                Event::Timer(TOKEN_TRAY_MENU) => {
                    // a left click on the tray icon: wait out the double-click time first
                    let delay = Duration::from_millis(unsafe { GetDoubleClickTime() } as u64);
                    self.timers.add(Instant::now(), delay, TOKEN_TRAY_MENU);
                }
                Event::Wake => {
                    self.wake.take();
                    app.event(self, Event::Wake);
                }
                other => app.event(self, other),
            }
        }
        if SCREEN_DIRTY.with(|d| d.replace(false)) {
            let state = (self.work_area(), (self.dpi_scale() * 1000.0) as u64);
            if state != self.screen {
                self.screen_due = Some(Instant::now() + SCREEN_DEBOUNCE);
            }
        }
        if let Some(due) = self.screen_due {
            if Instant::now() >= due {
                self.screen_due = None;
                self.screen = (self.work_area(), (self.dpi_scale() * 1000.0) as u64);
                app.event(self, Event::ScreenChanged);
            }
        }
        let now = Instant::now();
        while let Some(token) = self.timers.pop_due(now) {
            if token == TOKEN_TRAY_MENU {
                if TRAY_CLICK.with(|c| c.replace(false)) {
                    show_menu(self.control);
                }
            } else {
                app.event(self, Event::Timer(token));
            }
            if self.stopping {
                break;
            }
        }
    }
}

impl Host for Win32Backend {
    fn create_window(&mut self, title: &str, width: u32, height: u32, x: i32, y: i32, click_through: bool) -> Result<WinId, String> {
        let mut ex = WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE;
        if click_through {
            ex |= WS_EX_TRANSPARENT;
        }
        let title = wide(title);
        let hwnd = unsafe {
            CreateWindowExW(
                ex,
                PCWSTR(self.class_name.as_ptr()),
                PCWSTR(title.as_ptr()),
                WS_POPUP,
                x,
                y,
                width as i32,
                height as i32,
                None,
                None,
                Some(self.hinstance),
                None,
            )
        }
        .map_err(|e| format!("CreateWindowExW failed: {e}"))?;
        let id = self.next_id;
        self.next_id += 1;
        let state = Rc::new(WinState {
            id,
            hwnd,
            dib: RefCell::new(None),
            shown: Cell::new(false),
            closing: Cell::new(false),
            failed: Cell::new(false),
            own_moves: Cell::new(0),
            held: RefCell::new(Vec::new()),
            last: Cell::new((0, 0)),
        });
        WINDOWS.with(|w| w.borrow_mut().insert(hwnd_key(hwnd), state));
        Ok(id)
    }

    fn present(&mut self, win: WinId, pixmap: &Pixmap) {
        let Some(state) = self.find(win) else { return };
        let (w, h) = (pixmap.width(), pixmap.height());
        if w < 1 || h < 1 {
            return;
        }
        {
            let mut dib = state.dib.borrow_mut();
            if dib.as_ref().map(|d| (d.width, d.height) != (w, h)).unwrap_or(true) {
                match Dib::new(w, h) {
                    Ok(new) => *dib = Some(new),
                    Err(e) => {
                        if !state.failed.replace(true) {
                            log(e);
                        }
                        return;
                    }
                }
            }
            dib.as_ref().unwrap().fill(pixmap.data());
        }
        let dib = state.dib.borrow();
        let dib = dib.as_ref().unwrap();
        let size = SIZE { cx: w as i32, cy: h as i32 };
        let origin = POINT { x: 0, y: 0 };
        let blend = BLENDFUNCTION { BlendOp: AC_SRC_OVER as u8, BlendFlags: 0, SourceConstantAlpha: 255, AlphaFormat: AC_SRC_ALPHA as u8 };
        state.own_moves.set(state.own_moves.get() + 1);
        unsafe {
            // no destination point: the window stays where it is, only its size follows the surface
            let result = UpdateLayeredWindow(state.hwnd, None, None, Some(&size), Some(dib.dc), Some(&origin), COLORREF(0), Some(&blend), ULW_ALPHA);
            match result {
                Ok(()) => {
                    if !state.shown.replace(true) {
                        let _ = ShowWindow(state.hwnd, SW_SHOWNOACTIVATE);
                        let _ = SetWindowPos(state.hwnd, Some(HWND_BOTTOM), 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE);
                    }
                }
                Err(e) => {
                    if !state.failed.replace(true) {
                        log(format!("UpdateLayeredWindow failed: {e}"));
                    }
                }
            }
        }
        state.own_moves.set(state.own_moves.get() - 1);
    }

    fn move_window(&mut self, win: WinId, x: i32, y: i32) {
        let Some(state) = self.find(win) else { return };
        state.own_moves.set(state.own_moves.get() + 1);
        unsafe {
            let _ = SetWindowPos(state.hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
        }
        state.own_moves.set(state.own_moves.get() - 1);
    }

    fn position(&mut self, win: WinId) -> Option<(i32, i32)> {
        let state = self.find(win)?;
        let mut rect = RECT::default();
        unsafe { GetWindowRect(state.hwnd, &mut rect) }.ok()?;
        Some((rect.left, rect.top))
    }

    fn close_window(&mut self, win: WinId) {
        let Some(state) = self.find(win) else { return };
        state.closing.set(true);
        unsafe {
            if let Err(e) = DestroyWindow(state.hwnd) {
                log(format!("DestroyWindow failed: {e}"));
            }
        }
        WINDOWS.with(|w| w.borrow_mut().remove(&hwnd_key(state.hwnd)));
    }

    fn set_timer(&mut self, delay: Duration, token: u64) -> TimerId {
        self.timers.add(Instant::now(), delay, token)
    }

    fn cancel_timer(&mut self, id: TimerId) {
        self.timers.cancel(id);
    }

    fn work_area(&mut self) -> (i32, i32, i32, i32) {
        unsafe {
            let mut rect = RECT::default();
            if SystemParametersInfoW(SPI_GETWORKAREA, 0, Some(&mut rect as *mut RECT as *mut c_void), SYSTEM_PARAMETERS_INFO_UPDATE_FLAGS(0)).is_ok() {
                return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top);
            }
            (0, 0, GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN))
        }
    }

    fn dpi_scale(&mut self) -> f64 {
        (self.dpi() as f64 / 96.0).clamp(1.0, 3.0)
    }

    fn create_tray(&mut self, tooltip: &str, icon: &Path, items: Vec<Option<String>>) -> bool {
        let size = unsafe { GetSystemMetrics(SM_CXSMICON) }.max(16) as u32;
        let (icon, owns_icon) = load_icon(icon, size);
        let tray = Tray { icon, owns_icon, tip: tooltip.chars().take(127).collect(), items, skip_release: false, menu_open: false };
        tray.add(self.control);
        TRAY.with(|t| *t.borrow_mut() = Some(tray));
        true
    }

    fn tray_notify(&mut self, title: &str, body: &str) -> bool {
        TRAY.with(|t| match t.borrow().as_ref() {
            Some(tray) => {
                tray.notify(self.control, &title.chars().take(63).collect::<String>(), &body.chars().take(255).collect::<String>());
                true
            }
            None => false,
        })
    }

    fn register_hotkey(&mut self, spec: &str) -> bool {
        let Some((mods, key)) = super::parse_hotkey(spec) else {
            log(format!("hotkey {spec:?} is not understood (use e.g. ctrl+alt+w)"));
            return false;
        };
        let Some(vk) = virtual_key(&key) else {
            log(format!("hotkey {spec:?} is not understood (use e.g. ctrl+alt+w)"));
            return false;
        };
        let mut modifiers = MOD_NOREPEAT;
        for m in mods {
            modifiers |= match m {
                "ctrl" => MOD_CONTROL,
                "alt" => MOD_ALT,
                "shift" => MOD_SHIFT,
                _ => MOD_WIN,
            };
        }
        let _: HOT_KEY_MODIFIERS = modifiers;
        unsafe {
            if RegisterHotKey(Some(self.control), 1, modifiers, vk).is_err() {
                log(format!("hotkey {spec:?} was refused; another program may use it"));
                return false;
            }
        }
        HOTKEY.with(|h| h.set(Some(1)));
        true
    }

    fn quit(&mut self) {
        self.stopping = true;
        unsafe {
            let _ = PostMessageW(Some(self.control), WM_APP_QUIT, WPARAM(0), LPARAM(0));
        }
    }

    fn waker(&self) -> Waker {
        let flag = self.wake.clone();
        let control = hwnd_key(self.control);
        Waker::new(move || {
            // posted to the control window rather than the thread: thread messages are dropped by the
            // modal loops of menus, window messages are delivered by them
            if flag.set() {
                unsafe {
                    let _ = PostMessageW(Some(hwnd_from(control)), WM_APP_WAKE, WPARAM(0), LPARAM(0));
                }
            }
        })
    }
}

impl Win32Backend {
    fn find(&self, win: WinId) -> Option<Rc<WinState>> {
        WINDOWS.with(|w| w.borrow().values().find(|s| s.id == win).cloned())
    }
}

/// Virtual-key code of the key part of a hotkey.
fn virtual_key(key: &str) -> Option<u32> {
    let named = match key {
        "space" => 0x20,
        "enter" | "return" => 0x0D,
        "tab" => 0x09,
        "esc" | "escape" => 0x1B,
        "backspace" => 0x08,
        "delete" | "del" => 0x2E,
        "insert" | "ins" => 0x2D,
        "home" => 0x24,
        "end" => 0x23,
        "pageup" | "pgup" => 0x21,
        "pagedown" | "pgdn" => 0x22,
        "left" => 0x25,
        "up" => 0x26,
        "right" => 0x27,
        "down" => 0x28,
        "pause" => 0x13,
        "print" | "printscreen" | "prtsc" => 0x2C,
        _ => 0,
    };
    if named != 0 {
        return Some(named);
    }
    let chars: Vec<char> = key.chars().collect();
    if chars.len() == 1 && chars[0].is_ascii_alphanumeric() {
        return Some(chars[0].to_ascii_uppercase() as u32);
    }
    if chars.first() == Some(&'f') {
        if let Ok(n) = key[1..].parse::<u32>() {
            if (1..=24).contains(&n) {
                return Some(0x6F + n);
            }
        }
    }
    None
}

impl Backend for Win32Backend {
    fn run(&mut self, app: &mut dyn App) {
        let mut msg = MSG::default();
        'outer: while !self.stopping {
            self.pump(app);
            if self.stopping {
                break;
            }
            let wait = match self.timers.next_timeout(Instant::now()) {
                None => MAX_WAIT_MS,
                Some(d) => (d.as_millis() as u32 + 1).min(MAX_WAIT_MS),
            };
            let pending = EVENTS.with(|e| !e.borrow().is_empty());
            if !pending {
                unsafe {
                    MsgWaitForMultipleObjects(None, false, wait, QS_ALLINPUT);
                }
            }
            unsafe {
                while PeekMessageW(&mut msg, None, 0, 0, PM_REMOVE).as_bool() {
                    if msg.message == WM_QUIT {
                        self.stopping = true;
                        break 'outer;
                    }
                    let _ = TranslateMessage(&msg);
                    DispatchMessageW(&msg);
                }
            }
        }
        // the icon would otherwise stay in the notification area until the mouse passes over it
        TRAY.with(|t| {
            if let Some(tray) = t.borrow_mut().take() {
                tray.remove(self.control);
            }
        });
        if HOTKEY.with(|h| h.take()).is_some() {
            unsafe {
                let _ = UnregisterHotKey(Some(self.control), 1);
            }
        }
        let ids: Vec<WinId> = WINDOWS.with(|w| w.borrow().values().map(|s| s.id).collect());
        for id in ids {
            self.close_window(id);
        }
        self.stopping = false;
    }
}

#[allow(dead_code)]
fn _unused(_: HMENU, _: WINDOW_EX_STYLE, _: WINDOW_STYLE, _: UPDATE_LAYERED_WINDOW_FLAGS) {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mouse_coordinates_are_signed_16_bit_halves() {
        assert_eq!(lparam_point(LPARAM(((20u32 << 16) | 10u32) as isize)), (10, 20));
        assert_eq!(lparam_point(LPARAM((((-5i16 as u16 as u32) << 16) | (-3i16 as u16 as u32)) as isize)), (-3, -5));
    }

    #[test]
    fn virtual_keys() {
        assert_eq!(virtual_key("w"), Some('W' as u32));
        assert_eq!(virtual_key("f5"), Some(0x74));
        assert_eq!(virtual_key("space"), Some(0x20));
        assert_eq!(virtual_key("nonsense"), None);
    }
}
