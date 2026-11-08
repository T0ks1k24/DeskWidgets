//! One widget on the desktop: its Lua runtime, its window, its redraw timer, and what the mouse does to it.
use std::rc::Rc;
use std::time::{Duration, Instant};

use super::daemon::{Daemon, TimerAction};
use crate::core::catalog;
use crate::core::util::log;
use crate::engine::runtime::{Services, WidgetContext, WidgetRuntime};
use crate::platform::{Button, Host, TimerId, WinId};

const DRAG_THRESHOLD: i32 = 6; // pixels the pointer must travel before a click becomes a drag
const SETTLE_DELAY: Duration = Duration::from_millis(400); // without movement before a window moved by the WM snaps to the grid
const SLOW_FRAME: f64 = 0.25; // a frame slower than this (seconds) is reported: the widget blocks every other one
const MIN_INTERVAL: f64 = 0.2;

pub struct WidgetInstance {
    pub id: String,
    pub runtime: Option<WidgetRuntime>,
    pub window: Option<WinId>,
    pub timer: Option<(TimerId, u64)>,
    pub settle: Option<(TimerId, u64)>,
    pub offset: (i32, i32), // where the window manager put the window relative to where we asked
    pub requested: (i32, i32),
    press: Option<(i32, i32, (i32, i32))>, // (root_x, root_y, window position) while the left button is down
    dragging: bool,
    slow_reported: bool,
}

impl WidgetInstance {
    pub fn new(id: &str) -> WidgetInstance {
        WidgetInstance {
            id: id.to_string(),
            runtime: None,
            window: None,
            timer: None,
            settle: None,
            offset: (0, 0),
            requested: (0, 0),
            press: None,
            dragging: false,
            slow_reported: false,
        }
    }

    pub fn running(&self) -> bool {
        self.window.is_some()
    }

    /// Where the window would sit without the manager's shift: for finding the nearest cell.
    pub fn window_origin(&self, pos: (i32, i32)) -> (i32, i32) {
        (pos.0 - self.offset.0, pos.1 - self.offset.1)
    }
}

impl Daemon {
    // lifecycle
    pub(super) fn instance_start(&mut self, host: &mut dyn Host, id: &str) {
        let Some(inst) = self.layout.get(id).cloned() else { return };
        let Some(t) = self.types.get(&inst.kind).cloned() else { return };
        if self.instances.get(id).map(|i| i.window.is_some()).unwrap_or(true) || !t.supported() {
            return;
        }
        let (w, h) = self.grid.window_px(&inst.size);
        let size_px = (w as u32, h as u32);
        let mut ctx = WidgetContext::new(inst.clone(), t, &self.lang, self.grid.scale, size_px, self.fonts.clone(), self.sysinfo.clone());
        ctx.images = self.images.clone();
        ctx.services = self.hub.clone() as Rc<dyn Services>;
        let pending = self.notifications.clone();
        ctx.notify = Rc::new(move |title, body| pending.borrow_mut().push((title.to_string(), body.to_string())));
        let runtime = match WidgetRuntime::new(ctx) {
            Ok(r) => r,
            Err(e) => {
                log(format!("{id}: {e}")); // Lua failed to even start: the other widgets carry on
                return;
            }
        };
        let requested = self.window_xy(inst.col, inst.row);
        let win = match host.create_window(&format!("deskwidget:{id}"), size_px.0, size_px.1, requested.0, requested.1, false) {
            Ok(win) => win,
            Err(e) => {
                log(format!("{id}: cannot create the window: {e}"));
                return;
            }
        };
        self.windows.insert(win, id.to_string());
        if let Some(i) = self.instances.get_mut(id) {
            i.runtime = Some(runtime);
            i.window = Some(win);
            i.requested = requested;
            i.offset = (0, 0);
        }
        self.frame(host, id);
        let timer = self.after(host, Duration::from_millis(500), TimerAction::MeasureOffset(id.to_string()));
        let _ = timer;
    }

