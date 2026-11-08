//! Operating-system specific code behind the small interfaces below.
//!
//! * `Host`    what the service asks of the system: windows, timers, the screen, a tray icon, a hotkey
//! * `Backend` a `Host` that also runs the event loop
//! * `App`     what the backend calls back: one `event()` per thing that happened
//!
//! Everything the service does goes through these, so Linux (X11 / Xwayland) and Windows differ only in
//! `x11.rs` and `win32.rs`. `null.rs` is a headless backend with a manual clock, used by the tests.
//!
//! Threading: a backend runs its loop on the thread that calls `run()`. Windows, timers and events live on
//! that thread; a `Waker` (which is `Send`) is the only thing other threads may use: it makes the loop
//! deliver `Event::Wake`.
use std::collections::BinaryHeap;
use std::cmp::Reverse;
use std::collections::HashSet;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub mod integration;
pub mod null;
#[cfg(windows)]
pub mod win32;
#[cfg(unix)]
pub mod x11;

pub type WinId = u64;
pub type TimerId = u64;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Button {
    Left,
    Middle,
    Right,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Event {
    /// Mouse button down/up; x, y are inside the window, root_* on the screen.
    Button { win: WinId, button: Button, pressed: bool, x: i32, y: i32, root_x: i32, root_y: i32 },
    /// The pointer moved over the window (also while a button is held, even outside the window).
    Motion { win: WinId, x: i32, y: i32, root_x: i32, root_y: i32 },
    /// The window was moved by the user or the window manager (not by `move_window`).
    Moved { win: WinId, x: i32, y: i32 },
    /// The window was destroyed from outside.
    Closed { win: WinId },
    /// A new resolution, work area or DPI.
    ScreenChanged,
    /// A timer set with `set_timer` is due.
    Timer(u64),
    /// Another thread called `Waker::wake`.
    Wake,
    /// The global hotkey was pressed.
    Hotkey,
    /// A tray menu item was chosen (index into the items given to `create_tray`).
    TrayItem(usize),
}

/// Wakes the event loop from any thread.
#[derive(Clone)]
pub struct Waker(Arc<dyn Fn() + Send + Sync>);

impl Waker {
    pub fn new(f: impl Fn() + Send + Sync + 'static) -> Waker {
        Waker(Arc::new(f))
    }
    pub fn wake(&self) {
        (self.0)();
    }
}

pub trait Host {
    /// A hidden borderless transparent window, non-focusable, below the normal windows, not in the
    /// taskbar, on every virtual desktop. It appears with the first `present`. Coordinates are screen
    /// pixels, (0, 0) = top-left of the screen. `click_through`: mouse events pass to what is below.
    fn create_window(&mut self, title: &str, width: u32, height: u32, x: i32, y: i32, click_through: bool) -> Result<WinId, String>;
    /// Shows `pixmap` (premultiplied RGBA) and makes the window visible. A different size resizes the window.
    fn present(&mut self, win: WinId, pixmap: &tiny_skia::Pixmap);
    fn move_window(&mut self, win: WinId, x: i32, y: i32);
    /// (x, y) of the window's top-left corner as it is now on screen, or None if unknown.
    fn position(&mut self, win: WinId) -> Option<(i32, i32)>;
    fn close_window(&mut self, win: WinId);

    /// `Event::Timer(token)` after `delay`. Returns an id for `cancel_timer`.
    fn set_timer(&mut self, delay: Duration, token: u64) -> TimerId;
    fn cancel_timer(&mut self, id: TimerId);

    /// (x, y, width, height) in pixels of the usable area of the primary monitor
    /// (without a Windows taskbar; on Linux the whole screen).
    fn work_area(&mut self) -> (i32, i32, i32, i32);
    /// 1.0 for 96 dpi, 1.5 for 144 dpi ... (the widgets are drawn that much larger).
    fn dpi_scale(&mut self) -> f64 {
        1.0
    }

    // optional desktop integration; backends return false when the platform has no equivalent
    /// A notification-area icon whose menu has `items` (None = separator); a chosen item is `Event::TrayItem(index)`.
    fn create_tray(&mut self, _tooltip: &str, _icon: &Path, _items: Vec<Option<String>>) -> bool {
        false
    }
    fn tray_notify(&mut self, _title: &str, _body: &str) -> bool {
        false
    }
    /// Global hotkey, e.g. "ctrl+alt+w". Returns true when registered.
    fn register_hotkey(&mut self, _spec: &str) -> bool {
        false
    }

    /// Ends `Backend::run`.
    fn quit(&mut self);
    fn waker(&self) -> Waker;
}

pub trait App {
    fn event(&mut self, host: &mut dyn Host, event: Event);
}

pub trait Backend: Host {
    /// Runs the loop until `quit()`. `app.event` is called for everything that happens.
    fn run(&mut self, app: &mut dyn App);
}

/// The backend for this system: Win32 on Windows, X11 (also Xwayland) on Linux.
pub fn create_backend() -> Result<Box<dyn Backend>, String> {
    #[cfg(windows)]
    {
        return Ok(Box::new(win32::Win32Backend::new()?));
    }
    #[cfg(unix)]
    {
        if std::env::var_os("DISPLAY").map(|d| d.is_empty()).unwrap_or(true) {
            return Err("there is no X display: DeskWidgets draws through X11 (Xwayland on Wayland sessions). Is DISPLAY set?".into());
        }
        Ok(Box::new(x11::X11Backend::new(None)?))
    }
}

/// Can we open the display and get a 32-bit window? (`deskwidgets doctor`)
pub fn probe_display() -> bool {
    #[cfg(unix)]
    {
        x11::X11Backend::new(None).is_ok()
    }
    #[cfg(windows)]
    {
        true
    }
}

/// A parsed "ctrl+alt+w" style hotkey: modifier names and one key.
pub fn parse_hotkey(spec: &str) -> Option<(Vec<&'static str>, String)> {
    let mut modifiers: Vec<&'static str> = Vec::new();
    let mut keys: Vec<String> = Vec::new();
    for part in spec.to_lowercase().split('+') {
        let part = part.trim();
        let modifier = match part {
            "ctrl" | "control" => Some("ctrl"),
            "alt" => Some("alt"),
            "shift" => Some("shift"),
            "win" | "super" | "meta" | "cmd" => Some("super"),
            _ => None,
        };
        match modifier {
            Some(m) => {
                if !modifiers.contains(&m) {
                    modifiers.push(m)
                }
            }
            None if part.is_empty() => return None,
            None => keys.push(part.to_string()),
        }
    }
    // exactly one key plus at least one modifier: a global hotkey without a modifier would swallow that key
    if modifiers.is_empty() || keys.len() != 1 {
        return None;
    }
    Some((modifiers, keys.remove(0)))
}

// ---------------------------------------------------------------------------------------------
/// Timers for a backend loop. `next_timeout()` says how long the loop may sleep, `pop_due()` hands out what is due.
#[derive(Default)]
pub struct TimerQueue {
    heap: BinaryHeap<Reverse<(Instant, u64, u64)>>, // (due, id, token)
    cancelled: HashSet<u64>,
    next_id: u64,
}

impl TimerQueue {
    pub fn new() -> TimerQueue {
        TimerQueue::default()
    }

    pub fn add(&mut self, now: Instant, delay: Duration, token: u64) -> TimerId {
        self.next_id += 1;
        self.heap.push(Reverse((now + delay, self.next_id, token)));
        self.next_id
    }

    pub fn cancel(&mut self, id: TimerId) {
        self.cancelled.insert(id);
    }

    fn drop_cancelled(&mut self) {
        while let Some(Reverse((_, id, _))) = self.heap.peek() {
            if self.cancelled.remove(id) {
                self.heap.pop();
            } else {
                break;
            }
        }
    }

    /// How long until the next timer (zero if one is due), None if there are none.
    pub fn next_timeout(&mut self, now: Instant) -> Option<Duration> {
        self.drop_cancelled();
        self.heap.peek().map(|Reverse((due, _, _))| due.saturating_duration_since(now))
    }

    /// The token of one due timer, if any.
    pub fn pop_due(&mut self, now: Instant) -> Option<u64> {
        self.drop_cancelled();
        match self.heap.peek() {
            Some(Reverse((due, _, _))) if *due <= now => self.heap.pop().map(|Reverse((_, _, token))| token),
            _ => None,
        }
    }
}

/// What `call_soon`-style wake-ups share between threads: a flag that says "something happened"
/// and the way to wake the loop.
#[derive(Default)]
pub struct WakeFlag(Mutex<bool>);

impl WakeFlag {
    pub fn set(&self) -> bool {
        let mut flag = self.0.lock().unwrap();
        !std::mem::replace(&mut *flag, true)
    }
    pub fn take(&self) -> bool {
        std::mem::replace(&mut *self.0.lock().unwrap(), false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hotkeys() {
        assert_eq!(parse_hotkey("ctrl+alt+w"), Some((vec!["ctrl", "alt"], "w".to_string())));
        assert_eq!(parse_hotkey("Super+Alt+W").map(|h| h.0), Some(vec!["super", "alt"]));
        assert_eq!(parse_hotkey("w"), None);
        assert_eq!(parse_hotkey("ctrl+a+b"), None);
        assert_eq!(parse_hotkey("ctrl+"), None);
    }

    #[test]
    fn timers_fire_in_order_and_can_be_cancelled() {
        let mut q = TimerQueue::new();
        let t0 = Instant::now();
        let _a = q.add(t0, Duration::from_millis(30), 1);
        let b = q.add(t0, Duration::from_millis(10), 2);
        q.add(t0, Duration::from_millis(20), 3);
        q.cancel(b);
        assert_eq!(q.next_timeout(t0), Some(Duration::from_millis(20)));
        assert_eq!(q.pop_due(t0), None);
        let later = t0 + Duration::from_millis(100);
        assert_eq!((q.pop_due(later), q.pop_due(later), q.pop_due(later)), (Some(3), Some(1), None));
        assert_eq!(q.next_timeout(later), None);
    }
}
