//! The Linux backend: widget windows drawn straight onto an X server (X11 desktops and Xwayland).
//!
//! Each widget is its own 32-bit ARGB window, so a compositing window manager blends the transparent
//! corners and shadows for us. Pixels are pushed with plain PutImage: the images are a few hundred
//! pixels wide and change about once a second, which does not justify shared memory.
//!
//! Pure Rust (x11rb): no libX11 is needed. Everything here runs on the loop thread; the self-pipe behind
//! the `Waker` is the only thing other threads touch.
use std::collections::{HashMap, VecDeque};
use std::io::Write;
use std::os::fd::AsRawFd;
use std::os::unix::net::UnixStream;
use std::sync::Arc;
use std::time::{Duration, Instant};

use regex::Regex;
use tiny_skia::Pixmap;
use x11rb::connection::{Connection, RequestConnection};
use x11rb::wrapper::ConnectionExt as _;
use x11rb::protocol::randr::{self, ConnectionExt as _, NotifyMask};
use x11rb::protocol::shape::{self, ConnectionExt as _};
use x11rb::protocol::xproto::{
    self, AtomEnum, ChangeWindowAttributesAux, ColormapAlloc, ConfigureWindowAux, ConnectionExt as _, CreateGCAux, CreateWindowAux,
    EventMask, ImageFormat, ImageOrder, PropMode, VisualClass, Visualid, WindowClass,
};
use x11rb::protocol::Event as XEvent;
use x11rb::rust_connection::RustConnection;

use super::{App, Backend, Button, Event, Host, TimerId, TimerQueue, WakeFlag, Waker, WinId};
use crate::core::util::log;

const ALL_DESKTOPS: u32 = 0xFFFF_FFFF;
/// A window manager may move or nudge a window a moment after `move_window` (and a compositor may add an
/// offset), so configure events this soon after our own request are not reported as the user's doing.
const SELF_MOVE_GRACE: Duration = Duration::from_millis(300);

/// Whether widgets ask to stay below other windows (_NET_WM_STATE_BELOW).
///
/// Not on GNOME under Wayland: Ubuntu's desktop icons (DING) keep one transparent window over the whole screen
/// at the bottom of the *normal* layer, and a window marked "below" sits in a lower layer than that. The
/// widgets would still be visible (DING is transparent) but every click and drag would land on DING, so they
/// stay normal windows there, like the conky ones of DeskWidgets 1.x/2.x. DESKWIDGETS_KEEP_BELOW=1 or 0 overrides.
pub fn keep_below() -> bool {
    if let Ok(v) = std::env::var("DESKWIDGETS_KEEP_BELOW") {
        if v == "0" || v == "1" {
            return v == "1";
        }
    }
    let wayland = std::env::var("XDG_SESSION_TYPE").map(|v| v.to_lowercase() == "wayland").unwrap_or(false)
        || std::env::var_os("WAYLAND_DISPLAY").map(|v| !v.is_empty()).unwrap_or(false);
    let gnome = std::env::var("XDG_CURRENT_DESKTOP").map(|v| v.to_uppercase().contains("GNOME")).unwrap_or(false);
    !(wayland && gnome)
}

/// The `Xft.dpi` value from an X resource database string (the root window's RESOURCE_MANAGER), or None.
pub fn parse_xft_dpi(resources: &str) -> Option<f64> {
    let re = Regex::new(r"(?m)^Xft\.dpi:\s*([0-9]+(?:\.[0-9]+)?)\s*$").ok()?;
    re.captures(resources)?.get(1)?.as_str().parse().ok()
}

struct Atoms {
    wm_protocols: u32,
    wm_delete_window: u32,
    utf8_string: u32,
    resource_manager: u32,
    motif_wm_hints: u32,
    net_wm_name: u32,
    net_wm_pid: u32,
    net_wm_user_time: u32,
    net_wm_desktop: u32,
    net_wm_state: u32,
    state_below: u32,
    state_sticky: u32,
    state_skip_taskbar: u32,
    state_skip_pager: u32,
    window_type: u32,
    window_type_normal: u32,
    wm_hints: u32,
    wm_size_hints: u32,
}

