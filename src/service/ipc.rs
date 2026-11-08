//! Talking to the running service: the CLI and the gallery send it one JSON request at a time.
//!
//! The transport is a Unix socket on Linux and a localhost TCP port on Windows (the port is written to
//! a file in the user's private run folder). Either way a random key that only the user can read goes
//! with every request, so other accounts on the machine cannot drive your desktop. One request and one
//! reply per connection, each a single line of JSON.
use std::io::{BufRead, BufReader, Write};
#[cfg(unix)]
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::Sender;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::core::paths;
use crate::core::util::log;
use crate::platform::Waker;

pub const TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Debug)]
pub enum IpcError {
    NotRunning(String),
    Timeout(String),
    Refused(String),
}

impl std::fmt::Display for IpcError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            IpcError::NotRunning(m) | IpcError::Timeout(m) | IpcError::Refused(m) => f.write_str(m),
        }
    }
}

impl std::error::Error for IpcError {}

/// A request that reached the service, to be handled on its loop thread.
pub struct Request {
    pub command: String,
    pub args: Value,
    pub reply: Sender<Result<Value, String>>,
}

// ---------------------------------------------------------------------------------------------
// transport
#[cfg(unix)]
mod transport {
    use super::*;
    use std::os::unix::net::{UnixListener, UnixStream};

    pub type Stream = UnixStream;
    pub struct Listener(pub UnixListener, pub PathBuf);

    const UNIX_PATH_MAX: usize = 100; // sun_path is 108 bytes on Linux (104 on BSD), including the terminating NUL

    pub fn address() -> PathBuf {
        let path = paths::socket();
        if path.as_os_str().len() > UNIX_PATH_MAX {
            // a very long $XDG_RUNTIME_DIR: use a short, stable path
            let digest = crate::core::util::hash_key(&path.to_string_lossy());
            return PathBuf::from(format!("/tmp/deskwidgets-{}-{}.sock", paths::unsafe_uid(), &digest[..10]));
        }
        path
    }

    pub fn connect() -> std::io::Result<Stream> {
        UnixStream::connect(address())
    }

    pub fn listen() -> std::io::Result<Listener> {
        let address = address();
        let listener = UnixListener::bind(&address)?;
        Ok(Listener(listener, address))
    }

    pub fn exists() -> bool {
        address().exists()
    }

    pub fn cleanup() {
        let _ = std::fs::remove_file(address());
    }

    pub fn path() -> PathBuf {
        address()
    }

    impl Listener {
        pub fn accept(&self) -> std::io::Result<Stream> {
            self.0.accept().map(|(s, _)| s)
        }
        pub fn unblock(&self) {
            let _ = UnixStream::connect(&self.1);
        }
    }

    pub fn set_timeouts(s: &Stream, timeout: Duration) {
        let _ = s.set_read_timeout(Some(timeout));
        let _ = s.set_write_timeout(Some(timeout));
    }
}

#[cfg(windows)]
mod transport {
    use super::*;
    use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};

    pub type Stream = TcpStream;
    pub struct Listener(pub TcpListener, pub SocketAddr);

    fn port() -> Option<u16> {
        std::fs::read_to_string(paths::socket()).ok()?.trim().parse().ok()
    }

    pub fn connect() -> std::io::Result<Stream> {
        let port = port().ok_or_else(|| std::io::Error::new(std::io::ErrorKind::NotFound, "no port file"))?;
        TcpStream::connect_timeout(&SocketAddr::from((Ipv4Addr::LOCALHOST, port)), Duration::from_secs(2))
    }

    pub fn listen() -> std::io::Result<Listener> {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))?;
        let addr = listener.local_addr()?;
        crate::core::util::write_atomic(&paths::socket(), &addr.port().to_string())?;
        Ok(Listener(listener, addr))
    }

    pub fn exists() -> bool {
        paths::socket().exists()
    }

    pub fn cleanup() {
        let _ = std::fs::remove_file(paths::socket());
    }

    impl Listener {
        pub fn accept(&self) -> std::io::Result<Stream> {
            self.0.accept().map(|(s, _)| s)
        }
        pub fn unblock(&self) {
            let _ = TcpStream::connect(self.1);
        }
    }

    pub fn set_timeouts(s: &Stream, timeout: Duration) {
        let _ = s.set_read_timeout(Some(timeout));
        let _ = s.set_write_timeout(Some(timeout));
    }
}

fn read_key() -> Option<String> {
    std::fs::read_to_string(paths::auth_key()).ok().map(|s| s.trim().to_string()).filter(|s| !s.is_empty())
}

fn create_key() -> std::io::Result<String> {
    if let Some(key) = read_key() {
        return Ok(key);
    }
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|e| std::io::Error::other(e.to_string()))?;
    let key: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
    std::fs::create_dir_all(paths::run())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        let mut file = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(paths::auth_key())?;
        file.write_all(key.as_bytes())?;
    }
    #[cfg(not(unix))]
    std::fs::write(paths::auth_key(), &key)?;
    Ok(key)
}

/// Where the service listens (a Unix socket path on Linux), for tests.
#[cfg(unix)]
pub fn socket_address() -> PathBuf {
    transport::path()
}

