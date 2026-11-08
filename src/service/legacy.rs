//! DeskWidgets 1.x and 2.x drew every widget with its own `conky` process, and 3.0 was a Python service.
//! When this version starts on a machine that still has those running, stop them so the widgets do not appear twice.
#[cfg(target_os = "linux")]
pub fn stop_old_versions() {
    if std::env::var_os("DESKWIDGETS_KEEP_OLD").is_some() {
        return; // for testing
    }
    let me = std::process::id();
    let uid = unsafe { libc::getuid() };
    let Ok(entries) = std::fs::read_dir("/proc") else { return };
    for entry in entries.filter_map(|e| e.ok()) {
        let Some(pid) = entry.file_name().to_string_lossy().parse::<u32>().ok() else { continue };
        if pid == me {
            continue;
        }
        let owned = std::fs::metadata(entry.path()).map(|m| std::os::unix::fs::MetadataExt::uid(&m) == uid).unwrap_or(false);
        if !owned {
            continue;
        }
        let Ok(raw) = std::fs::read(entry.path().join("cmdline")) else { continue };
        let argv: Vec<String> = raw.split(|b| *b == 0).map(|a| String::from_utf8_lossy(a).into_owned()).collect();
        if is_old_widget(&argv) {
            unsafe { libc::kill(pid as i32, libc::SIGTERM) };
        }
    }
}

#[cfg(not(target_os = "linux"))]
pub fn stop_old_versions() {}

/// A conky process of a 1.x/2.x widget, or the Python service of 2.x/3.0 (`python3 .../bin/deskwidgets run`).
pub fn is_old_widget(argv: &[String]) -> bool {
    let Some(first) = argv.first() else { return false };
    let program = first.rsplit('/').next().unwrap_or("");
    if program == "conky" {
        return argv.iter().any(|a| a.ends_with("/widget.conf") || a.ends_with("mac/widgets.conf"));
    }
    if !program.starts_with("python") {
        return false;
    }
    let script = argv.get(1).map(String::as_str).unwrap_or("");
    let runs = argv.get(2).map(String::as_str) == Some("run");
    (script.ends_with("/bin/deskwidgets") && runs) || (script == "-m" && argv.get(2).map(String::as_str) == Some("deskwidgets") && argv.get(3).map(String::as_str) == Some("run"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(args: &[&str]) -> Vec<String> {
        args.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn recognises_old_services_only() {
        assert!(is_old_widget(&v(&["conky", "-c", "/home/u/.config/conky/mac/widgets.conf"])));
        assert!(is_old_widget(&v(&["/usr/bin/python3", "/usr/bin/deskwidgets", "run"])));
        assert!(is_old_widget(&v(&["python3", "-m", "deskwidgets", "run"])));
        assert!(!is_old_widget(&v(&["/usr/bin/deskwidgets", "run"])), "this version is not an old one");
        assert!(!is_old_widget(&v(&["conky", "-c", "/etc/other.conf"])));
        assert!(!is_old_widget(&v(&["python3", "script.py"])));
        assert!(!is_old_widget(&[]));
    }
}