struct XWin {
    colormap: u32,
    gc: u32,
    width: u32,
    height: u32,
    wanted: (i32, i32),
    known: (i32, i32),
    moved_at: Instant,
    mapped: bool,
    placed: bool,
    frame: Vec<u8>, // BGRA, as the server wants it
}

pub struct X11Backend {
    conn: RustConnection,
    root: u32,
    depth: u8,
    visual: Visualid,
    lsb_first: bool,
    atoms: Atoms,
    windows: HashMap<u32, XWin>,
    ids: HashMap<WinId, u32>,
    timers: TimerQueue,
    out: VecDeque<Event>,
    motion: Option<(u32, i32, i32, i32, i32)>,
    moved: Vec<u32>,
    screen_dirty: bool,
    screen_state: ((i32, i32, i32, i32), u64),
    wake: Arc<WakeFlag>,
    wake_read: UnixStream,
    wake_write: Arc<UnixStream>,
    quit: bool,
    shape_ok: Option<bool>,
    randr_ok: bool,
    randr_monitors: bool,
    keep_below: bool,
    hotkey_warned: bool,
}

fn x_err<E: std::fmt::Display>(what: &str) -> impl Fn(E) -> String + '_ {
    move |e| format!("{what}: {e}")
}

impl X11Backend {
    /// Opens its own connection to the X server (`display` or $DISPLAY).
    pub fn new(display: Option<&str>) -> Result<X11Backend, String> {
        let (conn, screen_num) = RustConnection::connect(display).map_err(|e| {
            let shown = display.map(str::to_string).or_else(|| std::env::var("DISPLAY").ok()).unwrap_or_default();
            format!("cannot open the X display {shown:?}: {e}; is DISPLAY set, and is the X server (Xwayland on a Wayland session) running?")
        })?;
        let screen = conn.setup().roots[screen_num].clone();
        let root = screen.root;
        let visual = argb_visual(&screen).ok_or(
            "the X server (DISPLAY) offers no 32-bit ARGB visual, so widget windows cannot be transparent; a compositing desktop session or Xvfb with \"+32\" has one",
        )?;
        let atoms = intern_atoms(&conn)?;
        let (wake_read, wake_write) = UnixStream::pair().map_err(|e| e.to_string())?;
        wake_read.set_nonblocking(true).map_err(|e| e.to_string())?;
        wake_write.set_nonblocking(true).map_err(|e| e.to_string())?;
        let lsb_first = conn.setup().image_byte_order == ImageOrder::LSB_FIRST;
        let mut backend = X11Backend {
            conn,
            root,
            depth: 32,
            visual,
            lsb_first,
            atoms,
            windows: HashMap::new(),
            ids: HashMap::new(),
            timers: TimerQueue::new(),
            out: VecDeque::new(),
            motion: None,
            moved: Vec::new(),
            screen_dirty: false,
            screen_state: ((0, 0, 0, 0), 0),
            wake: Arc::new(WakeFlag::default()),
            wake_read,
            wake_write: Arc::new(wake_write),
            quit: false,
            shape_ok: None,
            randr_ok: false,
            randr_monitors: false,
            keep_below: keep_below(),
            hotkey_warned: false,
        };
        // screen size changes show up as ConfigureNotify on the root; DPI changes as its RESOURCE_MANAGER property
        backend
            .conn
            .change_window_attributes(root, &ChangeWindowAttributesAux::new().event_mask(EventMask::STRUCTURE_NOTIFY | EventMask::PROPERTY_CHANGE))
            .map_err(x_err("select root events"))?;
        backend.init_randr();
        backend.screen_state = (backend.work_area(), (backend.dpi_scale() * 1000.0) as u64);
        backend.conn.flush().map_err(x_err("flush"))?;
        Ok(backend)
    }