    pub(super) fn instance_stop(&mut self, host: &mut dyn Host, id: &str) {
        let (timer, settle, window) = match self.instances.get_mut(id) {
            Some(i) => (i.timer.take(), i.settle.take(), i.window.take()),
            None => return,
        };
        self.cancel(host, timer);
        self.cancel(host, settle);
        if let Some(win) = window {
            self.windows.remove(&win);
            host.close_window(win);
        }
        if let Some(i) = self.instances.get_mut(id) {
            i.runtime = None;
            i.press = None;
            i.dragging = false;
        }
    }

    pub(super) fn instance_restart(&mut self, host: &mut dyn Host, id: &str) {
        self.instance_stop(host, id);
        self.instance_start(host, id);
    }

    // drawing
    fn interval(&self, id: &str) -> f64 {
        let (Some(inst), true) = (self.layout.get(id), true) else { return 5.0 };
        let Some(t) = self.types.get(&inst.kind) else { return 5.0 };
        t.interval_for(&catalog::option_values(inst, t, None)).max(MIN_INTERVAL)
    }

    pub(super) fn frame(&mut self, host: &mut dyn Host, id: &str) {
        let Some(instance) = self.instances.get_mut(id) else { return };
        instance.timer = None;
        let (Some(runtime), Some(win)) = (instance.runtime.as_mut(), instance.window) else { return };
        let began = Instant::now();
        let surface = runtime.render();
        host.present(win, &surface.borrow());
        let took = began.elapsed().as_secs_f64();
        if took > SLOW_FRAME && !instance.slow_reported {
            instance.slow_reported = true;
            log(format!("{id}: a frame took {took:.2}s; slow work belongs in a background download or helper (see docs/creating-widgets.md)"));
        }
        self.flush_notifications(host);
        let interval = self.interval(id);
        let timer = self.after(host, Duration::from_secs_f64(interval), TimerAction::Frame(id.to_string()));
        if let Some(i) = self.instances.get_mut(id) {
            i.timer = Some(timer);
        }
    }

    /// Right after a click: show what it changed without waiting for the next interval.
    fn redraw_soon(&mut self, host: &mut dyn Host, id: &str) {
        let old = self.instances.get_mut(id).and_then(|i| i.timer.take());
        self.cancel(host, old);
        let timer = self.after(host, Duration::from_millis(50), TimerAction::Frame(id.to_string()));
        if let Some(i) = self.instances.get_mut(id) {
            i.timer = Some(timer);
        }
    }

    // position
    /// Moves the window onto a cell.
    pub fn place(&mut self, host: &mut dyn Host, id: &str, col: i32, row: i32) {
        let (x, y) = self.window_xy(col, row);
        if let Some(i) = self.instances.get_mut(id) {
            i.requested = (x, y);
            if let Some(win) = i.window {
                host.move_window(win, x + i.offset.0, y + i.offset.1);
            }
        }
    }

    /// Some window managers shift a new window (panels, constraints); remember by how much so snapping
    /// compares like with like.
    pub(super) fn measure_offset(&mut self, host: &mut dyn Host, id: &str) {
        let Some(win) = self.instances.get(id).and_then(|i| i.window) else { return };
        if let Some(pos) = host.position(win) {
            if let Some(i) = self.instances.get_mut(id) {
                let (dx, dy) = (pos.0 - i.requested.0, pos.1 - i.requested.1);
                i.offset = if dx.abs() <= 48 && dy.abs() <= 48 { (dx, dy) } else { (0, 0) };
            }
        }
    }

    fn window_position(&self, host: &mut dyn Host, id: &str) -> (i32, i32) {
        let Some(i) = self.instances.get(id) else { return (0, 0) };
        i.window.and_then(|w| host.position(w)).unwrap_or(i.requested)
    }

