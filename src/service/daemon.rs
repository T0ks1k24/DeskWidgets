//! The service. One process, one loop: it owns every widget window.
//!
//! * draws each widget instance in its own window and redraws it on its interval
//! * a widget dragged with the mouse (or moved with Super+drag by the window manager) snaps to the
//!   nearest free grid cell when you let go; the grid is shown while you drag
//! * feeds widgets live data (`providers`: media player, calendars, Docker, batteries)
//! * is controlled through `service/ipc.rs`: add, remove, configure, move, arrange, reload, settings,
//!   gallery, notify, list, status, quit
use std::cell::RefCell;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{channel, Receiver};
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Value};

use super::instance::WidgetInstance;
use super::ipc::{ControlServer, Request};
use super::legacy;
use super::overlay::Overlay;
use crate::core::catalog::{self, Types};
use crate::core::grid::{round_half_even, Grid};
use crate::core::i18n::{set_lang, tr, tr_owned};
use crate::core::layout::Layout;
use crate::core::settings::{self, Settings};
use crate::core::util::log;
use crate::core::paths;
use crate::engine::fonts::FontMap;
use crate::engine::images::ImageCache;
use crate::engine::sysinfo::SysInfo;
use crate::platform::{self, App, Event, Host, TimerId, WinId};
use crate::providers::ServiceHub;
use crate::VERSION;

pub const GALLERY_HOTKEY: &str = "ctrl+alt+w";

#[derive(Clone, Debug, PartialEq)]
pub enum TimerAction {
    Frame(String),
    Settle(String),
    MeasureOffset(String),
    Screen,
    Quit,
}

pub struct Daemon {
    pub settings: Settings,
    pub lang: String,
    pub fonts: Arc<FontMap>,
    pub sysinfo: SysInfo,
    pub images: Rc<RefCell<ImageCache>>,
    pub hub: Rc<ServiceHub>,
    pub types: Types,
    pub layout: Layout,
    pub grid: Grid,
    pub origin: (i32, i32),
    pub instances: BTreeMap<String, WidgetInstance>,
    pub windows: HashMap<WinId, String>,
    pub overlay: Overlay,
    pub tray: bool,
    /// What opens the gallery (tests replace it).
    pub gallery_launcher: Box<dyn FnMut(Option<&str>)>,
    pub notifications: Rc<RefCell<Vec<(String, String)>>>,
    requests: Receiver<Request>,
    server: Option<ControlServer>,
    timers: HashMap<u64, TimerAction>,
    next_token: u64,
    screen_timer: Option<TimerId>,
    pub quit_flag: Arc<AtomicBool>,
}

impl Daemon {
    /// Loads everything and puts the widgets on the screen.
    pub fn start(host: &mut dyn Host, requests: Receiver<Request>) -> Daemon {
        Daemon::start_with(host, requests, Rc::new(ServiceHub::new()))
    }

    pub fn start_with(host: &mut dyn Host, requests: Receiver<Request>, hub: Rc<ServiceHub>) -> Daemon {
        paths::ensure_dirs();
        let settings = settings::load();
        let lang = settings::language(&settings);
        set_lang(&lang);
        let fonts = Arc::new(FontMap::new(&settings.font)); // before anything is drawn
        let types = catalog::discover();
        let (grid, origin) = build_grid(host, &settings);
        let mut layout = Layout::new(grid.clone(), types.clone(), settings.clone());
        layout.load();
        layout.save();
        let mut daemon = Daemon {
            settings,
            lang,
            fonts,
            sysinfo: SysInfo::new(true),
            images: Rc::new(RefCell::new(ImageCache::new())),
            hub,
            types,
            layout,
            grid,
            origin,
            instances: BTreeMap::new(),
            windows: HashMap::new(),
            overlay: Overlay::default(),
            tray: false,
            gallery_launcher: Box::new(open_gallery_process),
            notifications: Rc::new(RefCell::new(Vec::new())),
            requests,
            server: None,
            timers: HashMap::new(),
            next_token: 0,
            screen_timer: None,
            quit_flag: Arc::new(AtomicBool::new(false)),
        };
        daemon.sync_services();
        let ids: Vec<String> = daemon.layout.instances.iter().map(|i| i.id.clone()).collect();
        for id in ids {
            daemon.start_instance(host, &id);
        }
        daemon.setup_desktop_integration(host);
        log(format!("DeskWidgets {VERSION}: {} widgets on a {}×{} grid", daemon.instances.len(), daemon.grid.cols, daemon.grid.rows));
        daemon
    }

    pub fn shutdown(&mut self, host: &mut dyn Host) {
        let ids: Vec<String> = self.instances.keys().cloned().collect();
        for id in ids {
            self.stop_instance(host, &id);
        }
        self.overlay_hide(host);
        self.hub.stop();
        self.sysinfo.stop();
        if let Some(server) = self.server.take() {
            server.stop();
        }
    }