    /// XRandR (optional) tells us about monitor changes and which monitor is the primary one.
    fn init_randr(&mut self) {
        let Ok(Some(_)) = self.conn.extension_information(randr::X11_EXTENSION_NAME) else { return };
        let Ok(cookie) = self.conn.randr_query_version(1, 5) else { return };
        let Ok(version) = cookie.reply() else { return };
        let mask = NotifyMask::SCREEN_CHANGE | NotifyMask::CRTC_CHANGE | NotifyMask::OUTPUT_CHANGE;
        if self.conn.randr_select_input(self.root, mask).is_ok() {
            self.randr_ok = true;
        }
        self.randr_monitors = (version.major_version, version.minor_version) >= (1, 5);
    }

    /// Empties the window's input region: the pointer then reaches whatever is below. Without the
    /// XShape extension the window just keeps its clicks.
    fn pass_clicks_through(&mut self, window: u32) {
        if self.shape_ok.is_none() {
            let ok = matches!(self.conn.extension_information(shape::X11_EXTENSION_NAME), Ok(Some(_)))
                && self
                    .conn
                    .shape_query_version()
                    .ok()
                    .and_then(|c| c.reply().ok())
                    .map(|v| (v.major_version, v.minor_version) >= (1, 1)) // input shapes came with XShape 1.1
                    .unwrap_or(false);
            if !ok {
                log("this X server cannot make windows click-through: the grid overlay will take the mouse");
            }
            self.shape_ok = Some(ok);
        }
        if self.shape_ok == Some(true) {
            let _ = self.conn.shape_rectangles(shape::SO::SET, shape::SK::INPUT, xproto::ClipOrdering::UNSORTED, window, 0, 0, &[]);
        }
    }

    fn set_property32(&self, window: u32, property: u32, kind: impl Into<u32>, values: &[u32]) {
        let _ = self.conn.change_property32(PropMode::REPLACE, window, property, kind, values);
    }

    fn size_hints(&self, w: &XWin) -> [u32; 18] {
        // US_POSITION | P_POSITION | P_SIZE | P_MIN_SIZE | P_MAX_SIZE | P_WIN_GRAVITY
        let flags = 1 | (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5) | (1 << 9);
        let (x, y) = w.wanted;
        let (wd, ht) = (w.width, w.height);
        [flags, x as u32, y as u32, wd, ht, wd, ht, wd, ht, 0, 0, 0, 0, 0, 0, 0, 0, 10 /* StaticGravity */]
    }

    fn set_size_hints(&self, id: u32) {
        if let Some(w) = self.windows.get(&id) {
            let hints = self.size_hints(w);
            self.set_property32(id, AtomEnum::WM_NORMAL_HINTS.into(), self.atoms.wm_size_hints, &hints);
        }
    }

