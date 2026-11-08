//! The service against the headless backend: windows, dragging, the control commands, the control socket.
mod common;

use std::cell::RefCell;
use std::rc::Rc;
use std::sync::mpsc::{channel, Sender};
use std::time::Duration;

use deskwidgets::core::paths;
use deskwidgets::platform::null::NullBackend;
use deskwidgets::platform::{Backend, Button, Event};
use deskwidgets::providers::ServiceHub;
use deskwidgets::service::daemon::Daemon;
use deskwidgets::service::ipc::{self, ControlServer, Request};
use serde_json::{json, Value};

struct Fixture {
    _dir: tempfile::TempDir,
    backend: NullBackend,
    daemon: Daemon,
    gallery: Rc<RefCell<Vec<Option<String>>>>,
    _requests: Sender<Request>,
}

fn fixture() -> Fixture {
    let dir = common::isolated();
    let mut backend = NullBackend::new((1920, 1080), 1.0);
    let (tx, rx) = channel();
    let mut daemon = Daemon::start_with(&mut backend, rx, Rc::new(ServiceHub::disabled()));
    let gallery = Rc::new(RefCell::new(Vec::new()));
    let g = gallery.clone();
    daemon.gallery_launcher = Box::new(move |configure| g.borrow_mut().push(configure.map(str::to_string)));
    Fixture { _dir: dir, backend, daemon, gallery, _requests: tx }
}

impl Fixture {
    fn call(&mut self, command: &str, args: Value) -> Result<Value, String> {
        use deskwidgets::platform::Host as _;
        let _ = self.backend.work_area();
        self.daemon.handle(&mut self.backend, command, &args)
    }

    fn window(&self, id: &str) -> u64 {
        self.daemon.instances[id].window.expect("the widget has a window")
    }

    fn widgets_on_screen(&self) -> usize {
        self.backend.open_windows().iter().filter(|(_, w)| !w.click_through).count()
    }

    fn overlays(&self) -> usize {
        self.backend.open_windows().iter().filter(|(_, w)| w.click_through).count()
    }

    fn pos(&self, win: u64) -> (i32, i32) {
        let w = &self.backend.windows[&win];
        (w.x, w.y)
    }

    fn cell_xy(&self, col: i32, row: i32) -> (i32, i32) {
        self.daemon.window_xy(col, row)
    }

    fn layout_of(&self, id: &str) -> (i32, i32) {
        let i = self.daemon.layout.get(id).unwrap();
        (i.col, i.row)
    }

    /// Presses at (40, 40) inside the window and drags the pointer by (dx, dy) in a few steps.
    fn drag(&mut self, win: u64, dx: i32, dy: i32) {
        let (wx, wy) = self.pos(win);
        self.backend.press(win, 40, 40, Button::Left);
        for i in 1..=6 {
            self.backend.motion_root(win, wx + 40 + dx * i / 6, wy + 40 + dy * i / 6);
        }
        self.backend.release(win, 40 + dx, 40 + dy, Button::Left);
        self.backend.flush(&mut self.daemon);
    }
}

// ---------------------------------------------------------------------------------------------
#[test]
fn startup_opens_one_presented_window_per_widget_on_its_grid_cell() {
    let f = fixture();
    assert_eq!(f.widgets_on_screen(), f.daemon.layout.instances.len());
    assert!(f.daemon.layout.instances.len() >= 5);
    for inst in &f.daemon.layout.instances {
        let w = &f.backend.windows[&f.window(&inst.id)];
        assert!(w.presented >= 1);
        assert!(w.title.starts_with("deskwidget:"));
        assert_eq!((w.x, w.y), f.cell_xy(inst.col, inst.row));
        assert_eq!((w.width as i32, w.height as i32), f.daemon.grid.window_px(&inst.size));
    }
}

#[test]
fn widgets_redraw_on_their_interval_and_shutdown_closes_everything() {
    let mut f = fixture();
    let win = f.window("clock");
    let before = f.backend.windows[&win].presented;
    f.backend.advance(&mut f.daemon, Duration::from_secs(12));
    assert!(f.backend.windows[&win].presented > before, "the clock redrew");
    f.daemon.shutdown(&mut f.backend);
    assert_eq!(f.backend.open_windows().len(), 0);
}

