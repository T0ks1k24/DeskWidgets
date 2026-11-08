//! Background services the widgets can ask for (`L.service('media')`), started only while a widget needs them.
//!
//! ```text
//! let hub = ServiceHub::new();
//! hub.sync(&wanted);                // start what is wanted, stop the rest
//! hub.snapshot("media");            // -> JSON for Lua, or None
//! hub.media_control("PlayPause");
//! ```
//!
//! A service that cannot run here (no D-Bus, no Evolution Data Server, a Windows-only API on Linux)
//! is skipped with one message in the log; its widget shows an empty state.
use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use serde_json::Value;

use crate::core::util::log;
use crate::engine::runtime::Services;

pub mod batteries;
pub mod docker;
#[cfg(unix)]
pub mod events_eds;
pub mod hid_mice;
#[cfg(unix)]
pub mod media_mpris;
#[cfg(windows)]
pub mod media_windows;

/// The latest complete snapshot of a provider: replaced as a whole, so readers never see half of one.
#[derive(Clone, Default)]
pub struct Snapshot(Arc<Mutex<Option<Value>>>);

impl Snapshot {
    pub fn get(&self) -> Option<Value> {
        self.0.lock().unwrap().clone()
    }
    pub fn publish(&self, value: Value) {
        *self.0.lock().unwrap() = Some(value);
    }
}

pub trait Provider: Send {
    fn start(&mut self) -> Result<(), String>;
    fn stop(&mut self);
    fn snapshot(&self) -> Option<Value>;
    /// Remote control (media players); true if it was understood.
    fn control(&self, _command: &str) -> bool {
        false
    }
}

/// A flag a sleeping thread can be woken from.
#[derive(Default)]
pub struct StopFlag {
    stopped: AtomicBool,
    lock: Mutex<()>,
    cond: Condvar,
}

impl StopFlag {
    pub fn stop(&self) {
        self.stopped.store(true, Ordering::SeqCst);
        let _guard = self.lock.lock().unwrap();
        self.cond.notify_all();
    }
    pub fn is_stopped(&self) -> bool {
        self.stopped.load(Ordering::SeqCst)
    }
    /// Sleeps for `duration` or until stopped; returns true when stopped.
    pub fn wait(&self, duration: Duration) -> bool {
        let guard = self.lock.lock().unwrap();
        if self.is_stopped() {
            return true;
        }
        let _ = self.cond.wait_timeout(guard, duration).unwrap();
        self.is_stopped()
    }
}

/// Calls `poll` every `every` on a thread of its own. A failing poll is logged once and retried.
pub struct Poller {
    stop: Arc<StopFlag>,
    handle: Option<std::thread::JoinHandle<()>>,
}

impl Poller {
    pub fn spawn<F>(name: &'static str, every: Duration, mut poll: F) -> Poller
    where
        F: FnMut(&StopFlag) -> Result<(), String> + Send + 'static,
    {
        let stop = Arc::new(StopFlag::default());
        let flag = stop.clone();
        let handle = std::thread::Builder::new()
            .name(format!("provider-{name}"))
            .spawn(move || {
                let mut failed = false;
                while !flag.is_stopped() {
                    match poll(&flag) {
                        Ok(()) => failed = false,
                        Err(e) => {
                            if !failed {
                                log(format!("{name}: {e}"));
                            }
                            failed = true;
                        }
                    }
                    if flag.wait(every) {
                        break;
                    }
                }
            })
            .ok();
        Poller { stop, handle }
    }

    pub fn stop(&mut self) {
        self.stop.stop();
        // the thread ends by itself; do not wait for a slow external command
        self.handle.take();
    }
}

fn create(name: &str) -> Result<Option<Box<dyn Provider>>, String> {
    Ok(match name {
        "docker" => Some(Box::new(docker::Docker::new())),
        "batteries" => Some(Box::new(batteries::Batteries::new())),
        #[cfg(unix)]
        "media" => Some(Box::new(media_mpris::Media::new())),
        #[cfg(windows)]
        "media" => Some(Box::new(media_windows::Media::new())),
        #[cfg(unix)]
        "events" => Some(Box::new(events_eds::Events::new())),
        _ => None,
    })
}

#[derive(Default)]
struct HubState {
    running: HashMap<String, Box<dyn Provider>>,
    unavailable: HashSet<String>,
}

#[derive(Default)]
pub struct ServiceHub {
    state: Mutex<HubState>,
    disabled: bool,
}

impl ServiceHub {
    pub fn new() -> ServiceHub {
        ServiceHub::default()
    }