    fn set_hints(&self, id: u32, title: &str, pid: u32) {
        let a = &self.atoms;
        let c = &self.conn;
        let _ = c.change_property8(PropMode::REPLACE, id, a.net_wm_name, a.utf8_string, title.as_bytes());
        let legacy: Vec<u8> = title.chars().map(|ch| if (ch as u32) < 256 { ch as u8 } else { b'?' }).collect();
        let _ = c.change_property8(PropMode::REPLACE, id, AtomEnum::WM_NAME, AtomEnum::STRING, &legacy);
        let _ = c.change_property8(PropMode::REPLACE, id, AtomEnum::WM_CLASS, AtomEnum::STRING, b"deskwidget\0DeskWidget\0");
        // input=False: the window manager must never hand a widget the keyboard focus
        self.set_property32(id, AtomEnum::WM_HINTS.into(), a.wm_hints, &[1, 0, 0, 0, 0, 0, 0, 0, 0]);
        self.set_size_hints(id);
        self.set_property32(id, a.wm_protocols, AtomEnum::ATOM, &[a.wm_delete_window]);
        self.set_property32(id, a.motif_wm_hints, a.motif_wm_hints, &[2, 0, 0, 0, 0]); // MWM_HINTS_DECORATIONS, none
        // NORMAL rather than UTILITY or DESKTOP: window managers keep moving such windows on Super+drag,
        // and `skip_taskbar` already keeps them out of the alt-tab list
        self.set_property32(id, a.window_type, AtomEnum::ATOM, &[a.window_type_normal]);
        let mut states = vec![a.state_sticky, a.state_skip_taskbar, a.state_skip_pager];
        if self.keep_below {
            states.insert(0, a.state_below);
        }
        self.set_property32(id, a.net_wm_state, AtomEnum::ATOM, &states);
        self.set_property32(id, a.net_wm_desktop, AtomEnum::CARDINAL, &[ALL_DESKTOPS]);
        self.set_property32(id, a.net_wm_user_time, AtomEnum::CARDINAL, &[0]); // "not user-initiated": do not focus on map
        self.set_property32(id, a.net_wm_pid, AtomEnum::CARDINAL, &[pid]);
    }

    fn paint(&self, id: u32) {
        let Some(w) = self.windows.get(&id) else { return };
        if !w.mapped || w.frame.is_empty() {
            return;
        }
        let row = w.width as usize * 4;
        let max = self.conn.maximum_request_bytes().saturating_sub(64);
        let rows_per_request = (max / row).clamp(1, w.height as usize);
        let mut y = 0usize;
        while y < w.height as usize {
            let rows = rows_per_request.min(w.height as usize - y);
            let data = &w.frame[y * row..(y + rows) * row];
            let _ = self.conn.put_image(ImageFormat::Z_PIXMAP, id, w.gc, w.width as u16, rows as u16, 0, y as i16, 0, self.depth, data);
            y += rows;
        }
    }

    fn release(&mut self, id: u32, destroy: bool) {
        let Some(w) = self.windows.remove(&id) else { return };
        self.ids.retain(|_, v| *v != id);
        if destroy {
            let _ = self.conn.destroy_window(id);
        }
        let _ = self.conn.free_gc(w.gc);
        let _ = self.conn.free_colormap(w.colormap);
        let _ = self.conn.get_input_focus().map(|c| c.reply()); // gone when this returns, for whoever looks at the X tree next
    }

    fn root_position(&self, id: u32) -> Option<(i32, i32)> {
        // in root coordinates, so a frame or a reparenting window manager does not matter
        let reply = self.conn.translate_coordinates(id, self.root, 0, 0).ok()?.reply().ok()?;
        Some((reply.dst_x as i32, reply.dst_y as i32))
    }

    // events
    fn drain(&mut self) -> bool {
        let mut any = false;
        loop {
            loop {
                match self.conn.poll_for_event() {
                    Ok(Some(event)) => {
                        any = true;
                        self.handle(event);
                    }
                    Ok(None) => break,
                    Err(e) => {
                        log(format!("lost the connection to the X server: {e}"));
                        self.quit = true;
                        return any;
                    }
                }
            }
            self.flush_motion();
            if self.moved.is_empty() && !self.screen_dirty {
                return any;
            }
            // these ask the server (position, work area), which can queue further events
            let moved = std::mem::take(&mut self.moved);
            for id in moved {
                self.check_moved(id);
            }
            if self.screen_dirty {
                self.screen_dirty = false;
                self.check_screen();
            }
        }
    }

