//! Command line: `deskwidgets [command]` (no command = run the service).
use std::path::{Path, PathBuf};
use std::time::Duration;

use clap::{Arg, ArgAction, ArgMatches, Command};
use serde_json::{json, Value};

use crate::core::catalog::{self, SIZE_KEYS};
use crate::core::i18n::{set_lang, tr, tr_owned};
use crate::core::layout::read_instances;
use crate::core::{doctor, paths, scaffold, settings};
use crate::engine::fonts::FontMap;
use crate::engine::render::{self, Options};
use crate::platform::integration;
use crate::service::ipc;
use crate::VERSION;

/// Sends a command to the service (starting it if needed). Returns the answer, or None after printing why not.
pub fn remote(command: &str, args: Value) -> Option<Value> {
    if !ipc::ensure_running(Duration::from_secs(8)) {
        eprintln!(
            "{}{})",
            tr("Сервіс DeskWidgets не запустився (журнал: ", "The DeskWidgets service did not start (log: "),
            paths::service_log().display()
        );
        return None;
    }
    match ipc::request(command, args, ipc::TIMEOUT) {
        Ok(v) => Some(v),
        Err(e) => {
            eprintln!("deskwidgets: {e}");
            None
        }
    }
}

/// Exit code for commands that only need to be delivered.
fn remote_status(command: &str, args: Value) -> i32 {
    if remote(command, args).is_some() { 0 } else { 1 }
}

fn cmd_run() -> i32 {
    crate::service::daemon::run()
}

fn cmd_gallery(configure: Option<&str>, page: Option<&str>) -> i32 {
    crate::gallery::run(configure, page)
}

fn cmd_status() -> i32 {
    if !ipc::is_running() {
        println!("{}", tr("Сервіс не запущено", "The service is not running"));
        return 1;
    }
    match ipc::request("status", json!({}), ipc::TIMEOUT) {
        Ok(v) => {
            println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
            0
        }
        Err(e) => {
            eprintln!("deskwidgets: {e}");
            1
        }
    }
}

fn cmd_list() -> i32 {
    for i in read_instances() {
        println!("{:18} {:15} {:7} cell=({},{}) {}/{}", i.id, i.kind, i.size, i.col, i.row, i.style, i.accent);
    }
    0
}

fn cmd_types() -> i32 {
    let types = catalog::discover();
    let mut list: Vec<_> = types.values().collect();
    list.sort_by(|a, b| (&a.category, &a.id).cmp(&(&b.category, &b.id)));
    for t in list {
        let where_ = if t.user { tr("мій", "mine") } else { tr("вбудований", "built-in") };
        let note = if t.supported() { String::new() } else { format!("  [{}]", tr("не в цій системі", "not on this system")) };
        println!("{:16} {:28} {:22} {} · {}{}", t.id, t.title(), t.sizes.join(","), catalog::category_name(&t.category), where_, note);
    }
    0
}

fn cmd_new(id: &str, name: Option<&str>) -> i32 {
    match scaffold::create(id, name) {
        Ok(folder) => {
            let leaf = folder.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            println!("{}", folder.display());
            println!("{}", tr_owned(format!("Відредагуйте widget.lua, потім: deskwidgets reload && deskwidgets add {leaf}"), format!("Edit widget.lua, then: deskwidgets reload && deskwidgets add {leaf}")));
            0
        }
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
            eprintln!("{}{e}", tr("Вже існує: ", "Already exists: "));
            1
        }
        Err(e) => {
            eprintln!("deskwidgets: {e}");
            1
        }
    }
}

fn cmd_preview(m: &ArgMatches) -> i32 {
    let types = catalog::discover();
    let type_id = m.get_one::<String>("type").unwrap();
    let Some(t) = types.get(type_id) else {
        eprintln!("{}{type_id}", tr("Немає такого віджета: ", "No such widget: "));
        return 1;
    };
    let s = settings::load();
    let mut options = serde_json::Map::new();
    for item in m.get_many::<String>("option").into_iter().flatten() {
        let (key, text) = item.split_once('=').unwrap_or((item.as_str(), ""));
        let Some(option) = t.option(key) else {
            eprintln!("{}{key}", tr("У віджета немає параметра ", "The widget has no option "));
            return 1;
        };
        let raw = if option.kind == "bool" { Value::Bool(text == "true") } else { Value::String(text.to_string()) };
        match option.coerce(&raw) {
            Ok(v) => {
                options.insert(key.to_string(), v);
            }
            Err(e) => {
                eprintln!("{e}");
                return 1;
            }
        }
    }
    let size = m.get_one::<String>("size").cloned();
    let opts = Options {
        size: size.clone(),
        style: m.get_one::<String>("style").cloned(),
        accent: m.get_one::<String>("accent").cloned(),
        lang: m.get_one::<String>("lang").cloned().unwrap_or_else(|| settings::language(&s)),
        scale: m.get_one::<f64>("scale").copied().unwrap_or(1.0),
        options,
        demo: !m.get_flag("live"),
        ..Options::default()
    };
    let out: PathBuf = match m.get_one::<String>("output") {
        Some(o) => Path::new(o).to_path_buf(),
        None => PathBuf::from(format!("{}-{}.png", t.id, size.as_deref().unwrap_or(t.size()))),
    };
    let out = std::path::absolute(&out).unwrap_or(out);
    let fonts = std::sync::Arc::new(FontMap::new(&s.font));
    let ok = render::render_png(t, &out, &opts, &s, fonts);
    println!("{}", if ok { out.display().to_string() } else { tr("Не вдалося відрендерити", "Rendering failed").to_string() });
    if ok { 0 } else { 1 }
}