#[test]
fn add_remove_and_errors_are_reported() {
    let mut f = fixture();
    let n = f.widgets_on_screen();
    let id = f.call("add", json!({"type": "countdown"})).unwrap();
    assert_eq!(id, json!("countdown"));
    assert_eq!(f.widgets_on_screen(), n + 1);
    assert!(f.call("add", json!({"type": "nope"})).unwrap_err().contains("No such widget"));
    f.call("remove", json!({"id": "countdown"})).unwrap();
    assert_eq!(f.widgets_on_screen(), n);
    assert!(f.call("remove", json!({"id": "countdown"})).is_err());
    assert!(f.call("bogus", json!({})).unwrap_err().contains("unknown command"));
    assert!(f.call("add", json!({})).unwrap_err().contains("missing"));
    // the layout on disk follows
    let saved: Value = serde_json::from_str(&std::fs::read_to_string(paths::layout()).unwrap()).unwrap();
    assert_eq!(saved["instances"].as_array().unwrap().len(), n);
}

#[test]
fn a_full_grid_is_reported() {
    let mut f = fixture();
    let mut last = Ok(json!(null));
    for _ in 0..80 {
        last = f.call("add", json!({"type": "clock"}));
        if last.is_err() {
            break;
        }
    }
    assert!(last.unwrap_err().to_lowercase().contains("full"));
}

#[test]
fn configure_restarts_with_the_new_size_and_style() {
    let mut f = fixture();
    let old = f.window("clock");
    f.call("configure", json!({"id": "clock", "patch": {"size": "medium", "style": "neon"}})).unwrap();
    let new = f.window("clock");
    assert!(f.backend.windows[&old].closed);
    assert_eq!((f.backend.windows[&new].width as i32, f.backend.windows[&new].height as i32), f.daemon.grid.window_px("medium"));
    assert_eq!(f.daemon.layout.get("clock").unwrap().style, "neon");
}

#[test]
fn move_and_arrange() {
    let mut f = fixture();
    f.call("move", json!({"id": "clock", "col": 4, "row": 3})).unwrap();
    assert_eq!(f.layout_of("clock"), (4, 3));
    assert_eq!(f.pos(f.window("clock")), f.cell_xy(4, 3));
    f.call("arrange", json!({})).unwrap();
    let w = f.window("clock");
    assert_eq!(f.pos(w), f.cell_xy(f.layout_of("clock").0, f.layout_of("clock").1));
    assert!(f.call("move", json!({"id": "ghost", "col": 1, "row": 1})).is_err());
}

#[test]
fn status_list_and_ping() {
    let mut f = fixture();
    assert_eq!(f.call("ping", json!({})).unwrap(), json!("pong"));
    let status = f.call("status", json!({})).unwrap();
    assert_eq!(status["widgets"], json!(f.daemon.layout.instances.len()));
    assert_eq!(f.call("list", json!({})).unwrap().as_array().unwrap().len(), f.daemon.layout.instances.len());
    assert_eq!(f.call("list", json!({})).unwrap()[0]["type"], json!("clock"));
}

#[test]
fn settings_change_applies_the_grid() {
    let mut f = fixture();
    std::fs::write(paths::settings(), json!({"scale": 1.5, "gap": 10}).to_string()).unwrap();
    f.call("settings", json!({})).unwrap();
    assert_eq!(f.daemon.grid.scale, 1.5);
    assert_eq!(f.daemon.grid.gap, 10);
    for inst in &f.daemon.layout.instances {
        assert!(f.daemon.grid.fits(inst.col, inst.row, &inst.size));
    }
    // a font change is applied without restarting the process
    std::fs::write(paths::settings(), json!({"font": "DejaVu Sans"}).to_string()).unwrap();
    f.call("settings", json!({})).unwrap();
    assert_eq!(f.daemon.settings.font, "DejaVu Sans");
    assert!(f.daemon.instances.values().all(|i| i.window.is_some()));
}