    // timers
    pub fn after(&mut self, host: &mut dyn Host, delay: Duration, action: TimerAction) -> (TimerId, u64) {
        self.next_token += 1;
        let token = self.next_token;
        self.timers.insert(token, action);
        (host.set_timer(delay, token), token)
    }

    pub fn cancel(&mut self, host: &mut dyn Host, timer: Option<(TimerId, u64)>) {
        if let Some((id, token)) = timer {
            self.timers.remove(&token);
            host.cancel_timer(id);
        }
    }

    // settings and geometry
    pub fn window_xy(&self, col: i32, row: i32) -> (i32, i32) {
        let (gx, gy) = self.grid.window_xy(col, row);
        (gx + self.origin.0, gy + self.origin.1)
    }

    /// The cell nearest to a window at `pos` (already corrected by the window manager's offset).
    pub fn nearest_cell(&self, id: &str, pos: (i32, i32)) -> (i32, i32) {
        let Some(instance) = self.instances.get(id) else { return (0, 0) };
        let size = self.layout.get(id).map(|i| i.size.clone()).unwrap_or_else(|| "small".into());
        let (x, y) = instance.window_origin(pos);
        self.grid.nearest_cell(x - self.origin.0, y - self.origin.1, &size)
    }

    fn rebuild_grid(&mut self, host: &mut dyn Host) {
        let (grid, origin) = build_grid(host, &self.settings);
        self.grid = grid;
        self.origin = origin;
    }

    /// A monitor was plugged in or the resolution changed: lay the widgets out again (debounced).
    fn screen_changed(&mut self, host: &mut dyn Host) {
        let previous = self.screen_timer.take().map(|id| (id, 0));
        if let Some((id, _)) = previous {
            host.cancel_timer(id);
        }
        let (id, _) = self.after(host, Duration::from_millis(800), TimerAction::Screen);
        self.screen_timer = Some(id);
    }

    fn apply_screen(&mut self, host: &mut dyn Host) {
        self.screen_timer = None;
        self.rebuild_grid(host);
        self.layout.regrid(self.grid.clone());
        self.layout.save();
        self.restart_all(host);
    }

    // instances
    pub fn start_instance(&mut self, host: &mut dyn Host, id: &str) {
        self.instances.entry(id.to_string()).or_insert_with(|| WidgetInstance::new(id));
        self.instance_start(host, id);
    }

    pub fn stop_instance(&mut self, host: &mut dyn Host, id: &str) {
        if self.instances.contains_key(id) {
            self.instance_stop(host, id);
            self.instances.remove(id);
        }
    }

    pub fn restart_all(&mut self, host: &mut dyn Host) {
        let ids: Vec<String> = self.layout.instances.iter().map(|i| i.id.clone()).collect();
        for id in ids {
            if self.instances.contains_key(&id) {
                self.instance_restart(host, &id);
            } else {
                self.start_instance(host, &id);
            }
        }
        self.sync_services();
    }

    pub fn sync_services(&self) {
        let mut wanted: HashSet<String> = HashSet::new();
        for inst in &self.layout.instances {
            if let Some(t) = self.types.get(&inst.kind) {
                if t.supported() {
                    wanted.extend(t.uses.iter().cloned());
                }
            }
        }
        self.hub.sync(&wanted);
    }

    // desktop
    pub fn open_gallery(&mut self, configure: Option<&str>) {
        (self.gallery_launcher)(configure);
    }

    pub fn notify(&mut self, host: &mut dyn Host, title: &str, body: &str) {
        if !(self.tray && host.tray_notify(title, body)) {
            crate::platform::integration::notify(title, body);
        }
    }

    pub fn flush_notifications(&mut self, host: &mut dyn Host) {
        let pending: Vec<(String, String)> = self.notifications.borrow_mut().drain(..).collect();
        for (title, body) in pending {
            self.notify(host, &title, &body);
        }
    }

    /// The tray icon and the gallery hotkey where the backend can provide them (Windows).
    fn setup_desktop_integration(&mut self, host: &mut dyn Host) {
        let items = vec![
            Some(tr("Відкрити галерею", "Open the gallery").to_string()),
            Some(tr("Розставити по сітці", "Arrange on the grid").to_string()),
            None,
            Some(tr("Вийти", "Quit").to_string()),
        ];
        self.tray = host.create_tray("DeskWidgets", &paths::icon_png(), items);
        if cfg!(windows) && self.settings.hotkey && !host.register_hotkey(GALLERY_HOTKEY) {
            log(format!("the hotkey {GALLERY_HOTKEY} is taken by another program"));
        }
    }