// ---------------------------------------------------------------------------------------------
// client
/// Sends `command` to the service and returns its answer (any JSON value).
pub fn request(command: &str, args: Value, timeout: Duration) -> Result<Value, IpcError> {
    let key = read_key().ok_or_else(|| IpcError::NotRunning("no control key".into()))?;
    let mut stream = None;
    for attempt in 0..3 {
        match transport::connect() {
            Ok(s) => {
                stream = Some(s);
                break;
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock && attempt < 2 => std::thread::sleep(Duration::from_millis(100)),
            Err(e) => return Err(IpcError::NotRunning(e.to_string())),
        }
    }
    let mut stream = stream.ok_or_else(|| IpcError::NotRunning("the service is busy".into()))?;
    transport::set_timeouts(&stream, timeout);
    let message = json!({"key": key, "cmd": command, "args": args}).to_string() + "\n";
    stream.write_all(message.as_bytes()).map_err(|e| IpcError::NotRunning(e.to_string()))?;
    let mut line = String::new();
    match BufReader::new(&stream).read_line(&mut line) {
        Ok(0) => return Err(IpcError::NotRunning("the service closed the connection".into())),
        Ok(_) => {}
        Err(e) if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => {
            return Err(IpcError::Timeout(format!("the service did not answer \"{command}\"")))
        }
        Err(e) => return Err(IpcError::NotRunning(e.to_string())),
    }
    let reply: Value = serde_json::from_str(&line).map_err(|e| IpcError::Refused(e.to_string()))?;
    if reply.get("ok").and_then(Value::as_bool) == Some(true) {
        Ok(reply.get("result").cloned().unwrap_or(Value::Null))
    } else {
        Err(IpcError::Refused(reply.get("error").and_then(Value::as_str).unwrap_or("request failed").to_string()))
    }
}

pub fn is_running() -> bool {
    request("ping", json!({}), Duration::from_secs(2)).is_ok()
}

/// Starts `deskwidgets run` in the background, writing its messages to the log file.
pub fn start_service() -> std::io::Result<()> {
    std::fs::create_dir_all(paths::cache())?;
    let log_file = std::fs::OpenOptions::new().create(true).append(true).open(paths::service_log())?;
    let mut cmd = std::process::Command::new(paths::exe());
    cmd.arg("run").stdin(std::process::Stdio::null()).stdout(log_file.try_clone()?).stderr(log_file);
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
    let mut child = cmd.spawn()?;
    std::thread::spawn(move || {
        let _ = child.wait();
    });
    Ok(())
}

/// True when the service answers, starting it first if needed.
pub fn ensure_running(wait: Duration) -> bool {
    if is_running() {
        return true;
    }
    if let Err(e) = start_service() {
        log(format!("cannot start the service: {e}"));
        return false;
    }
    let deadline = Instant::now() + wait;
    while Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(150));
        if is_running() {
            return true;
        }
    }
    false
}

// ---------------------------------------------------------------------------------------------
// server
#[derive(Debug)]
pub struct AlreadyRunning;

/// Accepts requests on a background thread; each one is queued for the service's loop and the loop is woken.
pub struct ControlServer {
    stopped: Arc<AtomicBool>,
    listener: Arc<transport::Listener>,
}

impl ControlServer {
    pub fn start(queue: Sender<Request>, waker: Waker) -> Result<ControlServer, Result<AlreadyRunning, std::io::Error>> {
        std::fs::create_dir_all(paths::run()).map_err(Err)?;
        if transport::exists() {
            if is_running() {
                return Err(Ok(AlreadyRunning));
            }
            transport::cleanup(); // left behind by a crashed service
        }
        let key = create_key().map_err(Err)?;
        let listener = Arc::new(transport::listen().map_err(Err)?);
        let stopped = Arc::new(AtomicBool::new(false));
        let (l, s) = (listener.clone(), stopped.clone());
        std::thread::Builder::new()
            .name("control".into())
            .spawn(move || {
                while !s.load(Ordering::Relaxed) {
                    let Ok(stream) = l.accept() else { continue };
                    if s.load(Ordering::Relaxed) {
                        break;
                    }
                    let (queue, waker, key) = (queue.clone(), waker.clone(), key.clone());
                    std::thread::spawn(move || handle(stream, &key, queue, waker));
                }
            })
            .map_err(Err)?;
        Ok(ControlServer { stopped, listener })
    }

    pub fn stop(&self) {
        self.stopped.store(true, Ordering::Relaxed);
        self.listener.unblock();
        transport::cleanup();
    }
}

fn handle(mut stream: transport::Stream, key: &str, queue: Sender<Request>, waker: Waker) {
    transport::set_timeouts(&stream, TIMEOUT);
    let mut line = String::new();
    if BufReader::new(&stream).read_line(&mut line).unwrap_or(0) == 0 {
        return;
    }
    let Ok(message) = serde_json::from_str::<Value>(&line) else { return };
    if message.get("key").and_then(Value::as_str) != Some(key) {
        return; // not for us: say nothing
    }
    let Some(command) = message.get("cmd").and_then(Value::as_str) else { return };
    let args = message.get("args").cloned().filter(Value::is_object).unwrap_or_else(|| json!({}));
    let (tx, rx) = std::sync::mpsc::channel();
    if queue.send(Request { command: command.to_string(), args, reply: tx }).is_err() {
        return;
    }
    waker.wake();
    let reply = match rx.recv_timeout(TIMEOUT) {
        Ok(Ok(result)) => json!({"ok": true, "result": result}),
        Ok(Err(error)) => json!({"ok": false, "error": error}),
        Err(_) => json!({"ok": false, "error": "the service is busy"}),
    };
    let _ = stream.write_all((reply.to_string() + "\n").as_bytes());
}
