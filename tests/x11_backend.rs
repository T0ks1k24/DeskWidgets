//! The X11 backend against a real X server (Xvfb), driven with XTest. Skipped when Xvfb is not installed.
#![cfg(unix)]
mod common;

use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use deskwidgets::platform::x11::{keep_below, parse_xft_dpi, X11Backend};
use deskwidgets::platform::{App, Backend, Button, Event, Host};
use tiny_skia::Pixmap;
use x11rb::connection::Connection;
use x11rb::protocol::xproto::{ConnectionExt as _, ImageFormat};
use x11rb::protocol::xtest::ConnectionExt as _;
use x11rb::rust_connection::RustConnection;

struct Server {
    child: Child,
    display: String,
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn xvfb() -> Option<Server> {
    use std::sync::atomic::{AtomicU32, Ordering};
    static NEXT: AtomicU32 = AtomicU32::new(0);
    let number = 300 + (std::process::id() % 400) + NEXT.fetch_add(1, Ordering::SeqCst);
    let display = format!(":{number}");
    let child = Command::new("Xvfb")
        .args([display.as_str(), "-screen", "0", "1280x800x24+32", "-nolisten", "tcp", "+extension", "RANDR"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let server = Server { child, display };
    let socket = format!("/tmp/.X11-unix/X{number}");
    let deadline = Instant::now() + Duration::from_secs(10);
    while !std::path::Path::new(&socket).exists() {
        if Instant::now() > deadline {
            return None;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    std::thread::sleep(Duration::from_millis(200));
    Some(server)
}

fn solid(w: u32, h: u32, rgba: [u8; 4]) -> Pixmap {
    let mut pixmap = Pixmap::new(w, h).unwrap();
    for p in pixmap.data_mut().chunks_exact_mut(4) {
        p.copy_from_slice(&rgba);
    }
    pixmap
}

/// Collects events, quits when told to.
#[derive(Default)]
struct Probe {
    events: Vec<Event>,
    quit_after: Option<Instant>,
    quit_on: Option<fn(&[Event]) -> bool>,
}

impl App for Probe {
    fn event(&mut self, host: &mut dyn Host, event: Event) {
        self.events.push(event);
        if self.quit_on.map(|f| f(&self.events)).unwrap_or(false) || self.quit_after.map(|t| Instant::now() > t).unwrap_or(false) {
            host.quit();
        }
    }
}

macro_rules! server_or_skip {
    () => {
        match xvfb() {
            Some(s) => s,
            None => {
                eprintln!("skipped: Xvfb is not available");
                return;
            }
        }
    };
}

#[test]
fn a_presented_frame_shows_up_in_a_transparent_window_at_the_right_place() {
    let _dir = common::isolated();
    let server = server_or_skip!();
    let mut backend = X11Backend::new(Some(&server.display)).unwrap();
    let win = backend.create_window("deskwidget:test", 100, 60, 30, 40, false).unwrap();
    // opaque green, then half-transparent red (premultiplied)
    backend.present(win, &solid(100, 60, [0, 255, 0, 255]));
    assert_eq!(backend.position(win), Some((30, 40)));

    let (conn, _) = RustConnection::connect(Some(&server.display)).unwrap();
    let read = |conn: &RustConnection| {
        let reply = conn.get_image(ImageFormat::Z_PIXMAP, win as u32, 5, 5, 1, 1, !0).unwrap().reply().unwrap();
        (reply.depth, reply.data)
    };
    let (depth, data) = read(&conn);
    assert_eq!(depth, 32);
    assert_eq!(data, vec![0, 255, 0, 255], "B, G, R, A");
    backend.present(win, &solid(100, 60, [128, 0, 0, 128]));
    assert_eq!(read(&conn).1, vec![0, 0, 128, 128]);

    backend.move_window(win, 200, 100);
    assert_eq!(backend.position(win), Some((200, 100)));
    backend.present(win, &solid(120, 80, [0, 0, 255, 255])); // a new size resizes the window
    let geometry = conn.get_geometry(win as u32).unwrap().reply().unwrap();
    assert_eq!((geometry.width, geometry.height), (120, 80));

    backend.close_window(win);
    assert!(conn.get_geometry(win as u32).unwrap().reply().is_err(), "the window is gone");
    assert_eq!(backend.position(win), None);
}

#[test]
fn the_screen_is_reported_and_the_dpi_follows_xft() {
    let _dir = common::isolated();
    let server = server_or_skip!();
    let mut backend = X11Backend::new(Some(&server.display)).unwrap();
    assert_eq!(backend.work_area(), (0, 0, 1280, 800));
    assert_eq!(backend.dpi_scale(), 1.0);
    assert_eq!(parse_xft_dpi("Xft.antialias: 1\nXft.dpi: 144\n"), Some(144.0));
    assert_eq!(parse_xft_dpi("Xft.dpi:\t96.5"), Some(96.5));
    assert_eq!(parse_xft_dpi("Xft.hinting: 1"), None);
    let _ = keep_below();
}

#[test]
fn mouse_events_reach_the_app_with_window_and_root_coordinates() {
    let _dir = common::isolated();
    let server = server_or_skip!();
    let mut backend = X11Backend::new(Some(&server.display)).unwrap();
    let win = backend.create_window("deskwidget:test", 200, 120, 300, 200, false).unwrap();
    backend.present(win, &solid(200, 120, [10, 10, 10, 255]));
    let display = server.display.clone();
    let driver = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(400));
        let (conn, screen) = RustConnection::connect(Some(&display)).unwrap();
        let root = conn.setup().roots[screen].root;
        // MotionNotify = 6, ButtonPress = 4, ButtonRelease = 5
        conn.xtest_fake_input(6, 0, 0, root, 340, 230, 0).unwrap();
        conn.xtest_fake_input(4, 1, 0, root, 0, 0, 0).unwrap();
        conn.xtest_fake_input(6, 0, 0, root, 360, 250, 0).unwrap();
        conn.xtest_fake_input(5, 1, 0, root, 0, 0, 0).unwrap();
        conn.xtest_fake_input(4, 3, 0, root, 0, 0, 0).unwrap(); // right button
        conn.xtest_fake_input(5, 3, 0, root, 0, 0, 0).unwrap();
        conn.xtest_fake_input(4, 4, 0, root, 0, 0, 0).unwrap(); // scroll wheel: ignored
        conn.flush().unwrap();
        std::thread::sleep(Duration::from_millis(300));
    });
    let mut probe = Probe { quit_after: Some(Instant::now() + Duration::from_secs(3)), ..Probe::default() };
    probe.quit_on = Some(|events| events.iter().filter(|e| matches!(e, Event::Button { pressed: false, button: Button::Right, .. })).count() >= 1);
    backend.run(&mut probe);
    driver.join().unwrap();
    let buttons: Vec<_> = probe.events.iter().filter_map(|e| match e {
        Event::Button { button, pressed, x, y, root_x, root_y, .. } => Some((*button, *pressed, *x, *y, *root_x, *root_y)),
        _ => None,
    }).collect();
    assert_eq!(buttons[0], (Button::Left, true, 40, 30, 340, 230));
    assert_eq!(buttons[1].0, Button::Left);
    assert!(!buttons[1].1);
    assert_eq!((buttons[1].4, buttons[1].5), (360, 250));
    assert_eq!(buttons.iter().filter(|b| b.0 == Button::Right).count(), 2);
    assert_eq!(buttons.len(), 4, "the wheel is not a button: {buttons:?}");
    assert!(probe.events.iter().any(|e| matches!(e, Event::Motion { root_x: 360, root_y: 250, .. })), "{:?}", probe.events);
}

#[test]
fn timers_and_wakeups_run_on_the_loop() {
    let _dir = common::isolated();
    let server = server_or_skip!();
    let mut backend = X11Backend::new(Some(&server.display)).unwrap();
    let keep = backend.set_timer(Duration::from_millis(80), 7);
    let cancelled = backend.set_timer(Duration::from_millis(40), 8);
    backend.cancel_timer(cancelled);
    let _ = keep;
    let waker = backend.waker();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(150));
        waker.wake();
        waker.wake();
    });
    let mut probe = Probe { quit_after: Some(Instant::now() + Duration::from_secs(5)), ..Probe::default() };
    probe.quit_on = Some(|events| events.contains(&Event::Wake));
    let started = Instant::now();
    backend.run(&mut probe);
    assert!(started.elapsed() < Duration::from_secs(3));
    assert!(probe.events.contains(&Event::Timer(7)));
    assert!(!probe.events.contains(&Event::Timer(8)));
    assert!(probe.events.contains(&Event::Wake));
    // the Timer came before the Wake
    let timer = probe.events.iter().position(|e| *e == Event::Timer(7)).unwrap();
    let wake = probe.events.iter().position(|e| *e == Event::Wake).unwrap();
    assert!(timer < wake);
}

