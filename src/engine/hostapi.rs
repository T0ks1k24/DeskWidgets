//! The `host` table: what the engine offers to a widget's Lua state besides Cairo.
//!
//! Widgets do not call it directly, `lua/lib.lua` wraps it into the `L` API
//! (L.sys, L.service, L.http, L.python, L.open_url ...). Everything here is OS-independent from the
//! widget's point of view; the OS-specific work lives in `platform`.
use std::path::MAIN_SEPARATOR;
use std::process::Stdio;
use std::time::Duration;

use mlua::{Lua, Result as LuaResult, Table, Value as LuaValue};
use serde_json::Value;

use super::cairo_api::Surf;
use super::compat;
use super::http::{self, Request};
use super::runtime::WidgetContext;
use super::sysinfo::SysInfo;
use crate::core::util::{self, log};
use crate::core::paths;

/// A JSON value as a Lua one: arrays start at 1, null becomes nil, integers stay integers.
pub fn json_to_lua(lua: &Lua, value: &Value) -> LuaResult<LuaValue> {
    Ok(match value {
        Value::Null => LuaValue::Nil,
        Value::Bool(b) => LuaValue::Boolean(*b),
        Value::Number(n) => match n.as_i64() {
            Some(i) => LuaValue::Integer(i),
            None => LuaValue::Number(n.as_f64().unwrap_or(0.0)),
        },
        Value::String(s) => LuaValue::String(lua.create_string(s)?),
        Value::Array(items) => {
            let table = lua.create_table_with_capacity(items.len(), 0)?;
            for (i, item) in items.iter().enumerate() {
                table.raw_set(i + 1, json_to_lua(lua, item)?)?;
            }
            LuaValue::Table(table)
        }
        Value::Object(map) => {
            let table = lua.create_table_with_capacity(0, map.len())?;
            for (k, v) in map {
                table.raw_set(k.as_str(), json_to_lua(lua, v)?)?;
            }
            LuaValue::Table(table)
        }
    })
}

fn lossy(s: &mlua::String) -> String {
    s.to_string_lossy().to_string()
}

fn strings(table: Option<Table>) -> Vec<String> {
    let Some(table) = table else { return Vec::new() };
    table.sequence_values::<mlua::String>().filter_map(|v| v.ok()).map(|s| lossy(&s)).collect()
}

