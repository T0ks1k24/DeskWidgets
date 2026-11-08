//! Downloads for widgets, without ever blocking a frame.
//!
//! `get()` returns what has been fetched so far and starts (or refreshes) the download in the
//! background; widgets call it from `tick()` every time and read the answer when it arrives:
//!
//! ```text
//! local body, status, age, stamp = L.http('https://example.com/data.json', { every = 900 })
//! ```
//!
//! `download()` does the same for files (cover art, pictures) and returns the local path once it exists.
use std::collections::HashMap;
use std::io::Read;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use crate::core::util::{hash_key, log, write_atomic_bytes};
use crate::core::paths;

const MAX_BODY: u64 = 8 << 20;
const MAX_FILE: u64 = 16 << 20;
const RETRY_AFTER: f64 = 30.0; // seconds before a failed request is tried again
const TIMEOUT: Duration = Duration::from_secs(20);

#[derive(Clone, Debug, Default)]
pub struct Request {
    pub url: String,
    pub method: String,
    pub body: Option<String>,
    pub headers: Vec<(String, String)>,
    pub every: f64, // seconds after which the data is fetched again
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Reply {
    pub body: Option<Arc<str>>,
    pub status: u16,
    pub age: f64,   // seconds since the data was fetched, 0 when there is none
    pub stamp: u64, // grows with every successful fetch: re-parse only when it changes
}

#[derive(Default)]
struct Entry {
    body: Option<Arc<str>>,
    status: u16,
    fetched: Option<Instant>,
    attempted: Option<Instant>,
    in_flight: bool,
    stamp: u64,
    reported: bool,
}

fn cache() -> &'static Mutex<HashMap<String, Entry>> {
    static CACHE: OnceLock<Mutex<HashMap<String, Entry>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn agent() -> &'static ureq::Agent {
    static AGENT: OnceLock<ureq::Agent> = OnceLock::new();
    AGENT.get_or_init(|| {
        ureq::AgentBuilder::new()
            .timeout(TIMEOUT)
            .user_agent(&format!("deskwidgets/{}", crate::VERSION))
            .build()
    })
}

fn key_of(req: &Request) -> String {
    hash_key(&format!("{} {}\n{}", req.method, req.url, req.body.as_deref().unwrap_or("")))
}

pub fn get(req: &Request) -> Reply {
    let key = key_of(req);
    let now = Instant::now();
    let mut start = false;
    let reply = {
        let mut map = cache().lock().unwrap();
        let entry = map.entry(key.clone()).or_default();
        if !entry.in_flight {
            let every = if req.every > 0.0 { req.every } else { 900.0 };
            let since_attempt = entry.attempted.map(|t| now.duration_since(t).as_secs_f64());
            start = match (entry.fetched, since_attempt) {
                (_, None) => true,
                (None, Some(s)) => s >= RETRY_AFTER,
                (Some(f), Some(s)) => now.duration_since(f).as_secs_f64() >= every && s >= every.min(60.0),
            };
            if start {
                entry.in_flight = true;
                entry.attempted = Some(now);
            }
        }
        Reply {
            body: entry.body.clone(),
            status: entry.status,
            age: entry.fetched.map(|t| now.duration_since(t).as_secs_f64()).unwrap_or(0.0),
            stamp: entry.stamp,
        }
    };
    if start {
        let req = req.clone();
        std::thread::Builder::new()
            .name("http".into())
            .spawn(move || {
                let outcome = fetch(&req);
                let mut map = cache().lock().unwrap();
                let entry = map.entry(key).or_default();
                entry.in_flight = false;
                match outcome {
                    Ok((status, body)) => {
                        entry.body = Some(Arc::from(body));
                        entry.status = status;
                        entry.fetched = Some(Instant::now());
                        entry.stamp += 1;
                        entry.reported = false;
                    }
                    Err(e) => {
                        if !entry.reported {
                            entry.reported = true;
                            log(format!("download {}: {e}", req.url));
                        }
                    }
                }
            })
            .ok();
    }
    reply
}

