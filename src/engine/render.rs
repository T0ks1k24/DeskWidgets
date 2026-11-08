//! Headless rendering of a widget to a PNG: `deskwidgets preview`, gallery thumbnails, tests.
//!
//! No window and no display are needed, and nothing but the widget itself is started.
use std::path::Path;
use std::sync::Arc;

use serde_json::{Map, Value};

use super::canvas::Surface;
use super::fonts::FontMap;
use super::runtime::{WidgetContext, WidgetRuntime};
use super::sysinfo::SysInfo;
use crate::core::catalog::{Types, WidgetType};
use crate::core::grid::Grid;
use crate::core::layout::Instance;
use crate::core::settings::Settings;
use crate::core::util::log;

/// Only decides how big a cell is: previews do not depend on the real screen.
pub const SCREEN: (i32, i32) = (1920, 1080);

#[derive(Clone, Debug)]
pub struct Options {
    pub size: Option<String>,
    pub style: Option<String>,
    pub accent: Option<String>,
    pub lang: String,
    pub scale: f64,
    pub options: Map<String, Value>,
    pub demo: bool,
    pub frames: usize,
}

impl Default for Options {
    fn default() -> Self {
        Options { size: None, style: None, accent: None, lang: "uk".into(), scale: 1.0, options: Map::new(), demo: true, frames: 3 }
    }
}

/// Draws one frame of `wtype` and returns the surface.
pub fn render_surface(wtype: &WidgetType, opts: &Options, settings: &Settings, fonts: Arc<FontMap>, sysinfo: Option<SysInfo>) -> Result<Surface, String> {
    let size = opts.size.clone().unwrap_or_else(|| wtype.size().to_string());
    let inst = Instance {
        id: format!("preview-{}", wtype.id),
        kind: wtype.id.clone(),
        size: size.clone(),
        style: opts.style.clone().unwrap_or_else(|| wtype.style.clone()),
        accent: opts.accent.clone().unwrap_or_else(|| wtype.accent.clone()),
        options: opts.options.clone(),
        col: 0,
        row: 0,
    };
    let grid = Grid::from_settings(SCREEN.0, SCREEN.1, settings, opts.scale);
    let (w, h) = grid.window_px(&size);
    let mut ctx = WidgetContext::new(inst, wtype.clone(), &opts.lang, opts.scale, (w as u32, h as u32), fonts, sysinfo.unwrap_or_else(|| SysInfo::new(false)));
    ctx.demo = opts.demo;
    let mut runtime = WidgetRuntime::new(ctx)?;
    let mut surface = runtime.render();
    for _ in 1..opts.frames.max(1) {
        // widgets that sample values (rates, history) need a few ticks
        surface = runtime.render();
    }
    if !opts.demo {
        // live data comes from background downloads: give them a moment, then draw what arrived
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
        while super::http::pending() && std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_millis(200));
        }
        for _ in 0..3 {
            surface = runtime.render();
            std::thread::sleep(std::time::Duration::from_millis(300));
        }
    }
    Ok(surface)
}

/// Renders `wtype` into the file `out`. Returns true on success.
pub fn render_png(wtype: &WidgetType, out: &Path, opts: &Options, settings: &Settings, fonts: Arc<FontMap>) -> bool {
    let result = render_surface(wtype, opts, settings, fonts, None).and_then(|surface| {
        if let Some(parent) = out.parent() {
            std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let saved = surface.borrow().save_png(out).map_err(|e| e.to_string());
        saved
    });
    match result {
        Ok(()) => true,
        Err(e) => {
            log(format!("{}: rendering failed: {e}", wtype.id));
            false
        }
    }
}

/// Gallery thumbnails: one PNG per widget type. Returns true when all of them rendered.
pub fn render_all(out_dir: &Path, lang: &str, types: &Types, settings: &Settings, fonts: Arc<FontMap>) -> bool {
    let opts = Options { lang: lang.to_string(), ..Options::default() };
    let mut all = true;
    for t in types.values().filter(|t| t.supported()) {
        all &= render_png(t, &out_dir.join(format!("{}.png", t.id)), &opts, settings, fonts.clone());
    }
    all
}