    fn handle(&mut self, event: XEvent) {
        if let XEvent::MotionNotify(m) = &event {
            if self.windows.contains_key(&m.event) {
                // a drag produces a burst of motion events; only the last one of a burst matters
                if let Some((win, ..)) = self.motion {
                    if win != m.event {
                        self.flush_motion();
                    }
                }
                self.motion = Some((m.event, m.event_x as i32, m.event_y as i32, m.root_x as i32, m.root_y as i32));
            }
            return;
        }
        self.flush_motion(); // keep the order of motion and the events around it
        match event {
            XEvent::ButtonPress(b) | XEvent::ButtonRelease(b) => {
                // buttons 4-7 are the scroll wheel
                let pressed = b.response_type & 0x7f == xproto::BUTTON_PRESS_EVENT;
                let button = match b.detail {
                    1 => Some(Button::Left),
                    2 => Some(Button::Middle),
                    3 => Some(Button::Right),
                    _ => None,
                };
                if let (Some(button), true) = (button, self.windows.contains_key(&b.event)) {
                    self.out.push_back(Event::Button {
                        win: b.event as u64,
                        button,
                        pressed,
                        x: b.event_x as i32,
                        y: b.event_y as i32,
                        root_x: b.root_x as i32,
                        root_y: b.root_y as i32,
                    });
                }
            }
            XEvent::Expose(e) => {
                if e.count == 0 && self.windows.contains_key(&e.window) {
                    self.paint(e.window);
                }
            }
            XEvent::ConfigureNotify(c) => {
                if self.windows.contains_key(&c.window) {
                    if !self.moved.contains(&c.window) {
                        self.moved.push(c.window);
                    }
                } else if c.window == self.root {
                    self.screen_dirty = true;
                }
            }
            XEvent::MapNotify(m) => self.on_map(m.window),
            XEvent::DestroyNotify(d) => {
                if self.windows.contains_key(&d.window) {
                    self.release(d.window, false);
                    self.out.push_back(Event::Closed { win: d.window as u64 });
                }
            }
            XEvent::ClientMessage(m) => {
                if self.windows.contains_key(&m.window) && m.type_ == self.atoms.wm_protocols && m.data.as_data32()[0] == self.atoms.wm_delete_window {
                    self.release(m.window, true);
                    self.out.push_back(Event::Closed { win: m.window as u64 });
                }
            }
            XEvent::PropertyNotify(p) => {
                if p.window == self.root && p.atom == self.atoms.resource_manager {
                    self.screen_dirty = true;
                }
            }
            XEvent::RandrScreenChangeNotify(_) | XEvent::RandrNotify(_) => self.screen_dirty = true,
            XEvent::Error(e) => {
                // a widget window can vanish between two calls (BadWindow, BadDrawable): that must not end the process
                if !matches!(e.error_kind, x11rb::protocol::ErrorKind::Window | x11rb::protocol::ErrorKind::Drawable) {
                    log(format!("X error {:?} in request {}.{}", e.error_kind, e.major_opcode, e.minor_opcode));
                }
            }
            _ => {}
        }
    }

    fn flush_motion(&mut self) {
        if let Some((win, x, y, rx, ry)) = self.motion.take() {
            self.out.push_back(Event::Motion { win: win as u64, x, y, root_x: rx, root_y: ry });
        }
    }

    /// Some window managers ignore the requested position when they place a new window: ask again once.
    fn on_map(&mut self, id: u32) {
        let Some(w) = self.windows.get_mut(&id) else { return };
        if w.placed {
            return;
        }
        w.placed = true;
        let wanted = w.wanted;
        if let Some(position) = self.root_position(id) {
            if position != wanted {
                self.move_window(id as u64, wanted.0, wanted.1);
            }
        }
    }

    fn check_moved(&mut self, id: u32) {
        let Some(position) = self.root_position(id) else { return };
        let Some(w) = self.windows.get_mut(&id) else { return };
        let own = w.moved_at.elapsed() < SELF_MOVE_GRACE;
        let changed = position != w.known;
        w.known = position;
        if changed && !own {
            self.out.push_back(Event::Moved { win: id as u64, x: position.0, y: position.1 });
        }
    }

    fn check_screen(&mut self) {
        let state = (self.work_area(), (self.dpi_scale() * 1000.0) as u64);
        if state != self.screen_state {
            self.screen_state = state;
            self.out.push_back(Event::ScreenChanged);
        }
    }