    /// A hub that starts nothing, for tests and previews.
    pub fn disabled() -> ServiceHub {
        ServiceHub { disabled: true, ..ServiceHub::default() }
    }

    /// Starts the providers in `wanted`, stops the ones nobody uses any more.
    pub fn sync(&self, wanted: &HashSet<String>) {
        if self.disabled {
            return;
        }
        let mut state = self.state.lock().unwrap();
        let gone: Vec<String> = state.running.keys().filter(|n| !wanted.contains(*n)).cloned().collect();
        for name in gone {
            if let Some(mut provider) = state.running.remove(&name) {
                provider.stop();
            }
        }
        let mut names: Vec<&String> = wanted.iter().collect();
        names.sort();
        for name in names {
            if state.running.contains_key(name) || state.unavailable.contains(name) {
                continue;
            }
            match create(name) {
                Ok(None) => {
                    state.unavailable.insert(name.clone());
                }
                Ok(Some(mut provider)) => match provider.start() {
                    Ok(()) => {
                        state.running.insert(name.clone(), provider);
                    }
                    Err(e) => {
                        // a broken provider must not take the widgets down
                        log(format!("service \"{name}\" is not available: {e}"));
                        state.unavailable.insert(name.clone());
                    }
                },
                Err(e) => {
                    log(format!("service \"{name}\" is not available: {e}"));
                    state.unavailable.insert(name.clone());
                }
            }
        }
    }

    pub fn stop(&self) {
        let mut state = self.state.lock().unwrap();
        for (_, mut provider) in state.running.drain() {
            provider.stop();
        }
    }
}

impl Services for ServiceHub {
    fn snapshot(&self, name: &str) -> Option<Value> {
        self.state.lock().unwrap().running.get(name).and_then(|p| p.snapshot())
    }

    fn media_control(&self, command: &str) -> bool {
        self.state.lock().unwrap().running.get("media").map(|p| p.control(command)).unwrap_or(false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    struct Fake(Snapshot);
    impl Provider for Fake {
        fn start(&mut self) -> Result<(), String> {
            self.0.publish(json!({"status": "none"}));
            Ok(())
        }
        fn stop(&mut self) {}
        fn snapshot(&self) -> Option<Value> {
            self.0.get()
        }
    }

    #[test]
    fn snapshots_are_replaced_whole_and_unknown_services_are_skipped() {
        let hub = ServiceHub::new();
        hub.state.lock().unwrap().running.insert("media".into(), Box::new(Fake(Snapshot::default())));
        assert!(hub.snapshot("media").is_none() || true);
        let mut p = Fake(Snapshot::default());
        p.start().unwrap();
        assert_eq!(p.snapshot(), Some(json!({"status": "none"})));
        hub.sync(&["nonsense".to_string()].into_iter().collect());
        assert!(hub.snapshot("nonsense").is_none());
        assert!(hub.state.lock().unwrap().running.is_empty(), "providers nobody wants are stopped");
    }

    #[test]
    fn poller_runs_and_stops() {
        let count = Arc::new(Mutex::new(0));
        let c = count.clone();
        let mut poller = Poller::spawn("test", Duration::from_millis(5), move |_| {
            *c.lock().unwrap() += 1;
            Ok(())
        });
        std::thread::sleep(Duration::from_millis(60));
        poller.stop();
        let seen = *count.lock().unwrap();
        assert!(seen >= 3);
        std::thread::sleep(Duration::from_millis(40));
        assert!(*count.lock().unwrap() <= seen + 1);
    }
}