    // the control interface (ipc.rs); every handler returns something JSON-able
    pub fn handle(&mut self, host: &mut dyn Host, command: &str, args: &Value) -> Result<Value, String> {
        let text = |key: &str| args.get(key).and_then(Value::as_str).map(str::to_string);
        let need = |key: &str| text(key).ok_or_else(|| format!("missing argument: {key}"));
        match command {
            "ping" => Ok(json!("pong")),
            "status" => {
                let running: Vec<&String> = self.instances.iter().filter(|(_, w)| w.running()).map(|(id, _)| id).collect();
                Ok(json!({"version": VERSION, "pid": std::process::id(), "widgets": self.instances.len(), "running": running}))
            }
            "list" => Ok(serde_json::to_value(&self.layout.instances).unwrap_or(Value::Null)),
            "add" => self.cmd_add(host, &need("type")?),
            "remove" => self.cmd_remove(host, &need("id")?),
            "configure" => self.cmd_configure(host, &need("id")?, args.get("patch").unwrap_or(&Value::Null)),
            "move" => {
                let (col, row) = (args.get("col").and_then(Value::as_i64), args.get("row").and_then(Value::as_i64));
                let (Some(col), Some(row)) = (col, row) else { return Err("missing argument: col, row".into()) };
                self.cmd_move(host, &need("id")?, col as i32, row as i32)
            }
            "arrange" => {
                self.cmd_arrange(host);
                Ok(Value::Null)
            }
            "reload" => {
                self.cmd_reload(host);
                Ok(Value::Null)
            }
            "settings" => {
                self.cmd_settings(host);
                Ok(Value::Null)
            }
            "gallery" => {
                self.open_gallery(text("configure").as_deref());
                Ok(Value::Null)
            }
            "notify" => {
                self.notify(host, &need("title")?, &text("body").unwrap_or_default());
                Ok(Value::Null)
            }
            "quit" => {
                self.after(host, Duration::from_millis(50), TimerAction::Quit);
                Ok(Value::Null)
            }
            other => Err(format!("unknown command: {other}")),
        }
    }

    fn cmd_add(&mut self, host: &mut dyn Host, type_id: &str) -> Result<Value, String> {
        if !self.types.contains_key(type_id) {
            self.types = catalog::discover();
            self.layout.reload_types(self.types.clone());
        }
        let Some(t) = self.types.get(type_id).cloned() else {
            return Err(tr_owned(format!("Немає такого віджета: {type_id}"), format!("No such widget: {type_id}")));
        };
        if !t.supported() {
            return Err(tr_owned(format!("«{}» не працює в цій системі", t.title()), format!("\"{}\" does not work on this system", t.title())));
        }
        let Some(inst) = self.layout.add(type_id) else {
            return Err(tr("На сітці немає місця", "The grid is full").to_string());
        };
        self.layout.save();
        self.start_instance(host, &inst.id);
        self.sync_services();
        log(format!("{}: added at ({}, {})", inst.id, inst.col, inst.row));
        Ok(json!(inst.id))
    }

    fn cmd_remove(&mut self, host: &mut dyn Host, id: &str) -> Result<Value, String> {
        if self.layout.remove(id).is_none() {
            return Err(format!("no such widget: {id}"));
        }
        self.stop_instance(host, id);
        self.layout.save();
        self.sync_services();
        log(format!("{id}: removed"));
        Ok(Value::Null)
    }

    fn cmd_configure(&mut self, host: &mut dyn Host, id: &str, patch: &Value) -> Result<Value, String> {
        let Some(inst) = self.layout.configure(id, patch) else { return Err(format!("no such widget: {id}")) };
        self.layout.save();
        if self.instances.contains_key(id) {
            self.instance_restart(host, id);
            self.place(host, id, inst.col, inst.row);
        } else {
            self.start_instance(host, id);
        }
        Ok(Value::Null)
    }

    fn cmd_move(&mut self, host: &mut dyn Host, id: &str, col: i32, row: i32) -> Result<Value, String> {
        if self.layout.get(id).is_none() {
            return Err(format!("no such widget: {id}"));
        }
        let Some((c, r)) = self.layout.move_to(id, col, row) else { return Err(tr("Тут немає місця", "No room there").to_string()) };
        if self.instances.contains_key(id) {
            self.place(host, id, c, r);
        }
        self.layout.save();
        Ok(json!([c, r]))
    }

    /// Packs every widget onto the grid again, outer columns first.
    fn cmd_arrange(&mut self, host: &mut dyn Host) {
        for id in self.layout.arrange() {
            if let Some(inst) = self.layout.get(&id).cloned() {
                if self.instances.contains_key(&id) {
                    self.place(host, &id, inst.col, inst.row);
                }
            }
        }
        self.layout.save();
        log("widgets arranged");
    }

