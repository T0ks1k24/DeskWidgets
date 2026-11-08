//! Where everything lives, on Linux and on Windows.
//!
//! Read-only data (widgets, the Lua library, fonts, icons) is embedded in the executable and unpacked
//! once into the cache folder (`core::assets`); `DESKWIDGETS_DATA` points at a source checkout instead.
//! `DESKWIDGETS_HOME` moves config, cache and runtime folders under one root (tests, experiments).
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

pub const APP_ID: &str = "io.github.toksik.DeskWidgets";
pub const GALLERY_ID: &str = "io.github.toksik.DeskWidgets.Gallery";
pub const WINDOWS: bool = cfg!(windows);

#[derive(Debug, Clone)]
pub struct Paths {
    pub home: PathBuf,
    pub config: PathBuf,
    pub cache: PathBuf,
    pub run: PathBuf,
}

static PATHS: OnceLock<Paths> = OnceLock::new();

thread_local! {
    static LOCAL: std::cell::Cell<Option<&'static Paths>> = const { std::cell::Cell::new(None) };
}

/// Makes this thread (and only this thread) use `root` for config, cache and runtime files. For tests that
/// need an isolated desktop of their own while others run in parallel.
pub fn use_root_for_this_thread(root: &Path) {
    let paths: &'static Paths = Box::leak(Box::new(Paths {
        home: root.join("home"),
        config: root.join("config"),
        cache: root.join("cache"),
        run: root.join("run"),
    }));
    LOCAL.with(|l| l.set(Some(paths)));
}

fn home_dir() -> PathBuf {
    let var = if cfg!(windows) { "USERPROFILE" } else { "HOME" };
    std::env::var_os(var).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."))
}

fn xdg(variable: &str, fallback: &str, home: &Path) -> PathBuf {
    match std::env::var_os(variable) {
        Some(v) if !v.is_empty() => PathBuf::from(v),
        _ => home.join(fallback),
    }
}

/// True when everything lives under a throw-away root (tests, experiments): then nothing may touch the real
/// desktop session either (autostart entries, GNOME key bindings, the user's own config folders).
pub fn isolated() -> bool {
    LOCAL.with(|l| l.get()).is_some() || std::env::var_os("DESKWIDGETS_HOME").is_some()
}

fn compute() -> Paths {
    let home = home_dir();
    if let Some(root) = std::env::var_os("DESKWIDGETS_HOME") {
        let root = PathBuf::from(root);
        return Paths { home: root.join("home"), config: root.join("config"), cache: root.join("cache"), run: root.join("run") };
    }
    if cfg!(windows) {
        let roaming = std::env::var_os("APPDATA").map(PathBuf::from).unwrap_or_else(|| home.join("AppData").join("Roaming"));
        let local = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(|| home.join("AppData").join("Local"));
        return Paths {
            config: roaming.join("DeskWidgets"),
            cache: local.join("DeskWidgets").join("cache"),
            run: local.join("DeskWidgets").join("run"),
            home,
        };
    }
    let runtime = match std::env::var_os("XDG_RUNTIME_DIR") {
        Some(v) if !v.is_empty() => PathBuf::from(v),
        _ => std::env::temp_dir().join(format!("deskwidgets-{}", unsafe_uid())),
    };
    Paths {
        config: xdg("XDG_CONFIG_HOME", ".config", &home).join("deskwidgets"),
        cache: xdg("XDG_CACHE_HOME", ".cache", &home).join("deskwidgets"),
        run: runtime.join("deskwidgets"),
        home,
    }
}

#[cfg(unix)]
pub fn unsafe_uid() -> u32 {
    unsafe { libc::getuid() }
}

#[cfg(not(unix))]
pub fn unsafe_uid() -> u32 {
    0
}

pub fn get() -> &'static Paths {
    LOCAL.with(|l| l.get()).unwrap_or_else(|| PATHS.get_or_init(compute))
}

pub fn home() -> &'static Path {
    &get().home
}
pub fn config() -> &'static Path {
    &get().config
}
pub fn cache() -> &'static Path {
    &get().cache
}
pub fn run() -> &'static Path {
    &get().run
}

pub fn user_widgets() -> PathBuf {
    config().join("widgets")
}
pub fn layout() -> PathBuf {
    config().join("layout.json")
}
pub fn settings() -> PathBuf {
    config().join("settings.json")
}
pub fn previews() -> PathBuf {
    cache().join("previews")
}
/// Decoded copies of pictures and downloaded files (cover art, widget downloads).
pub fn images() -> PathBuf {
    cache().join("images")
}
/// The service listens here (a Unix socket on Linux, a port file on Windows).
pub fn socket() -> PathBuf {
    run().join(if cfg!(windows) { "control.port" } else { "control" })
}
pub fn auth_key() -> PathBuf {
    run().join("control.key")
}
pub fn service_log() -> PathBuf {
    cache().join("service.log")
}

// data shipped inside the executable
pub fn data() -> &'static Path {
    super::assets::data_dir()
}
pub fn builtin_widgets() -> PathBuf {
    data().join("widgets")
}
pub fn lua() -> PathBuf {
    data().join("lua")
}
pub fn fonts() -> PathBuf {
    data().join("assets").join("fonts")
}
pub fn icon() -> PathBuf {
    data().join("assets").join("icons").join(format!("{APP_ID}.svg"))
}
pub fn icon_png() -> PathBuf {
    data().join("assets").join("icons").join(format!("{APP_ID}.png"))
}
pub fn udev_rules() -> PathBuf {
    data().join("assets").join("udev").join("70-deskwidgets-hid.rules")
}

// DeskWidgets 1.x kept its files here
pub fn old_layout() -> PathBuf {
    home().join(".config").join("conky").join("mac").join("layout.json")
}
pub fn old_known_devices() -> PathBuf {
    home().join(".config").join("conky").join("network_known.txt")
}

pub fn ensure_dirs() {
    for dir in [config().to_path_buf(), user_widgets(), cache().to_path_buf(), previews(), images(), run().to_path_buf()] {
        let _ = std::fs::create_dir_all(dir);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(run(), std::fs::Permissions::from_mode(0o700));
    }
}

/// The path of this executable, for autostart entries and for starting the service.
pub fn exe() -> PathBuf {
    std::env::current_exe().unwrap_or_else(|_| PathBuf::from("deskwidgets"))
}