/// Gallery thumbnails, one PNG per widget.
fn cmd_previews(m: &ArgMatches) -> i32 {
    let s = settings::load();
    let lang = m.get_one::<String>("lang").cloned().unwrap_or_else(|| settings::language(&s));
    let mut types = catalog::discover();
    if let Some(only) = m.get_one::<String>("only") {
        let wanted: Vec<&str> = only.split(',').collect();
        let filtered = types.iter().filter(|(k, _)| wanted.contains(&k.as_str())).map(|(k, v)| (k.clone(), v.clone())).collect();
        types = std::sync::Arc::new(filtered);
    }
    let fonts = std::sync::Arc::new(FontMap::new(&s.font));
    let dir = paths::previews().join(VERSION).join(&lang);
    if render::render_all(&dir, &lang, &types, &s, fonts) { 0 } else { 1 }
}

fn cmd_setup(no_autostart: bool, no_hotkey: bool) -> i32 {
    let done = integration::setup(!no_autostart, !no_hotkey);
    let mut s = settings::load();
    s.autostart = !no_autostart;
    s.hotkey = !no_hotkey;
    s.extra.insert("_setup_done".into(), Value::Bool(true));
    let _ = settings::save(&s);
    for (k, v) in done {
        println!("{k}: {}", if v { "ok" } else { "-" });
    }
    0
}

/// The command that installs the udev rule for the wireless mice.
pub fn udev_command() -> String {
    format!(
        "sudo install -m 644 {} /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw",
        paths::udev_rules().display()
    )
}

fn cmd_mice() -> i32 {
    let code = crate::providers::hid_mice::main();
    if crate::providers::hid_mice::find().iter().any(|d| !d.access) {
        println!("{}", tr("Щоб читати заряд миші без root, один раз виконайте:", "To read the mouse battery without root, run once:"));
        println!("  {}", udev_command());
    }
    code
}

fn cmd_netwatch(m: &ArgMatches) -> i32 {
    let get = |k: &str| m.get_one::<String>(k).cloned().unwrap_or_default();
    crate::netwatch::run(Path::new(&get("cache")), Path::new(&get("known")), Path::new(&get("seen")), Path::new(&get("log")), &get("lang"))
}

fn simple(name: &'static str, help: &'static str) -> Command {
    Command::new(name).about(help)
}

