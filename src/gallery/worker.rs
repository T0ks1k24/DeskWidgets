//! Background threads of the gallery: drawing widget pictures and talking to the service.
//!
//! Pictures come from the same engine the service uses, drawn on a thread of their own so the window never
//! waits for Lua; every request names a key, and only the newest request per key is drawn.
use std::collections::HashMap;
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::Arc;
use std::time::Duration;

use eframe::egui;
use serde_json::Value;

use crate::core::catalog::WidgetType;
use crate::core::settings::Settings;
use crate::engine::fonts::FontMap;
use crate::engine::render::{self, Options};
use crate::service::ipc;

pub enum Job {
    Render { key: String, wtype: Box<WidgetType>, opts: Box<Options>, settings: Box<Settings> },
    Fonts(String),
}

pub struct Done {
    pub key: String,
    pub image: egui::ColorImage,
}

pub struct Renderer {
    pub jobs: Sender<Job>,
    pub done: Receiver<Done>,
}

pub fn spawn_renderer(ctx: egui::Context, font: String) -> Renderer {
    let (jobs, rx) = channel::<Job>();
    let (tx, done) = channel::<Done>();
    std::thread::Builder::new()
        .name("gallery-render".into())
        .spawn(move || {
            let mut fonts = Arc::new(FontMap::new(&font));
            while let Ok(first) = rx.recv() {
                // the newest request per key wins; previews go first
                let mut batch: Vec<Job> = vec![first];
                batch.extend(rx.try_iter());
                let mut latest: HashMap<String, Job> = HashMap::new();
                let mut order: Vec<String> = Vec::new();
                for job in batch {
                    match job {
                        Job::Fonts(family) => {
                            fonts = Arc::new(FontMap::new(&family));
                        }
                        Job::Render { ref key, .. } => {
                            if !latest.contains_key(key) {
                                order.push(key.clone());
                            }
                            latest.insert(key.clone(), job);
                        }
                    }
                }
                order.sort_by_key(|k| if k == "preview" { 0 } else { 1 });
                for key in order {
                    let Some(Job::Render { wtype, opts, settings, .. }) = latest.remove(&key) else { continue };
                    match render::render_surface(&wtype, &opts, &settings, fonts.clone(), None) {
                        Ok(surface) => {
                            let pixmap = surface.borrow();
                            let image = egui::ColorImage::from_rgba_premultiplied([pixmap.width() as usize, pixmap.height() as usize], pixmap.data());
                            if tx.send(Done { key, image }).is_err() {
                                return;
                            }
                            ctx.request_repaint();
                        }
                        Err(e) => crate::core::util::log(format!("{}: rendering failed: {e}", wtype.id)),
                    }
                }
            }
        })
        .ok();
    Renderer { jobs, done }
}

pub struct IpcCall {
    pub command: String,
    pub args: Value,
}

pub struct IpcAnswer {
    pub command: String,
    pub result: Result<Value, String>,
}

pub struct Ipc {
    pub calls: Sender<IpcCall>,
    pub answers: Receiver<IpcAnswer>,
}

/// Talks to the service in order, starting it first when needed.
pub fn spawn_ipc(ctx: egui::Context) -> Ipc {
    let (calls, rx) = channel::<IpcCall>();
    let (tx, answers) = channel::<IpcAnswer>();
    std::thread::Builder::new()
        .name("gallery-ipc".into())
        .spawn(move || {
            while let Ok(call) = rx.recv() {
                let result = if !ipc::ensure_running(Duration::from_secs(8)) {
                    Err(format!("The DeskWidgets service did not start (see {})", crate::core::paths::service_log().display()))
                } else {
                    ipc::request(&call.command, call.args, Duration::from_secs(6)).map_err(|e| e.to_string())
                };
                if tx.send(IpcAnswer { command: call.command, result }).is_err() {
                    return;
                }
                ctx.request_repaint();
            }
        })
        .ok();
    Ipc { calls, answers }
}