#[test]
fn a_widget_of_a_missing_type_is_kept_but_not_run() {
    let mut f = fixture();
    let folder = deskwidgets::core::scaffold::create("temp_widget", None).unwrap();
    let id = f.call("add", json!({"type": "temp_widget"})).unwrap().as_str().unwrap().to_string();
    assert!(f.daemon.instances[&id].running());
    let win = f.window(&id);
    std::fs::remove_dir_all(folder).unwrap(); // a typo in widget.json, a folder being moved ...
    f.call("reload", json!({})).unwrap();
    assert!(f.backend.windows[&win].closed);
    assert!(f.daemon.instances.get(&id).map(|i| !i.running()).unwrap_or(true));
    assert!(f.daemon.layout.get(&id).is_some(), "the instance stays in the layout");
    let saved = std::fs::read_to_string(paths::layout()).unwrap();
    assert!(saved.contains("temp_widget"));
}

// ---------------------------------------------------------------------------------------------
#[test]
fn dragging_moves_the_window_with_the_pointer_and_snaps_on_release() {
    let mut f = fixture();
    let id = f.call("add", json!({"type": "clock"})).unwrap();
    let id = id.as_str().unwrap().to_string();
    let win = f.window(&id);
    let start = f.pos(win);
    let (px, py) = start;
    f.backend.press(win, 40, 40, Button::Left);
    f.backend.motion_root(win, px + 100, py + 90);
    f.backend.flush(&mut f.daemon);
    assert_eq!(f.pos(win), (start.0 + 60, start.1 + 50), "the window follows the pointer");
    assert_eq!(f.overlays(), 1, "the grid is shown while dragging");
    let pitch = f.daemon.grid.pitch;
    f.backend.motion_root(win, px + 40 + 2 * pitch + 10, py + 40 + pitch - 7);
    f.backend.release(win, 40 + 2 * pitch + 10, 40 + pitch - 7, Button::Left);
    f.backend.flush(&mut f.daemon);
    let (col, row) = f.layout_of(&id);
    assert_eq!(f.pos(win), f.cell_xy(col, row), "snapped onto a cell");
    assert_eq!(f.overlays(), 0, "the grid is hidden again");
    let saved: Value = serde_json::from_str(&std::fs::read_to_string(paths::layout()).unwrap()).unwrap();
    let entry = saved["instances"].as_array().unwrap().iter().find(|i| i["id"] == json!(id)).unwrap();
    assert_eq!((entry["col"].as_i64().unwrap() as i32, entry["row"].as_i64().unwrap() as i32), (col, row));
}

#[test]
fn dropping_on_another_widget_never_overlaps_it() {
    let mut f = fixture();
    let id = f.call("add", json!({"type": "countdown"})).unwrap().as_str().unwrap().to_string();
    let win = f.window(&id);
    let other = f.daemon.layout.get("clock").unwrap().clone();
    let (ox, oy) = f.cell_xy(other.col, other.row);
    let (wx, wy) = f.pos(win);
    f.drag(win, ox - wx + 20, oy - wy + 20);
    let mine = f.layout_of(&id);
    assert_ne!(mine, (other.col, other.row));
    let a = f.daemon.layout.get(&id).unwrap().cells();
    assert!(a.is_disjoint(&other.cells()));
}

#[test]
fn small_movement_is_a_click_not_a_drag() {
    let mut f = fixture();
    let win = f.window("clock");
    let start = f.pos(win);
    f.backend.press(win, 40, 40, Button::Left);
    f.backend.motion_root(win, start.0 + 42, start.1 + 41);
    f.backend.release(win, 42, 41, Button::Left);
    f.backend.flush(&mut f.daemon);
    assert_eq!(f.pos(win), start);
    assert_eq!(f.overlays(), 0);
}

#[test]
fn right_click_opens_the_settings_of_that_widget() {
    let mut f = fixture();
    let win = f.window("weather");
    f.backend.press(win, 5, 5, Button::Right);
    f.backend.release(win, 5, 5, Button::Right);
    f.backend.flush(&mut f.daemon);
    assert_eq!(*f.gallery.borrow(), vec![Some("weather".to_string())]);
}

#[test]
fn locked_widgets_do_not_move() {
    let mut f = fixture();
    f.daemon.settings.lock = true;
    let win = f.window("clock");
    let start = f.pos(win);
    f.drag(win, 200, 100);
    assert_eq!(f.pos(win), start);
}

