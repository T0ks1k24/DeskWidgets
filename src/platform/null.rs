//! A headless backend for tests: no display, windows are plain objects, time only moves when the test says so.
use std::collections::{HashMap, VecDeque};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tiny_skia::Pixmap;

use super::{App, Backend, Button, Event, Host, TimerId, TimerQueue, WakeFlag, Waker, WinId};

pub struct NullWindow {
    pub title: String,
    pub width: u32,
    pub height: u32,
    pub x: i32,
    pub y: i32,
    pub click_through: bool,
    pub presented: u32,
    pub last: Option<Pixmap>,
    pub closed: bool,
}

pub struct NullBackend {
    pub screen: (i32, i32),
    pub scale: f64,
    pub windows: HashMap<WinId, NullWindow>,
    pub tray: Option<Vec<Option<String>>>,
    pub notifications: Vec<(String, String)>,
    pub hotkey: Option<String>,
    pub quit_requested: bool,
    next_win: WinId,
    timers: TimerQueue,
    now: Instant,
    queue: VecDeque<Event>,
    wake: Arc<WakeFlag>,
}

impl NullBackend {
    pub fn new(screen: (i32, i32), scale: f64) -> NullBackend {
        NullBackend {
            screen,
            scale,
            windows: HashMap::new(),
            tray: None,
            notifications: Vec::new(),
            hotkey: None,
            quit_requested: false,
            next_win: 1,
            timers: TimerQueue::new(),
            now: Instant::now(),
            queue: VecDeque::new(),
            wake: Arc::new(WakeFlag::default()),
        }
    }

    /// Delivers what is queued and due, without moving time.
    pub fn flush(&mut self, app: &mut dyn App) {
        loop {
            let mut progressed = false;
            if self.wake.take() {
                self.queue.push_front(Event::Wake);
            }
            while let Some(event) = self.queue.pop_front() {
                progressed = true;
                app.event(self, event);
            }
            while let Some(token) = self.timers.pop_due(self.now) {
                progressed = true;
                app.event(self, Event::Timer(token));
            }
            if !progressed {
                return;
            }
        }
    }

    /// Moves the clock forward, firing timers in order on the way.
    pub fn advance(&mut self, app: &mut dyn App, by: Duration) {
        let target = self.now + by;
        loop {
            self.flush(app);
            match self.timers.next_timeout(self.now) {
                Some(wait) if self.now + wait <= target => self.now += wait,
                _ => break,
            }
        }
        self.now = target;
        self.flush(app);
    }

    pub fn inject(&mut self, event: Event) {
        self.queue.push_back(event);
    }

    // what a user would do
    pub fn press(&mut self, win: WinId, x: i32, y: i32, button: Button) {
        let (wx, wy) = self.windows.get(&win).map(|w| (w.x, w.y)).unwrap_or((0, 0));
        self.inject(Event::Button { win, button, pressed: true, x, y, root_x: wx + x, root_y: wy + y });
    }

    pub fn release(&mut self, win: WinId, x: i32, y: i32, button: Button) {
        let (wx, wy) = self.windows.get(&win).map(|w| (w.x, w.y)).unwrap_or((0, 0));
        self.inject(Event::Button { win, button, pressed: false, x, y, root_x: wx + x, root_y: wy + y });
    }

    /// The pointer at root position (rx, ry) while over `win`.
    pub fn motion_root(&mut self, win: WinId, rx: i32, ry: i32) {
        let (wx, wy) = self.windows.get(&win).map(|w| (w.x, w.y)).unwrap_or((0, 0));
        self.inject(Event::Motion { win, x: rx - wx, y: ry - wy, root_x: rx, root_y: ry });
    }

    /// The window manager moved the window (Super+drag).
    pub fn wm_move(&mut self, win: WinId, x: i32, y: i32) {
        if let Some(w) = self.windows.get_mut(&win) {
            w.x = x;
            w.y = y;
        }
        self.inject(Event::Moved { win, x, y });
    }

    pub fn open_windows(&self) -> Vec<(&WinId, &NullWindow)> {
        let mut list: Vec<_> = self.windows.iter().filter(|(_, w)| !w.closed).collect();
        list.sort_by_key(|(id, _)| **id);
        list
    }

    pub fn find(&self, title: &str) -> Option<WinId> {
        self.windows.iter().find(|(_, w)| !w.closed && w.title == title).map(|(id, _)| *id)
    }
}

impl Host for NullBackend {
    fn create_window(&mut self, title: &str, width: u32, height: u32, x: i32, y: i32, click_through: bool) -> Result<WinId, String> {
        let id = self.next_win;
        self.next_win += 1;
        self.windows.insert(
            id,
            NullWindow { title: title.to_string(), width, height, x, y, click_through, presented: 0, last: None, closed: false },
        );
        Ok(id)
    }

    fn present(&mut self, win: WinId, pixmap: &Pixmap) {
        if let Some(w) = self.windows.get_mut(&win).filter(|w| !w.closed) {
            w.presented += 1;
            w.width = pixmap.width();
            w.height = pixmap.height();
            w.last = Some(pixmap.clone());
        }
    }

    fn move_window(&mut self, win: WinId, x: i32, y: i32) {
        if let Some(w) = self.windows.get_mut(&win).filter(|w| !w.closed) {
            w.x = x;
            w.y = y;
        }
    }

    fn position(&mut self, win: WinId) -> Option<(i32, i32)> {
        self.windows.get(&win).filter(|w| !w.closed).map(|w| (w.x, w.y))
    }

    fn close_window(&mut self, win: WinId) {
        if let Some(w) = self.windows.get_mut(&win) {
            w.closed = true;
        }
    }

    fn set_timer(&mut self, delay: Duration, token: u64) -> TimerId {
        self.timers.add(self.now, delay, token)
    }

    fn cancel_timer(&mut self, id: TimerId) {
        self.timers.cancel(id);
    }

    fn work_area(&mut self) -> (i32, i32, i32, i32) {
        (0, 0, self.screen.0, self.screen.1)
    }

    fn dpi_scale(&mut self) -> f64 {
        self.scale
    }

    fn create_tray(&mut self, _tooltip: &str, _icon: &Path, items: Vec<Option<String>>) -> bool {
        self.tray = Some(items);
        true
    }

    fn tray_notify(&mut self, title: &str, body: &str) -> bool {
        self.notifications.push((title.to_string(), body.to_string()));
        true
    }

    fn register_hotkey(&mut self, spec: &str) -> bool {
        self.hotkey = Some(spec.to_string());
        true
    }

    fn quit(&mut self) {
        self.quit_requested = true;
    }

    fn waker(&self) -> Waker {
        let flag = self.wake.clone();
        Waker::new(move || {
            flag.set();
        })
    }
}

impl Backend for NullBackend {
    fn run(&mut self, app: &mut dyn App) {
        // real time, for a quick smoke test; tests normally call flush/advance themselves
        let _ = Mutex::new(());
        while !self.quit_requested {
            self.now = Instant::now();
            self.flush(app);
            std::thread::sleep(Duration::from_millis(10));
        }
    }
}
