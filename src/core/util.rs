//! Small helpers shared by every part of DeskWidgets.
use std::fs;
use std::io::Write;
use std::path::Path;
use std::time::Duration;

thread_local! {
    static CAPTURE: std::cell::RefCell<Option<Vec<String>>> = const { std::cell::RefCell::new(None) };
}

pub fn log(message: impl AsRef<str>) {
    let captured = CAPTURE.with(|c| match c.borrow_mut().as_mut() {
        Some(list) => {
            list.push(message.as_ref().to_string());
            true
        }
        None => false,
    });
    if !captured {
        eprintln!("deskwidgets: {}", message.as_ref());
    }
}

/// Runs `f` and returns what it logged on this thread instead of printing it (tests).
pub fn capture_log<R>(f: impl FnOnce() -> R) -> (R, Vec<String>) {
    CAPTURE.with(|c| *c.borrow_mut() = Some(Vec::new()));
    let result = f();
    let lines = CAPTURE.with(|c| c.borrow_mut().take()).unwrap_or_default();
    (result, lines)
}

/// Writes `text` so that readers see the old or the new file, never half of one.
pub fn write_atomic(path: &Path, text: &str) -> std::io::Result<()> {
    write_atomic_bytes(path, text.as_bytes())
}

pub fn write_atomic_bytes(path: &Path, data: &[u8]) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    // unique per call: several threads (and processes) may write the same file at once
    static COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let unique = COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let tmp = path.with_file_name(format!(".{}-{}-{}.tmp", name, std::process::id(), unique));
    let result = (|| {
        let mut file = fs::File::create(&tmp)?;
        file.write_all(data)?;
        file.sync_all().ok();
        drop(file);
        replace(&tmp, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result
}

/// `fs::rename` that survives Windows' short sharing violations while a reader has the file open.
pub fn replace(src: &Path, dst: &Path) -> std::io::Result<()> {
    let mut attempt = 0;
    loop {
        match fs::rename(src, dst) {
            Ok(()) => return Ok(()),
            Err(e) => {
                attempt += 1;
                if attempt >= 20 || !cfg!(windows) {
                    return Err(e);
                }
                std::thread::sleep(Duration::from_millis(20));
            }
        }
    }
}

/// One-line value: whitespace collapsed.
pub fn clean(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// A stable short hex key for a string (cache file names). FNV-1a, 64 bit.
pub fn hash_key(text: &str) -> String {
    let mut hash: u64 = 0xcbf29ce484222325;
    for byte in text.as_bytes() {
        hash ^= *byte as u64;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    format!("{hash:016x}")
}

/// A command without a console window on Windows.
pub fn command(program: impl AsRef<std::ffi::OsStr>) -> std::process::Command {
    #[allow(unused_mut)]
    let mut cmd = std::process::Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd
}

/// Runs a command and returns (exit code, stdout), or (None, "") when it cannot run or times out.
pub fn run_command(program: &str, args: &[&str], timeout: Duration) -> (Option<i32>, String) {
    use std::io::Read;
    use std::process::Stdio;
    use std::time::Instant;
    let mut child = match command(program)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(child) => child,
        Err(_) => return (None, String::new()),
    };
    let mut stdout = child.stdout.take();
    let reader = std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(out) = stdout.as_mut() {
            let _ = out.read_to_end(&mut buf);
        }
        buf
    });
    let deadline = Instant::now() + timeout;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(10)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
        }
    };
    let out = reader.join().unwrap_or_default();
    match status {
        Some(status) => (status.code(), String::from_utf8_lossy(&out).into_owned()),
        None => (None, String::new()),
    }
}

/// Is `program` found on the PATH?
pub fn which(program: &str) -> Option<std::path::PathBuf> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join(program);
        if candidate.is_file() {
            return Some(candidate);
        }
        if cfg!(windows) {
            let exe = dir.join(format!("{program}.exe"));
            if exe.is_file() {
                return Some(exe);
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clean_collapses_whitespace() {
        assert_eq!(clean("  a \n b\t c "), "a b c");
    }

    #[test]
    fn hash_key_is_stable() {
        assert_eq!(hash_key("abc"), hash_key("abc"));
        assert_ne!(hash_key("abc"), hash_key("abd"));
        assert_eq!(hash_key("").len(), 16);
    }

    #[test]
    fn write_atomic_replaces() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("a/b.txt");
        write_atomic(&path, "one").unwrap();
        write_atomic(&path, "two").unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "two");
        assert_eq!(fs::read_dir(path.parent().unwrap()).unwrap().count(), 1);
    }
}
