//! Fitting into the desktop: autostart, the app-menu entry, the gallery hotkey, notifications.
//!
//! Linux: XDG autostart and menu entries, a GNOME custom keybinding (other desktops: bind
//! `deskwidgets gallery` to a key yourself). Windows: the Run registry key and a Start-menu shortcut;
//! the Windows hotkey and tray icon live in the service (`platform/win32.rs`).
//! Every step is optional and safe to repeat.
use std::collections::BTreeMap;
use std::path::PathBuf;

use crate::core::i18n::tr;
use crate::core::util::{self, log, write_atomic};
use crate::core::paths;

const HOTKEY: &str = "<Super><Alt>w";
const KEYBINDING_PATH: &str = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/deskwidgets/";
const MEDIA_KEYS: &str = "org.gnome.settings-daemon.plugins.media-keys";

fn config_home() -> PathBuf {
    match std::env::var_os("XDG_CONFIG_HOME") {
        Some(v) if !v.is_empty() && !paths::isolated() => PathBuf::from(v),
        _ => paths::home().join(".config"),
    }
}

fn data_home() -> PathBuf {
    match std::env::var_os("XDG_DATA_HOME") {
        Some(v) if !v.is_empty() && !paths::isolated() => PathBuf::from(v),
        _ => paths::home().join(".local").join("share"),
    }
}

fn autostart_file() -> PathBuf {
    config_home().join("autostart").join(format!("{}.desktop", paths::APP_ID))
}

/// argv that starts `deskwidgets <args>`, wherever it is installed.
pub fn command(args: &[&str]) -> Vec<String> {
    let mut argv = vec![paths::exe().to_string_lossy().into_owned()];
    argv.extend(args.iter().map(|a| a.to_string()));
    argv
}

/// An Exec= line (desktop-entry quoting rules).
fn desktop_exec(args: &[&str]) -> String {
    fn quote(text: &str) -> String {
        let escaped = text.replace('\\', "\\\\").replace('"', "\\\"").replace('`', "\\`").replace('$', "\\$").replace('%', "%%");
        if text.is_empty() || text.chars().any(|c| " \t'\"<>~|&;*?#()".contains(c)) {
            format!("\"{escaped}\"")
        } else {
            escaped
        }
    }
    command(args).iter().map(|a| quote(a)).collect::<Vec<_>>().join(" ")
}

// autostart
pub fn set_autostart(enabled: bool) -> bool {
    if cfg!(windows) && !paths::isolated() {
        return windows_impl::set_run_key(enabled);
    }
    if enabled {
        let text = format!(
            "[Desktop Entry]\nType=Application\nName=DeskWidgets\nComment={}\nExec={}\nIcon=preferences-desktop-theme\nTerminal=false\nNoDisplay=true\nX-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=5\n",
            tr("Віджети робочого столу", "Desktop widgets"),
            desktop_exec(&["run"])
        );
        write_atomic(&autostart_file(), &text).is_ok()
    } else {
        let _ = std::fs::remove_file(autostart_file());
        true
    }
}

pub fn autostart_enabled() -> bool {
    if cfg!(windows) {
        return windows_impl::run_key_present();
    }
    autostart_file().exists()
}

/// 1.x started from ~/.config/autostart/conky.desktop (runs start.sh). Disable it, keep a copy.
pub fn retire_v1() -> bool {
    let old = paths::home().join(".config").join("autostart").join("conky.desktop");
    let Ok(text) = std::fs::read_to_string(&old) else { return false };
    if !text.contains("conky/mac/start.sh") {
        return false;
    }
    let renamed = old.with_file_name("conky.desktop.disabled-by-deskwidgets");
    if std::fs::rename(&old, renamed).is_ok() {
        log("old autostart (conky.desktop) disabled");
        return true;
    }
    false
}