pub fn build() -> Command {
    let about = tr("Віджети робочого столу в стилі macOS для Linux і Windows.", "macOS-style desktop widgets for Linux and Windows.");
    let id_arg = |help: &'static str| Arg::new("id").required(true).help(help);
    Command::new("deskwidgets")
        .about(about)
        .version(VERSION)
        .subcommand(simple("run", tr("запустити сервіс (за замовчуванням)", "run the service (default)")))
        .subcommand(simple("gallery", tr("відкрити галерею", "open the gallery")).arg(Arg::new("configure").long("configure").value_name("ID").help("settings of one widget"))
            .arg(Arg::new("page").long("page").value_parser(["mine", "add", "prefs"]).hide(true)))
        .subcommand(simple("configure", tr("налаштування віджета", "settings of a widget")).arg(id_arg("widget id")))
        .subcommand(simple("add", tr("додати віджет на стіл", "add a widget to the desktop")).arg(Arg::new("type").required(true)))
        .subcommand(simple("remove", tr("прибрати віджет", "remove a widget")).arg(id_arg("widget id")))
        .subcommand(simple("arrange", tr("розставити по сітці", "pack widgets onto the grid")))
        .subcommand(simple("reload", tr("перечитати віджети й перезапустити", "re-read widget folders and restart")))
        .subcommand(simple("quit", tr("зупинити сервіс", "stop the service")))
        .subcommand(simple("status", tr("стан сервісу", "state of the service")))
        .subcommand(simple("list", tr("віджети на столі", "widgets on the desktop")))
        .subcommand(simple("types", tr("усі доступні віджети", "all available widgets")))
        .subcommand(simple("new", tr("створити свій віджет", "create your own widget")).arg(Arg::new("id").required(true)).arg(Arg::new("name").long("name")))
        .subcommand(
            simple("preview", tr("відрендерити віджет у PNG", "render a widget to PNG"))
                .arg(Arg::new("type").required(true))
                .arg(Arg::new("size").long("size").value_parser(SIZE_KEYS))
                .arg(Arg::new("style").long("style"))
                .arg(Arg::new("accent").long("accent"))
                .arg(Arg::new("lang").long("lang").value_parser(["uk", "en"]))
                .arg(Arg::new("scale").long("scale").value_parser(clap::value_parser!(f64)))
                .arg(Arg::new("live").long("live").action(ArgAction::SetTrue).help(tr("справжні дані замість прикладу", "real data instead of sample data")))
                .arg(Arg::new("option").long("option").action(ArgAction::Append).value_name("KEY=VALUE").help(tr("значення параметра віджета (можна кілька разів)", "a widget option value (repeatable)")))
                .arg(Arg::new("output").short('o').long("output")),
        )
        .subcommand(
            simple("previews", tr("оновити мініатюри галереї", "refresh gallery thumbnails"))
                .arg(Arg::new("lang").long("lang").value_parser(["uk", "en"]))
                .arg(Arg::new("only").long("only").help(tr("лише ці віджети (через кому)", "only these widgets (comma separated)"))),
        )
        .subcommand(
            simple("setup", tr("автозапуск, гаряча клавіша, пункт меню", "autostart, hotkey, menu entry"))
                .arg(Arg::new("no-autostart").long("no-autostart").action(ArgAction::SetTrue))
                .arg(Arg::new("no-hotkey").long("no-hotkey").action(ArgAction::SetTrue)),
        )
        .subcommand(simple("notify", tr("показати сповіщення", "show a notification")).arg(Arg::new("title").required(true)).arg(Arg::new("body").default_value("")))
        .subcommand(simple("doctor", tr("перевірити залежності", "check dependencies")))
        .subcommand(simple("mice", tr("заряд бездротових ігрових мишей", "battery of wireless gaming mice")))
        .subcommand(
            Command::new("netwatch")
                .hide(true)
                .arg(Arg::new("cache").required(true))
                .arg(Arg::new("known").required(true))
                .arg(Arg::new("seen").required(true))
                .arg(Arg::new("log").required(true))
                .arg(Arg::new("lang").default_value("uk")),
        )
}

pub fn main_with(args: Vec<String>) -> i32 {
    set_lang(&settings::language(&settings::load()));
    let matches = match build().try_get_matches_from(args) {
        Ok(m) => m,
        Err(e) => {
            let code = e.exit_code();
            let _ = e.print();
            return code;
        }
    };
    let arg = |m: &ArgMatches, k: &str| m.get_one::<String>(k).cloned().unwrap_or_default();
    match matches.subcommand() {
        None | Some(("run", _)) => cmd_run(),
        Some(("gallery", m)) => cmd_gallery(m.get_one::<String>("configure").map(String::as_str), m.get_one::<String>("page").map(String::as_str)),
        Some(("configure", m)) => cmd_gallery(Some(&arg(m, "id")), None),
        Some(("add", m)) => remote_status("add", json!({"type": arg(m, "type")})),
        Some(("remove", m)) => remote_status("remove", json!({"id": arg(m, "id")})),
        Some(("quit", _)) => {
            if !ipc::is_running() {
                return 0;
            }
            remote_status("quit", json!({}))
        }
        Some((name @ ("arrange" | "reload"), _)) => remote_status(name, json!({})),
        Some(("status", _)) => cmd_status(),
        Some(("list", _)) => cmd_list(),
        Some(("types", _)) => cmd_types(),
        Some(("new", m)) => cmd_new(&arg(m, "id"), m.get_one::<String>("name").map(String::as_str)),
        Some(("preview", m)) => cmd_preview(m),
        Some(("previews", m)) => cmd_previews(m),
        Some(("setup", m)) => cmd_setup(m.get_flag("no-autostart"), m.get_flag("no-hotkey")),
        Some(("notify", m)) => remote_status("notify", json!({"title": arg(m, "title"), "body": arg(m, "body")})),
        Some(("doctor", _)) => doctor::run_checks(),
        Some(("mice", _)) => cmd_mice(),
        Some(("netwatch", m)) => cmd_netwatch(m),
        Some(_) => 2,
    }
}