    // mouse
    pub(super) fn on_button(&mut self, host: &mut dyn Host, win: WinId, button: Button, pressed: bool, pos: (i32, i32), root: (i32, i32)) {
        let Some(id) = self.windows.get(&win).cloned() else { return };
        match button {
            Button::Right => {
                if !pressed {
                    self.open_gallery(Some(&id));
                }
            }
            Button::Left => {
                if pressed {
                    let at = self.window_position(host, &id);
                    if let Some(i) = self.instances.get_mut(&id) {
                        i.press = Some((root.0, root.1, at));
                        i.dragging = false;
                    }
                } else {
                    self.release(host, &id, pos);
                }
            }
            Button::Middle => {}
        }
    }

    pub(super) fn on_motion(&mut self, host: &mut dyn Host, win: WinId, root: (i32, i32)) {
        let Some(id) = self.windows.get(&win).cloned() else { return };
        if self.settings.lock {
            return;
        }
        let Some(i) = self.instances.get_mut(&id) else { return };
        let Some((sx, sy, (wx, wy))) = i.press else { return };
        let (dx, dy) = (root.0 - sx, root.1 - sy);
        let mut started = false;
        if !i.dragging {
            if dx * dx + dy * dy < DRAG_THRESHOLD * DRAG_THRESHOLD {
                return;
            }
            i.dragging = true;
            started = true;
        }
        let pos = (wx + dx, wy + dy);
        host.move_window(win, pos.0, pos.1);
        if started {
            self.overlay_show(host, &id);
        }
        self.overlay_update(host, &id, pos);
    }

    fn release(&mut self, host: &mut dyn Host, id: &str, pos: (i32, i32)) {
        let Some(i) = self.instances.get_mut(id) else { return };
        let dragging = std::mem::replace(&mut i.dragging, false);
        i.press = None;
        if dragging {
            let at = self.window_position(host, id);
            self.drop_widget(host, id, at);
        } else if let Some(runtime) = i.runtime.as_ref() {
            if runtime.click(pos.0 as f64, pos.1 as f64) {
                self.flush_notifications(host);
                self.redraw_soon(host, id);
            }
        }
    }

    /// The window manager moved the window (Super+drag on Linux): snap once it stops.
    pub(super) fn on_moved(&mut self, host: &mut dyn Host, win: WinId) {
        let Some(id) = self.windows.get(&win).cloned() else { return };
        if self.instances.get(&id).map(|i| i.press.is_some()).unwrap_or(true) {
            return;
        }
        let old = self.instances.get_mut(&id).and_then(|i| i.settle.take());
        self.cancel(host, old);
        let timer = self.after(host, SETTLE_DELAY, TimerAction::Settle(id.clone()));
        if let Some(i) = self.instances.get_mut(&id) {
            i.settle = Some(timer);
        }
    }

    pub(super) fn settled(&mut self, host: &mut dyn Host, id: &str) {
        let Some(i) = self.instances.get_mut(id) else { return };
        i.settle = None;
        if i.press.is_some() {
            return;
        }
        let Some(win) = i.window else { return };
        let expected = (i.requested.0 + i.offset.0, i.requested.1 + i.offset.1);
        if let Some(pos) = host.position(win) {
            if pos != expected {
                self.drop_widget(host, id, pos);
            }
        }
    }

    pub(super) fn on_closed(&mut self, _host: &mut dyn Host, win: WinId) {
        // the window was destroyed from outside: forget it, the next restart creates a new one
        if let Some(id) = self.windows.remove(&win) {
            if let Some(i) = self.instances.get_mut(&id) {
                i.window = None;
            }
        }
    }

    /// The widget was let go at window position `pos`: snap it to the nearest free cell.
    pub fn drop_widget(&mut self, host: &mut dyn Host, id: &str, pos: (i32, i32)) {
        self.overlay_hide(host);
        let Some(inst) = self.layout.get(id).cloned() else { return };
        let (col, row) = self.nearest_cell(id, pos);
        let spot = self.layout.move_to(id, col, row).unwrap_or((inst.col, inst.row));
        self.place(host, id, spot.0, spot.1);
        self.layout.save();
    }
}
