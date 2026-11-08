//! One widget instance: its own Lua state, drawn into a surface.
//!
//! ```text
//! let ctx = WidgetContext::new(inst, wtype, "en", 1.0, (202, 202), fonts, sysinfo);
//! let mut runtime = WidgetRuntime::new(ctx)?;
//! let surface = runtime.render();      // premultiplied RGBA, ready for a window or a PNG
//! runtime.click(x, y);
//! ```
//!
//! Needs no window, no display and no GUI toolkit. Windows, timers and the like belong to `platform`.
use std::cell::RefCell;
use std::rc::Rc;
use std::sync::Arc;

use mlua::{Function, Lua, Table};
use serde_json::{Map, Value};
use tiny_skia::Color;

use super::cairo_api::{self, Cr};
use super::canvas::{new_surface, Context, Surface};
use super::fonts::FontMap;
use super::hostapi;
use super::images::ImageCache;
use super::sysinfo::SysInfo;
use crate::core::catalog::{self, WidgetType};
use crate::core::layout::Instance;
use crate::core::{paths, util::log};

/// Live data of the background services (media, events ...) as the widgets see it.
pub trait Services {
    fn snapshot(&self, name: &str) -> Option<Value>;
    fn media_control(&self, command: &str) -> bool;
}

/// Stand-in for the background services when there are none, e.g. in previews.
pub struct NoServices;

impl Services for NoServices {
    fn snapshot(&self, _name: &str) -> Option<Value> {
        None
    }
    fn media_control(&self, _command: &str) -> bool {
        false
    }
}

#[derive(Clone)]
pub struct WidgetContext {
    pub inst: Instance, // the layout entry: id, size, style, accent, options
    pub wtype: WidgetType,
    pub lang: String,
    pub scale: f64,
    pub size_px: (u32, u32), // window size in pixels
    pub fonts: Arc<FontMap>,
    pub sysinfo: SysInfo,
    pub images: Rc<RefCell<ImageCache>>,
    pub services: Rc<dyn Services>,
    pub demo: bool, // show sample data (gallery thumbnails, previews)
    pub notify: Rc<dyn Fn(&str, &str)>,
}

impl WidgetContext {
    pub fn new(inst: Instance, wtype: WidgetType, lang: &str, scale: f64, size_px: (u32, u32), fonts: Arc<FontMap>, sysinfo: SysInfo) -> WidgetContext {
        WidgetContext {
            inst,
            wtype,
            lang: lang.to_string(),
            scale,
            size_px,
            fonts,
            sysinfo,
            images: Rc::new(RefCell::new(ImageCache::new())),
            services: Rc::new(NoServices),
            demo: false,
            notify: Rc::new(|_, _| {}),
        }
    }

    pub fn accent(&self) -> String {
        catalog::resolved_accent(&self.inst, &self.wtype)
    }

    /// Option values the way Lua reads them (L.opt): everything is a string; on/off is "1"/"0".
    pub fn option_strings(&self) -> Map<String, Value> {
        let values = catalog::option_values(&self.inst, &self.wtype, Some(&self.lang));
        let mut out = Map::new();
        for (key, value) in values {
            let text = match &value {
                Value::Bool(b) => if *b { "1".to_string() } else { "0".to_string() },
                Value::Array(items) => items.iter().map(|v| v.as_str().map(str::to_string).unwrap_or_else(|| v.to_string())).collect::<Vec<_>>().join(","),
                Value::String(s) => s.clone(),
                Value::Number(n) if n.is_f64() && n.as_f64().map(|f| f.fract() == 0.0).unwrap_or(false) => format!("{:.1}", n.as_f64().unwrap()),
                other => other.to_string(),
            };
            if self.wtype.option(&key).map(|o| o.kind == "file").unwrap_or(false) {
                // 1.x/2.x widgets read the converted copy; any picture works now
                out.insert(format!("{key}_png"), Value::String(text.clone()));
            }
            out.insert(key, Value::String(text));
        }
        out
    }
}

pub struct WidgetRuntime {
    pub ctx: WidgetContext,
    lua: Lua,
    main: Option<Table>,
    surface: Surface,
}

impl WidgetRuntime {
    pub fn new(ctx: WidgetContext) -> Result<WidgetRuntime, String> {
        let (width, height) = ctx.size_px;
        let surface = new_surface(width, height).ok_or("the widget is too large")?;
        let lua = Lua::new();
        let mut runtime = WidgetRuntime { ctx, lua, main: None, surface };
        runtime.start();
        Ok(runtime)
    }

    fn start(&mut self) {
        let result = (|| -> mlua::Result<Table> {
            let lua = &self.lua;
            cairo_api::install(lua, self.ctx.fonts.clone())?;
            let host = hostapi::table(lua, &self.ctx)?;
            lua.globals().set("host", host)?;
            // `require 'lib'` finds the engine's library; widget folders may ship modules of their own
            let lua_dir = paths::lua().to_string_lossy().replace('\\', "/");
            lua.load("package.path = ... .. '/?.lua;' .. host.widget_dir .. '/?.lua;' .. package.path")
                .call::<()>(lua_dir)?;
            lua.load("return require('main')").eval::<Table>()
        })();
        match result {
            Ok(main) => self.main = Some(main),
            Err(e) => log(format!("{}: cannot start the widget: {e}", self.ctx.inst.id)), // lib.lua itself is broken
        }
    }

    /// Evaluates a chunk of Lua in this widget's state and returns its result as JSON (tests, tools).
    pub fn eval_json(&self, code: &str) -> Value {
        use mlua::LuaSerdeExt;
        match self.lua.load(code).eval::<mlua::Value>() {
            Ok(value) => self.lua.from_value::<Value>(value).unwrap_or(Value::Null),
            Err(e) => Value::String(format!("error: {e}")),
        }
    }

    pub fn failed(&self) -> bool {
        self.main.is_none()
    }

    /// Runs tick() and draw(); returns the surface (reused: copy it before the next call).
    pub fn render(&mut self) -> Surface {
        self.surface.borrow_mut().fill(Color::TRANSPARENT);
        if let Some(main) = &self.main {
            let cr = Cr(Rc::new(RefCell::new(Context::new(self.surface.clone(), self.ctx.fonts.clone()))));
            let outcome = main.get::<Function>("frame").and_then(|frame| frame.call::<()>(cr));
            if let Err(e) = outcome {
                log(format!("{}: {e}", self.ctx.inst.id));
            }
        }
        self.surface.clone()
    }

    /// Left click at window pixel (x, y). True when the widget handled it.
    pub fn click(&self, x: f64, y: f64) -> bool {
        let Some(main) = &self.main else { return false };
        match main.get::<Function>("click").and_then(|click| click.call::<mlua::Value>((x, y))) {
            Ok(mlua::Value::Boolean(b)) => b,
            Ok(mlua::Value::Nil) => false,
            Ok(_) => true,
            Err(e) => {
                log(format!("{}: {e}", self.ctx.inst.id));
                false
            }
        }
    }
}