#[test]
fn the_window_manager_moving_a_window_snaps_it_once_it_stops() {
    let mut f = fixture();
    let win = f.window("clock");
    let pitch = f.daemon.grid.pitch;
    let (x, y) = f.pos(win);
    f.backend.wm_move(win, x + pitch + 13, y + 9);
    f.backend.advance(&mut f.daemon, Duration::from_millis(100));
    assert_eq!(f.pos(win), (x + pitch + 13, y + 9), "not yet: it may still be moving");
    f.backend.advance(&mut f.daemon, Duration::from_millis(500));
    let (col, row) = f.layout_of("clock");
    assert_eq!(f.pos(win), f.cell_xy(col, row));
}

#[test]
fn a_removed_widget_ignores_late_events() {
    let mut f = fixture();
    let win = f.window("clock");
    f.call("remove", json!({"id": "clock"})).unwrap();
    f.backend.inject(Event::Button { win, button: Button::Left, pressed: true, x: 1, y: 1, root_x: 1, root_y: 1 });
    f.backend.inject(Event::Motion { win, x: 50, y: 50, root_x: 50, root_y: 50 });
    f.backend.inject(Event::Moved { win, x: 5, y: 5 });
    f.backend.flush(&mut f.daemon);
}

#[test]
fn the_grid_overlay_can_be_turned_off() {
    let mut f = fixture();
    f.daemon.settings.show_grid = false;
    let win = f.window("clock");
    let (x, y) = f.pos(win);
    f.backend.press(win, 5, 5, Button::Left);
    f.backend.motion_root(win, x + 80, y + 80);
    f.backend.flush(&mut f.daemon);
    assert_eq!(f.overlays(), 0);
}

#[test]
fn a_resolution_change_lays_the_widgets_out_again() {
    let mut f = fixture();
    f.backend.screen = (900, 700);
    f.backend.inject(Event::ScreenChanged);
    f.backend.advance(&mut f.daemon, Duration::from_millis(1000));
    assert!(f.daemon.grid.cols <= 4);
    for inst in &f.daemon.layout.instances {
        assert!(f.daemon.grid.fits(inst.col, inst.row, &inst.size));
    }
    for (_, w) in f.backend.open_windows().iter().filter(|(_, w)| !w.click_through) {
        assert!(w.x < 900);
    }
}

#[test]
fn high_dpi_scales_cells() {
    let mut f = fixture();
    let small = f.daemon.grid.cell;
    f.backend.scale = 2.0;
    f.backend.inject(Event::ScreenChanged);
    f.backend.advance(&mut f.daemon, Duration::from_millis(1000));
    assert_eq!(f.daemon.grid.cell, small * 2);
}

#[test]
fn tray_hotkey_and_notifications_go_through_the_backend() {
    let mut f = fixture();
    assert!(f.backend.tray.is_some(), "a tray icon is offered where the backend has one");
    f.call("notify", json!({"title": "Hi", "body": "there"})).unwrap();
    assert_eq!(f.backend.notifications, vec![("Hi".to_string(), "there".to_string())]);
    f.backend.inject(Event::TrayItem(0));
    f.backend.inject(Event::Hotkey);
    f.backend.flush(&mut f.daemon);
    assert_eq!(f.gallery.borrow().len(), 2);
    f.backend.inject(Event::TrayItem(3));
    f.backend.flush(&mut f.daemon);
    assert!(f.backend.quit_requested);
}

// ---------------------------------------------------------------------------------------------
/// The real socket: a request from another thread reaches the loop thread.
struct Running {
    _dir: tempfile::TempDir,
    root: std::path::PathBuf,
    quit: Option<std::sync::mpsc::Sender<()>>,
    thread: Option<std::thread::JoinHandle<()>>,
}