/// The menu entry for the gallery (packages ship their own on Linux).
pub fn install_menu_entry() -> bool {
    if cfg!(windows) {
        return windows_impl::install_start_menu();
    }
    if std::path::Path::new(&format!("/usr/share/applications/{}.desktop", paths::GALLERY_ID)).exists() {
        return true;
    }
    let icon = paths::icon();
    let icon = if icon.exists() { icon.to_string_lossy().into_owned() } else { "preferences-desktop-theme".into() };
    let text = format!(
        "[Desktop Entry]\nType=Application\nName={}\nComment={}\nExec={}\nIcon={}\nCategories=Utility;GTK;\nKeywords=widgets;clock;weather;anime;desktop;віджети;\nStartupNotify=true\n",
        tr("Віджети", "Widgets"),
        tr("Додати, налаштувати й розставити віджети робочого столу", "Add, configure and arrange desktop widgets"),
        desktop_exec(&["gallery"]),
        icon
    );
    write_atomic(&data_home().join("applications").join(format!("{}.desktop", paths::GALLERY_ID)), &text).is_ok()
}

// GNOME: Super+Alt+W opens the gallery
fn gsettings(args: &[&str]) -> Option<String> {
    let (code, out) = util::run_command("gsettings", args, std::time::Duration::from_secs(10));
    (code == Some(0)).then(|| out.trim().to_string())
}

pub fn is_gnome() -> bool {
    !paths::isolated() && std::env::var("XDG_CURRENT_DESKTOP").map(|v| v.to_uppercase().contains("GNOME")).unwrap_or(false) && util::which("gsettings").is_some()
}

/// The paths in a gsettings string array like `['/a/', '/b/']` (or `@as []`).
fn parse_path_list(raw: &str) -> Option<Vec<String>> {
    let raw = raw.trim().trim_start_matches("@as").trim();
    let inner = raw.strip_prefix('[')?.strip_suffix(']')?;
    Some(inner.split(',').map(|s| s.trim().trim_matches('\'').trim_matches('"').to_string()).filter(|s| !s.is_empty()).collect())
}

fn format_path_list(list: &[String]) -> String {
    format!("[{}]", list.iter().map(|p| format!("'{p}'")).collect::<Vec<_>>().join(", "))
}

pub fn set_hotkey(enabled: bool) -> bool {
    if !is_gnome() || paths::isolated() {
        return false;
    }
    let Some(raw) = gsettings(&["get", MEDIA_KEYS, "custom-keybindings"]) else { return false };
    let Some(mut current) = parse_path_list(&raw) else { return false };
    let schema = format!("{MEDIA_KEYS}.custom-keybinding:{KEYBINDING_PATH}");
    let known = current.iter().any(|p| p == KEYBINDING_PATH);
    if enabled {
        let command = command(&["gallery"]).iter().map(|a| shell_quote(a)).collect::<Vec<_>>().join(" ");
        for (key, value) in [("name", tr("Віджети робочого столу", "Desktop widgets").to_string()), ("command", command), ("binding", HOTKEY.to_string())] {
            gsettings(&["set", &schema, key, &value]);
        }
        if !known {
            current.push(KEYBINDING_PATH.to_string());
            gsettings(&["set", MEDIA_KEYS, "custom-keybindings", &format_path_list(&current)]);
        }
    } else if known {
        current.retain(|p| p != KEYBINDING_PATH);
        gsettings(&["set", MEDIA_KEYS, "custom-keybindings", &format_path_list(&current)]);
    }
    true
}

fn shell_quote(text: &str) -> String {
    if !text.is_empty() && text.chars().all(|c| c.is_ascii_alphanumeric() || "@%+=:,./-_".contains(c)) {
        text.to_string()
    } else {
        format!("'{}'", text.replace('\'', "'\"'\"'"))
    }
}

/// First-run setup. Returns what was done.
pub fn setup(autostart: bool, hotkey: bool) -> BTreeMap<&'static str, bool> {
    retire_v1();
    let mut done = BTreeMap::new();
    done.insert("autostart", set_autostart(autostart));
    done.insert("menu", install_menu_entry());
    if !cfg!(windows) {
        done.insert("hotkey", set_hotkey(hotkey));
    }
    done
}