fn fetch(req: &Request) -> Result<(u16, String), String> {
    let method = if req.method.is_empty() { "GET" } else { req.method.as_str() };
    let mut request = agent().request(method, &req.url);
    for (name, value) in &req.headers {
        request = request.set(name, value);
    }
    let response = match &req.body {
        Some(body) => request.send_string(body),
        None => request.call(),
    }
    .map_err(|e| e.to_string())?;
    let status = response.status();
    let mut bytes = Vec::new();
    response.into_reader().take(MAX_BODY).read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    Ok((status, String::from_utf8_lossy(&bytes).into_owned()))
}

/// Is a download still running? (`preview --live` waits for them.)
pub fn pending() -> bool {
    cache().lock().unwrap().values().any(|e| e.in_flight) || downloads().lock().unwrap().values().any(|d| d.in_flight)
}

/// Downloads `url` into the file `path` (written whole or not at all).
pub fn fetch_to_file(url: &str, path: &std::path::Path, limit: u64) -> Result<(), String> {
    let response = agent().get(url).call().map_err(|e| e.to_string())?;
    let mut bytes = Vec::new();
    response.into_reader().take(limit).read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    write_atomic_bytes(path, &bytes).map_err(|e| e.to_string())
}

// files
struct Download {
    attempted: Instant,
    in_flight: bool,
}

fn downloads() -> &'static Mutex<HashMap<String, Download>> {
    static DOWNLOADS: OnceLock<Mutex<HashMap<String, Download>>> = OnceLock::new();
    DOWNLOADS.get_or_init(|| Mutex::new(HashMap::new()))
}

/// Where `download(url)` puts the file.
pub fn download_path(url: &str) -> PathBuf {
    paths::images().join(format!("dl-{}", hash_key(url)))
}

/// The local file for `url` once it has been downloaded; until then None, and the download runs in the
/// background. A file is downloaded once and kept.
pub fn download(url: &str) -> Option<PathBuf> {
    let path = download_path(url);
    if path.is_file() {
        return Some(path);
    }
    let now = Instant::now();
    {
        let mut map = downloads().lock().unwrap();
        let entry = map.entry(url.to_string()).or_insert(Download { attempted: now - Duration::from_secs(3600), in_flight: false });
        if entry.in_flight || now.duration_since(entry.attempted).as_secs_f64() < RETRY_AFTER {
            return None;
        }
        entry.in_flight = true;
        entry.attempted = now;
    }
    let url = url.to_string();
    std::thread::Builder::new()
        .name("download".into())
        .spawn(move || {
            if let Err(e) = fetch_to_file(&url, &download_path(&url), MAX_FILE) {
                log(format!("download {url}: {e}"));
            }
            if let Some(entry) = downloads().lock().unwrap().get_mut(&url) {
                entry.in_flight = false;
            }
        })
        .ok();
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write as _;
    use std::net::TcpListener;

    fn serve_once(body: &'static str) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        std::thread::spawn(move || {
            for stream in listener.incoming().take(2) {
                let mut stream = stream.unwrap();
                let mut buf = [0u8; 2048];
                let _ = stream.read(&mut buf);
                let _ = write!(stream, "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body);
            }
        });
        format!("http://{addr}/x")
    }

    #[test]
    fn get_returns_nothing_first_then_the_body_without_blocking() {
        let url = serve_once("hello");
        let req = Request { url, every: 60.0, ..Request::default() };
        let first = get(&req);
        assert!(first.body.is_none());
        let mut reply = first;
        for _ in 0..100 {
            std::thread::sleep(Duration::from_millis(50));
            reply = get(&req);
            if reply.body.is_some() {
                break;
            }
        }
        assert_eq!(reply.body.as_deref(), Some("hello"));
        assert_eq!(reply.status, 200);
        assert_eq!(reply.stamp, 1);
        assert_eq!(get(&req).stamp, 1, "fresh data is not fetched again");
    }
}