fn start_service(root: &std::path::Path) -> (Option<std::sync::mpsc::Sender<()>>, std::thread::JoinHandle<()>, std::sync::mpsc::Receiver<bool>) {
    let (quit_tx, quit_rx) = channel::<()>();
    let (ready_tx, ready_rx) = channel::<bool>();
    let root = root.to_path_buf();
    let thread = std::thread::spawn(move || {
        paths::use_root_for_this_thread(&root);
        deskwidgets::core::i18n::set_lang("en");
        let mut backend = NullBackend::new((1920, 1080), 1.0);
        let (tx, rx) = channel();
        let server = match ControlServer::start(tx, deskwidgets::platform::Host::waker(&backend)) {
            Ok(s) => s,
            Err(_) => {
                let _ = ready_tx.send(false);
                return;
            }
        };
        let mut daemon = Daemon::start_with(&mut backend, rx, Rc::new(ServiceHub::disabled()));
        let waker = deskwidgets::platform::Host::waker(&backend);
        std::thread::spawn(move || {
            let _ = quit_rx.recv();
            waker.wake();
        });
        let _ = ready_tx.send(true);
        backend.run(&mut daemon);
        daemon.shutdown(&mut backend);
        server.stop();
    });
    (Some(quit_tx), thread, ready_rx)
}

fn running() -> Running {
    let dir = common::isolated();
    let root = dir.path().to_path_buf();
    let (quit, thread, ready) = start_service(&root);
    assert!(ready.recv_timeout(Duration::from_secs(30)).unwrap());
    Running { _dir: dir, root, quit, thread: Some(thread) }
}

impl Drop for Running {
    fn drop(&mut self) {
        let _ = ipc::request("quit", json!({}), Duration::from_secs(5));
        let _ = self.quit.take();
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
        let _ = &self.root;
    }
}

#[test]
fn requests_round_trip_over_the_socket() {
    let _s = running();
    assert!(ipc::is_running());
    let id = ipc::request("add", json!({"type": "clock"}), Duration::from_secs(10)).unwrap();
    let list = ipc::request("list", json!({}), Duration::from_secs(10)).unwrap();
    assert!(list.as_array().unwrap().iter().any(|i| i["id"] == id));
    let error = ipc::request("add", json!({"type": "nope"}), Duration::from_secs(10)).unwrap_err();
    assert!(error.to_string().contains("No such widget"), "{error}");
}

#[test]
fn quit_ends_the_loop_and_removes_the_socket() {
    let mut s = running();
    ipc::request("quit", json!({}), Duration::from_secs(5)).unwrap();
    s.thread.take().unwrap().join().unwrap();
    assert!(!ipc::is_running());
}

#[test]
fn a_second_service_is_refused() {
    let s = running();
    let (_, thread, ready) = start_service(&s.root);
    assert!(!ready.recv_timeout(Duration::from_secs(30)).unwrap(), "refused: one is running");
    thread.join().unwrap();
    assert!(ipc::is_running());
}

#[test]
fn a_client_with_the_wrong_key_is_rejected_and_garbage_does_not_hurt() {
    let s = running();
    let key_file = paths::auth_key();
    let key = std::fs::read_to_string(&key_file).unwrap();
    std::fs::write(&key_file, "wrong").unwrap();
    assert!(ipc::request("ping", json!({}), Duration::from_secs(2)).is_err());
    std::fs::write(&key_file, key).unwrap();
    assert!(ipc::is_running());
    #[cfg(unix)]
    {
        use std::io::Write;
        let mut garbage = std::os::unix::net::UnixStream::connect(deskwidgets::service::ipc::socket_address()).unwrap();
        garbage.write_all(b"\x00\x01 not json\n").unwrap();
    }
    assert!(ipc::is_running());
    drop(s);
}

#[test]
fn a_stale_socket_is_replaced() {
    let dir = common::isolated();
    #[cfg(unix)]
    {
        // a socket file nobody listens on, left behind by a crashed service
        let stale = deskwidgets::service::ipc::socket_address();
        std::fs::create_dir_all(stale.parent().unwrap()).unwrap();
        drop(std::os::unix::net::UnixListener::bind(&stale).unwrap());
        assert!(stale.exists());
    }
    let (quit, thread, ready) = start_service(dir.path());
    assert!(ready.recv_timeout(Duration::from_secs(30)).unwrap());
    assert!(ipc::is_running());
    ipc::request("quit", json!({}), Duration::from_secs(5)).unwrap();
    drop(quit);
    thread.join().unwrap();
}