/// Notifications from processes that have no tray icon.
pub fn notify(title: &str, body: &str) {
    if cfg!(windows) {
        log(format!("{title}: {body}")); // the service shows these through its tray icon (`deskwidgets notify`)
    } else if util::which("notify-send").is_some() {
        util::run_command("notify-send", &["-u", "critical", "-i", "network-wireless", title, body], std::time::Duration::from_secs(10));
    }
}

#[cfg(windows)]
mod windows_impl {
    use super::*;
    use winreg::enums::{HKEY_CURRENT_USER, KEY_SET_VALUE, KEY_READ};
    use winreg::RegKey;

    const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";

    fn exe_command() -> String {
        format!("\"{}\" run", paths::exe().display())
    }

    pub fn run_key_present() -> bool {
        RegKey::predef(HKEY_CURRENT_USER)
            .open_subkey_with_flags(RUN_KEY, KEY_READ)
            .and_then(|key| key.get_value::<String, _>("DeskWidgets"))
            .is_ok()
    }

    pub fn set_run_key(enabled: bool) -> bool {
        let Ok(key) = RegKey::predef(HKEY_CURRENT_USER).open_subkey_with_flags(RUN_KEY, KEY_SET_VALUE) else { return false };
        if enabled {
            key.set_value("DeskWidgets", &exe_command()).is_ok()
        } else {
            match key.delete_value("DeskWidgets") {
                Ok(()) => true,
                Err(e) => e.kind() == std::io::ErrorKind::NotFound,
            }
        }
    }

    /// DeskWidgets.ico next to the settings (Windows shortcuts need .ico), made from the PNG.
    fn windows_icon() -> String {
        let ico = paths::config().join("DeskWidgets.ico");
        let made = image::open(paths::icon_png()).and_then(|img| img.resize(256, 256, image::imageops::FilterType::Lanczos3).save_with_format(&ico, image::ImageFormat::Ico));
        if made.is_ok() { ico.to_string_lossy().into_owned() } else { String::new() }
    }

    /// A Start-menu shortcut to the gallery, made with the Windows scripting host (no extra packages).
    pub fn install_start_menu() -> bool {
        let programs = PathBuf::from(std::env::var_os("APPDATA").unwrap_or_default()).join("Microsoft").join("Windows").join("Start Menu").join("Programs");
        if !programs.is_dir() {
            return false;
        }
        let icon = windows_icon();
        let script = "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($args[0]);$s.TargetPath=$args[1];$s.Arguments=$args[2];$s.IconLocation=$args[3];$s.Save()";
        let shortcut = programs.join("DeskWidgets.lnk").to_string_lossy().into_owned();
        let exe = paths::exe().to_string_lossy().into_owned();
        let (code, _) = util::run_command("powershell", &["-NoProfile", "-Command", script, &shortcut, &exe, "gallery", &icon], std::time::Duration::from_secs(30));
        code == Some(0)
    }
}

#[cfg(not(windows))]
mod windows_impl {
    pub fn run_key_present() -> bool {
        false
    }
    pub fn set_run_key(_enabled: bool) -> bool {
        false
    }
    pub fn install_start_menu() -> bool {
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gsettings_lists() {
        assert_eq!(parse_path_list("@as []"), Some(vec![]));
        assert_eq!(parse_path_list("['/a/', '/b/']"), Some(vec!["/a/".to_string(), "/b/".to_string()]));
        assert_eq!(format_path_list(&["/a/".to_string(), "/b/".to_string()]), "['/a/', '/b/']");
        assert_eq!(parse_path_list("nonsense"), None);
    }

    #[test]
    fn desktop_exec_quotes_what_needs_it() {
        let line = desktop_exec(&["run"]);
        assert!(line.ends_with(" run"));
        assert_eq!(shell_quote("a b"), "'a b'");
    }
}