    /// Re-reads the widget folders (new or edited widgets) and restarts every widget.
    fn cmd_reload(&mut self, host: &mut dyn Host) {
        self.types = catalog::discover();
        self.layout.reload_types(self.types.clone());
        let gone: Vec<String> = self
            .instances
            .keys()
            .filter(|id| self.layout.get(id).map(|i| !self.types.contains_key(&i.kind)).unwrap_or(true))
            .cloned()
            .collect();
        for id in gone {
            self.stop_instance(host, &id);
        }
        self.restart_all(host);
        log("widgets reloaded");
    }

    /// Settings were changed in the gallery: apply them.
    fn cmd_settings(&mut self, host: &mut dyn Host) {
        let old_font = self.settings.font.clone();
        self.settings = settings::load();
        self.lang = settings::language(&self.settings);
        set_lang(&self.lang);
        if self.settings.font != old_font {
            self.fonts = Arc::new(FontMap::new(&self.settings.font));
        }
        self.rebuild_grid(host);
        self.layout.settings = self.settings.clone();
        self.layout.regrid(self.grid.clone());
        self.layout.save();
        self.restart_all(host);
        log("settings applied");
    }

    fn drain_requests(&mut self, host: &mut dyn Host) {
        while let Ok(request) = self.requests.try_recv() {
            let result = self.handle(host, &request.command, &request.args);
            let _ = request.reply.send(result);
        }
    }
}

impl App for Daemon {
    fn event(&mut self, host: &mut dyn Host, event: Event) {
        match event {
            Event::Button { win, button, pressed, x, y, root_x, root_y } => self.on_button(host, win, button, pressed, (x, y), (root_x, root_y)),
            Event::Motion { win, root_x, root_y, .. } => self.on_motion(host, win, (root_x, root_y)),
            Event::Moved { win, .. } => self.on_moved(host, win),
            Event::Closed { win } => self.on_closed(host, win),
            Event::ScreenChanged => self.screen_changed(host),
            Event::Timer(token) => match self.timers.remove(&token) {
                Some(TimerAction::Frame(id)) => self.frame(host, &id),
                Some(TimerAction::Settle(id)) => self.settled(host, &id),
                Some(TimerAction::MeasureOffset(id)) => self.measure_offset(host, &id),
                Some(TimerAction::Screen) => self.apply_screen(host),
                Some(TimerAction::Quit) => host.quit(),
                None => {}
            },
            Event::Wake => {
                if self.quit_flag.load(Ordering::SeqCst) {
                    host.quit();
                }
                self.drain_requests(host);
            }
            Event::Hotkey => self.open_gallery(None),
            Event::TrayItem(index) => match index {
                0 => self.open_gallery(None),
                1 => self.cmd_arrange(host),
                3 => host.quit(),
                _ => {}
            },
        }
    }
}

fn build_grid(host: &mut dyn Host, s: &Settings) -> (Grid, (i32, i32)) {
    let (x, y, width, height) = host.work_area();
    let dpi = host.dpi_scale();
    let px = |v: i32| round_half_even(v as f64 * dpi);
    (Grid::new(width, height, s.scale * dpi, px(s.gap), px(s.margin_side), px(s.margin_top), px(s.margin_bottom)), (x, y))
}

/// Starts `deskwidgets gallery` as a separate process.
pub fn open_gallery_process(configure: Option<&str>) {
    let mut args = vec!["gallery".to_string()];
    if let Some(id) = configure {
        args.extend(["--configure".to_string(), id.to_string()]);
    }
    crate::engine::hostapi::run_background(&paths::exe().to_string_lossy(), &args, None);
}

/// Runs until `quit`. Returns the process exit code.
pub fn run() -> i32 {
    paths::ensure_dirs();
    legacy::stop_old_versions();
    let mut backend = match platform::create_backend() {
        Ok(b) => b,
        Err(e) => {
            log(format!("cannot start: {e}"));
            return 1;
        }
    };
    let (tx, rx) = channel();
    let server = match ControlServer::start(tx, backend.waker()) {
        Ok(server) => server,
        Err(Ok(_already)) => {
            log("DeskWidgets is already running");
            return 0;
        }
        Err(Err(e)) => {
            log(format!("cannot start: {e}"));
            return 1;
        }
    };
    let mut daemon = Daemon::start(&mut *backend, rx);
    daemon.server = Some(server);
    let (flag, waker) = (daemon.quit_flag.clone(), backend.waker());
    let _ = ctrlc::set_handler(move || {
        flag.store(true, Ordering::SeqCst);
        waker.wake();
    });
    backend.run(&mut daemon);
    daemon.shutdown(&mut *backend);
    0
}