    fn primary_monitor(&self) -> Option<(i32, i32, i32, i32)> {
        if !self.randr_monitors {
            return None;
        }
        let reply = self.conn.randr_get_monitors(self.root, true).ok()?.reply().ok()?;
        let found: Vec<(bool, (i32, i32, i32, i32))> = reply
            .monitors
            .iter()
            .filter(|m| m.width > 0 && m.height > 0)
            .map(|m| (m.primary, (m.x as i32, m.y as i32, m.width as i32, m.height as i32)))
            .collect();
        if let Some((_, g)) = found.iter().find(|(primary, _)| *primary) {
            return Some(*g);
        }
        // no primary set: the monitor at the origin, else the first one
        found.iter().find(|(_, g)| g.0 == 0 && g.1 == 0).or(found.first()).map(|(_, g)| *g)
    }

    fn screen_size(&self) -> (i32, i32) {
        self.conn
            .get_geometry(self.root)
            .ok()
            .and_then(|c| c.reply().ok())
            .map(|g| (g.width as i32, g.height as i32))
            .unwrap_or((1920, 1080))
    }

    fn resource_manager(&self) -> Option<String> {
        let reply = self
            .conn
            .get_property(false, self.root, self.atoms.resource_manager, AtomEnum::STRING, 0, 1 << 16)
            .ok()?
            .reply()
            .ok()?;
        (reply.format == 8).then(|| String::from_utf8_lossy(&reply.value).into_owned())
    }

    fn deliver(&mut self, app: &mut dyn App) -> bool {
        let mut progressed = false;
        while let Some(event) = self.out.pop_front() {
            progressed = true;
            app.event(self, event);
        }
        progressed
    }

    fn drain_wake_pipe(&mut self) {
        use std::io::Read;
        let mut buf = [0u8; 256];
        while let Ok(n) = (&self.wake_read).read(&mut buf) {
            if n == 0 {
                break;
            }
        }
    }
}

fn argb_visual(screen: &xproto::Screen) -> Option<Visualid> {
    // a 32-bit TrueColor visual laid out like ARGB32 (the masks are checked, not assumed)
    screen
        .allowed_depths
        .iter()
        .filter(|d| d.depth == 32)
        .flat_map(|d| d.visuals.iter())
        .find(|v| v.class == VisualClass::TRUE_COLOR && v.red_mask == 0xFF0000 && v.green_mask == 0xFF00 && v.blue_mask == 0xFF)
        .map(|v| v.visual_id)
}

fn intern_atoms(conn: &RustConnection) -> Result<Atoms, String> {
    let names = [
        "WM_PROTOCOLS", "WM_DELETE_WINDOW", "UTF8_STRING", "RESOURCE_MANAGER", "_MOTIF_WM_HINTS", "_NET_WM_NAME",
        "_NET_WM_PID", "_NET_WM_USER_TIME", "_NET_WM_DESKTOP", "_NET_WM_STATE", "_NET_WM_STATE_BELOW",
        "_NET_WM_STATE_STICKY", "_NET_WM_STATE_SKIP_TASKBAR", "_NET_WM_STATE_SKIP_PAGER", "_NET_WM_WINDOW_TYPE",
        "_NET_WM_WINDOW_TYPE_NORMAL", "WM_HINTS", "WM_SIZE_HINTS",
    ];
    let cookies: Vec<_> = names.iter().map(|n| conn.intern_atom(false, n.as_bytes())).collect::<Result<_, _>>().map_err(x_err("intern atoms"))?;
    let mut ids = Vec::new();
    for cookie in cookies {
        ids.push(cookie.reply().map_err(x_err("intern atoms"))?.atom);
    }
    Ok(Atoms {
        wm_protocols: ids[0],
        wm_delete_window: ids[1],
        utf8_string: ids[2],
        resource_manager: ids[3],
        motif_wm_hints: ids[4],
        net_wm_name: ids[5],
        net_wm_pid: ids[6],
        net_wm_user_time: ids[7],
        net_wm_desktop: ids[8],
        net_wm_state: ids[9],
        state_below: ids[10],
        state_sticky: ids[11],
        state_skip_taskbar: ids[12],
        state_skip_pager: ids[13],
        window_type: ids[14],
        window_type_normal: ids[15],
        wm_hints: ids[16],
        wm_size_hints: ids[17],
    })
}