/// Builds the `host` table for one widget.
pub fn table(lua: &Lua, ctx: &WidgetContext) -> LuaResult<Table> {
    let t = lua.create_table()?;
    let inst = &ctx.inst;
    let (width, height) = ctx.size_px;
    t.set("id", inst.id.as_str())?;
    t.set("type", ctx.wtype.id.as_str())?;
    t.set("size", inst.size.as_str())?;
    t.set("style", inst.style.as_str())?;
    t.set("accent", ctx.accent())?;
    t.set("lang", ctx.lang.as_str())?;
    t.set("demo", ctx.demo)?;
    t.set("scale", ctx.scale)?;
    t.set("width", width)?;
    t.set("height", height)?;
    t.set("widget_dir", ctx.wtype.dir.to_string_lossy().as_ref())?;
    t.set("run_dir", paths::run().to_string_lossy().as_ref())?;
    t.set("config_dir", paths::config().to_string_lossy().as_ref())?;
    t.set("home", paths::home().to_string_lossy().as_ref())?;
    t.set("exe", paths::exe().to_string_lossy().as_ref())?;
    t.set("os", if cfg!(windows) { "windows" } else { "linux" })?;
    t.set("sep", MAIN_SEPARATOR.to_string())?;

    let opts = lua.create_table()?;
    for (key, value) in ctx.option_strings() {
        opts.set(key, value.as_str().unwrap_or(""))?;
    }
    t.set("opts", opts)?;
    let fonts = lua.create_table()?;
    fonts.set("cjk", ctx.fonts.cjk())?;
    t.set("fonts", fonts)?;
    t.set("sys", sys_table(lua, &ctx.sysinfo)?)?;

    t.set("log", lua.create_function(|_, message: mlua::String| {
        log(lossy(&message));
        Ok(())
    })?)?;

    let images = ctx.images.clone();
    t.set("load_image", lua.create_function(move |_, path: mlua::String| {
        Ok(images.borrow_mut().load(&lossy(&path)).map(Surf))
    })?)?;

    let services = ctx.services.clone();
    t.set("service", lua.create_function(move |lua, name: mlua::String| match services.snapshot(&lossy(&name)) {
        Some(value) => json_to_lua(lua, &value),
        None => Ok(LuaValue::Nil),
    })?)?;
    let services = ctx.services.clone();
    t.set("media_control", lua.create_function(move |_, command: mlua::String| Ok(services.media_control(&lossy(&command))))?)?;

    let widget_dir = ctx.wtype.dir.clone();
    let dir = widget_dir.clone();
    t.set("run", lua.create_function(move |_, argv: Option<Table>| {
        let argv = strings(argv);
        if let Some((program, args)) = argv.split_first() {
            run_background(program, args, Some(&dir));
        }
        Ok(())
    })?)?;
    let dir = widget_dir.clone();
    t.set("python", lua.create_function(move |_, (script, args): (mlua::String, Option<Table>)| {
        let mut argv = vec![lossy(&script)];
        argv.extend(strings(args));
        let python = if cfg!(windows) { "python" } else { "python3" };
        run_background(python, &argv, Some(&dir));
        Ok(())
    })?)?;
    let dir = widget_dir;
    t.set("shell", lua.create_function(move |_, command: mlua::String| {
        let command = lossy(&command);
        if cfg!(windows) {
            run_background("cmd", &["/C".to_string(), command], Some(&dir));
        } else {
            run_background("sh", &["-c".to_string(), command], Some(&dir));
        }
        Ok(())
    })?)?;
    t.set("popen", lua.create_function(|_, command: mlua::String| Ok(shell_output(&lossy(&command))))?)?;
    t.set("quote", lua.create_function(|_, text: mlua::String| Ok(quote(&lossy(&text))))?)?;
    t.set("open_url", lua.create_function(|_, url: mlua::String| {
        open_url(&lossy(&url));
        Ok(())
    })?)?;
    let notify = ctx.notify.clone();
    t.set("notify", lua.create_function(move |_, (title, body): (mlua::String, Option<mlua::String>)| {
        notify(&lossy(&title), &body.map(|b| lossy(&b)).unwrap_or_default());
        Ok(())
    })?)?;
    t.set("tz_offset", lua.create_function(|_, zone: mlua::String| Ok(tz_offset(&lossy(&zone))))?)?;
    let info = ctx.sysinfo.clone();
    t.set("conky_parse", lua.create_function(move |_, text: mlua::String| Ok(compat::conky_parse(&lossy(&text), &info)))?)?;

    t.set("http", lua.create_function(|lua, (url, opts): (mlua::String, Option<Table>)| {
        let mut req = Request { url: lossy(&url), every: 900.0, ..Request::default() };
        if let Some(o) = opts {
            req.method = o.get::<Option<String>>("method")?.unwrap_or_default().to_uppercase();
            req.body = o.get::<Option<mlua::String>>("body")?.map(|b| lossy(&b));
            req.every = o.get::<Option<f64>>("every")?.unwrap_or(900.0);
            if let Some(headers) = o.get::<Option<Table>>("headers")? {
                for pair in headers.pairs::<mlua::String, mlua::String>() {
                    let (k, v) = pair?;
                    req.headers.push((lossy(&k), lossy(&v)));
                }
            }
            if req.method.is_empty() && req.body.is_some() {
                req.method = "POST".into();
            }
        }
        if req.method.is_empty() {
            req.method = "GET".into();
        }
        let reply = http::get(&req);
        let body = match &reply.body {
            Some(b) => LuaValue::String(lua.create_string(b.as_bytes())?),
            None => LuaValue::Nil,
        };
        Ok((body, reply.status, reply.age, reply.stamp))
    })?)?;
    t.set("download", lua.create_function(|_, url: mlua::String| {
        Ok(http::download(&lossy(&url)).map(|p| p.to_string_lossy().into_owned()))
    })?)?;
    t.set("json_decode", lua.create_function(|lua, text: mlua::String| {
        match serde_json::from_slice::<Value>(&text.as_bytes()) {
            Ok(value) => Ok((json_to_lua(lua, &value)?, LuaValue::Nil)),
            Err(e) => Ok((LuaValue::Nil, LuaValue::String(lua.create_string(e.to_string())?))),
        }
    })?)?;
    Ok(t)
}

fn sys_table(lua: &Lua, info: &SysInfo) -> LuaResult<Table> {
    let t = lua.create_table()?;
    macro_rules! part {
        ($name:literal, $method:ident) => {{
            let info = info.clone();
            t.set($name, lua.create_function(move |lua, _: mlua::MultiValue| json_to_lua(lua, &info.$method()))?)?;
        }};
    }
    part!("cpu", cpu);
    part!("memory", memory);
    part!("disks", disks);
    part!("net", net);
    part!("battery", battery);
    part!("temps", temps);
    let i = info.clone();
    t.set("disk", lua.create_function(move |lua, path: Option<mlua::String>| {
        json_to_lua(lua, &i.disk(path.map(|p| lossy(&p)).filter(|p| !p.is_empty()).as_deref()))
    })?)?;
    let i = info.clone();
    t.set("top", lua.create_function(move |lua, n: Option<f64>| {
        let n = n.filter(|n| *n >= 1.0).unwrap_or(5.0) as usize;
        json_to_lua(lua, &i.top(n))
    })?)?;
    let i = info.clone();
    t.set("uptime", lua.create_function(move |_, _: mlua::MultiValue| Ok(i.uptime()))?)?;
    Ok(t)
}