#[test]
fn a_click_through_window_lets_the_pointer_reach_what_is_below() {
    let _dir = common::isolated();
    let server = server_or_skip!();
    let mut backend = X11Backend::new(Some(&server.display)).unwrap();
    let below = backend.create_window("deskwidget:below", 300, 300, 100, 100, false).unwrap();
    backend.present(below, &solid(300, 300, [20, 20, 20, 255]));
    let overlay = backend.create_window("deskwidgets-grid", 600, 600, 0, 0, true).unwrap();
    backend.present(overlay, &solid(600, 600, [0, 0, 0, 0]));
    let display = server.display.clone();
    let driver = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(400));
        let (conn, screen) = RustConnection::connect(Some(&display)).unwrap();
        let root = conn.setup().roots[screen].root;
        conn.xtest_fake_input(6, 0, 0, root, 150, 150, 0).unwrap();
        conn.xtest_fake_input(4, 1, 0, root, 0, 0, 0).unwrap();
        conn.xtest_fake_input(5, 1, 0, root, 0, 0, 0).unwrap();
        conn.flush().unwrap();
        std::thread::sleep(Duration::from_millis(300));
    });
    let mut probe = Probe { quit_after: Some(Instant::now() + Duration::from_secs(3)), ..Probe::default() };
    probe.quit_on = Some(|events| events.iter().any(|e| matches!(e, Event::Button { pressed: false, .. })));
    backend.run(&mut probe);
    driver.join().unwrap();
    let hit = probe.events.iter().find_map(|e| match e {
        Event::Button { win, pressed: true, .. } => Some(*win),
        _ => None,
    });
    assert_eq!(hit, Some(below), "the overlay was transparent to the pointer: {:?}", probe.events);
}