impl Host for X11Backend {
    fn create_window(&mut self, title: &str, width: u32, height: u32, x: i32, y: i32, click_through: bool) -> Result<WinId, String> {
        let (width, height) = (width.max(1), height.max(1));
        let conn = &self.conn;
        let id = conn.generate_id().map_err(x_err("create window"))?;
        let colormap = conn.generate_id().map_err(x_err("create window"))?;
        conn.create_colormap(ColormapAlloc::NONE, colormap, self.root, self.visual).map_err(x_err("create colormap"))?;
        let events = EventMask::BUTTON_PRESS | EventMask::BUTTON_RELEASE | EventMask::POINTER_MOTION | EventMask::BUTTON_MOTION | EventMask::EXPOSURE | EventMask::STRUCTURE_NOTIFY;
        // no background pixmap: the server must not clear the window before an Expose repaint (flicker)
        let aux = CreateWindowAux::new().background_pixmap(0).border_pixel(0).event_mask(events).colormap(colormap);
        conn.create_window(self.depth, id, self.root, x as i16, y as i16, width as u16, height as u16, 0, WindowClass::INPUT_OUTPUT, self.visual, &aux)
            .map_err(x_err("create window"))?;
        let gc = conn.generate_id().map_err(x_err("create window"))?;
        conn.create_gc(gc, id, &CreateGCAux::new()).map_err(x_err("create gc"))?;
        self.windows.insert(
            id,
            XWin {
                colormap,
                gc,
                width,
                height,
                wanted: (x, y),
                known: (x, y),
                moved_at: Instant::now(),
                mapped: false,
                placed: false,
                frame: Vec::new(),
            },
        );
        self.ids.insert(id as u64, id);
        self.set_hints(id, title, std::process::id());
        if click_through {
            self.pass_clicks_through(id);
        }
        let _ = self.conn.flush();
        Ok(id as u64)
    }

    fn present(&mut self, win: WinId, pixmap: &Pixmap) {
        let id = win as u32;
        let (w, h) = (pixmap.width(), pixmap.height());
        let lsb = self.lsb_first;
        let Some(window) = self.windows.get_mut(&id) else { return };
        let resized = (w, h) != (window.width, window.height);
        if resized {
            window.width = w;
            window.height = h;
        }
        window.frame.resize(w as usize * h as usize * 4, 0);
        if lsb {
            super::super::engine::canvas::rgba_to_bgra(pixmap.data(), &mut window.frame);
        } else {
            for (s, d) in pixmap.data().chunks_exact(4).zip(window.frame.chunks_exact_mut(4)) {
                d.copy_from_slice(&[s[3], s[0], s[1], s[2]]);
            }
        }
        if resized {
            self.set_size_hints(id); // first: a manager would clamp the resize to the old fixed size
            let _ = self.conn.configure_window(id, &ConfigureWindowAux::new().width(w).height(h));
        }
        let Some(window) = self.windows.get_mut(&id) else { return };
        if !window.mapped {
            window.mapped = true;
            window.moved_at = Instant::now();
            let _ = self.conn.map_window(id);
        }
        self.paint(id);
        let _ = self.conn.flush();
    }

    fn move_window(&mut self, win: WinId, x: i32, y: i32) {
        let id = win as u32;
        let Some(w) = self.windows.get_mut(&id) else { return };
        w.wanted = (x, y);
        w.known = (x, y);
        w.moved_at = Instant::now();
        let _ = self.conn.configure_window(id, &ConfigureWindowAux::new().x(x).y(y));
        let _ = self.conn.flush();
    }