/// Starts a program and does not wait for it (no console window on Windows).
pub fn run_background(program: &str, args: &[String], cwd: Option<&std::path::Path>) {
    let mut cmd = util::command(program);
    cmd.args(args).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    if let Some(dir) = cwd {
        cmd.current_dir(dir);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        unsafe {
            cmd.pre_exec(|| {
                libc::setsid();
                Ok(())
            });
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000 | 0x0000_0008); // CREATE_NO_WINDOW | DETACHED_PROCESS
    }
    match cmd.spawn() {
        Ok(mut child) => {
            // reap it when it ends, so finished helpers do not stay around as zombies
            std::thread::spawn(move || {
                let _ = child.wait();
            });
        }
        Err(e) => log(format!("cannot start {program}: {e}")),
    }
}

/// Output of a shell command (blocks: widgets should use it rarely).
pub fn shell_output(command: &str) -> String {
    let (program, flag) = if cfg!(windows) { ("cmd", "/C") } else { ("sh", "-c") };
    util::run_command(program, &[flag, command], Duration::from_secs(10)).1
}

/// Quotes one argument for L.spawn / L.popen.
pub fn quote(text: &str) -> String {
    if cfg!(windows) {
        return quote_windows(text);
    }
    let safe = !text.is_empty() && text.chars().all(|c| c.is_ascii_alphanumeric() || "@%+=:,./-_".contains(c));
    if safe { text.to_string() } else { format!("'{}'", text.replace('\'', "'\"'\"'")) }
}

fn quote_windows(text: &str) -> String {
    if !text.is_empty() && !text.contains([' ', '\t', '"']) {
        return text.to_string();
    }
    let mut out = String::from("\"");
    let mut backslashes = 0;
    for c in text.chars() {
        match c {
            '\\' => backslashes += 1,
            '"' => {
                out.push_str(&"\\".repeat(backslashes * 2 + 1));
                out.push('"');
                backslashes = 0;
            }
            c => {
                out.push_str(&"\\".repeat(backslashes));
                out.push(c);
                backslashes = 0;
            }
        }
    }
    out.push_str(&"\\".repeat(backslashes * 2));
    out.push('"');
    out
}

/// A web address in the browser, or a file/folder in its default application.
pub fn open_url(target: &str) {
    if cfg!(windows) {
        run_background("rundll32", &["url.dll,FileProtocolHandler".to_string(), target.to_string()], None);
    } else if target.starts_with("http://") || target.starts_with("https://") || target.starts_with("mailto:") || std::path::Path::new(target).exists() {
        run_background("xdg-open", &[target.to_string()], None);
    } else {
        log(format!("cannot open {target}"));
    }
}

/// Seconds east of UTC that `zone` ("Europe/Kyiv") is at right now; 0 for an unknown zone.
pub fn tz_offset(zone: &str) -> i64 {
    use chrono::{Offset, Utc};
    let normalised = if zone == "Europe/Kiev" { "Europe/Kyiv" } else { zone };
    match normalised.parse::<chrono_tz::Tz>() {
        Ok(tz) => Utc::now().with_timezone(&tz).offset().fix().local_minus_utc() as i64,
        Err(_) => 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quoting() {
        if !cfg!(windows) {
            assert_eq!(quote("plain-1/x.txt"), "plain-1/x.txt");
            assert_eq!(quote("two words"), "'two words'");
            assert_eq!(quote("it's"), "'it'\"'\"'s'");
            assert_eq!(quote(""), "''");
        }
        assert_eq!(quote_windows("a b"), "\"a b\"");
        assert_eq!(quote_windows("a\"b"), "\"a\\\"b\"");
        assert_eq!(quote_windows("c:\\dir with space\\"), "\"c:\\dir with space\\\\\"");
    }

    #[test]
    fn time_zones() {
        assert_eq!(tz_offset("UTC"), 0);
        assert_eq!(tz_offset("Nowhere/Land"), 0);
        let kyiv = tz_offset("Europe/Kyiv");
        assert!(kyiv == 7200 || kyiv == 10800);
        assert_eq!(tz_offset("Europe/Kiev"), kyiv);
    }

    #[test]
    fn json_arrays_start_at_one() {
        let lua = Lua::new();
        let v = json_to_lua(&lua, &serde_json::json!({"a": [10, 20], "n": null, "f": 1.5, "i": 3})).unwrap();
        lua.globals().set("v", v).unwrap();
        let (a1, a2, n, f, i): (i64, i64, bool, f64, i64) =
            lua.load("return v.a[1], v.a[2], v.n == nil, v.f, v.i").eval().unwrap();
        assert_eq!((a1, a2, n, f, i), (10, 20, true, 1.5, 3));
        assert_eq!(lua.load("return math.type(v.i)").eval::<String>().unwrap(), "integer");
    }
}