    fn position(&mut self, win: WinId) -> Option<(i32, i32)> {
        let id = win as u32;
        self.windows.contains_key(&id).then(|| self.root_position(id)).flatten()
    }

    fn close_window(&mut self, win: WinId) {
        self.release(win as u32, true);
        let _ = self.conn.flush();
    }

    fn set_timer(&mut self, delay: Duration, token: u64) -> TimerId {
        self.timers.add(Instant::now(), delay, token)
    }

    fn cancel_timer(&mut self, id: TimerId) {
        self.timers.cancel(id);
    }

    /// The primary monitor, or the whole X screen when XRandR cannot say which that is. The service
    /// applies its own margins from the settings, so panels and docks are deliberately not subtracted.
    fn work_area(&mut self) -> (i32, i32, i32, i32) {
        self.primary_monitor().unwrap_or_else(|| {
            let (w, h) = self.screen_size();
            (0, 0, w, h)
        })
    }

    /// `Xft.dpi` / 96 when the desktop sets it (1.0 below 96 dpi, at most 3.0). X reports a physical
    /// screen size that is often wrong, so it is not used.
    fn dpi_scale(&mut self) -> f64 {
        match self.resource_manager().as_deref().and_then(parse_xft_dpi) {
            Some(dpi) if dpi >= 96.0 => (dpi / 96.0).min(3.0),
            _ => 1.0,
        }
    }

    fn register_hotkey(&mut self, _spec: &str) -> bool {
        // GNOME gets a custom keybinding from `deskwidgets setup`; other desktops bind `deskwidgets gallery` themselves
        let _ = self.hotkey_warned;
        false
    }

    fn quit(&mut self) {
        self.quit = true;
        let _ = (&*self.wake_write).write(&[1]);
    }

    fn waker(&self) -> Waker {
        let flag = self.wake.clone();
        let pipe = self.wake_write.clone();
        Waker::new(move || {
            if flag.set() {
                let _ = (&*pipe).write(&[1]); // a full pipe means a wake-up is pending anyway
            }
        })
    }
}

impl Backend for X11Backend {
    fn run(&mut self, app: &mut dyn App) {
        let x_fd = self.conn.stream().as_raw_fd();
        let wake_fd = self.wake_read.as_raw_fd();
        while !self.quit {
            let mut progressed = self.drain();
            progressed |= self.deliver(app);
            if self.wake.take() {
                progressed = true;
                app.event(self, Event::Wake);
            }
            let now = Instant::now();
            while let Some(token) = self.timers.pop_due(now) {
                progressed = true;
                app.event(self, Event::Timer(token));
                if self.quit {
                    break;
                }
            }
            progressed |= self.deliver(app);
            if self.quit {
                break;
            }
            if progressed {
                // callbacks talk to the server (position(), ...) and the answers bring events along:
                // look again before sleeping
                continue;
            }
            let _ = self.conn.flush();
            let timeout_ms: i32 = match self.timers.next_timeout(Instant::now()) {
                None => -1,
                Some(d) => (d.as_micros().div_ceil(1000)).min(i32::MAX as u128) as i32,
            };
            let mut fds = [
                libc::pollfd { fd: x_fd, events: libc::POLLIN, revents: 0 },
                libc::pollfd { fd: wake_fd, events: libc::POLLIN, revents: 0 },
            ];
            unsafe { libc::poll(fds.as_mut_ptr(), 2, timeout_ms) };
            if fds[1].revents != 0 {
                self.drain_wake_pipe();
            }
        }
        // close what is still open, so nothing is left on the desktop
        let ids: Vec<u32> = self.windows.keys().copied().collect();
        for id in ids {
            self.release(id, true);
        }
        let _ = self.conn.flush();
    }
}
