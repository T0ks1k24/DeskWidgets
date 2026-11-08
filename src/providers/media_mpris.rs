//! Now playing on Linux: follows the MPRIS players on the session bus (Firefox, Spotify, VLC ...).
//!
//! Players are polled once a second: a `GetAll` per player is cheap, and polling is robust against
//! players that send incomplete change notifications. Position is read each time, so the progress
//! bar needs no extrapolation beyond the second between two polls.
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use zbus::blocking::Connection;
use zbus::zvariant::{OwnedValue, Value as ZValue};

use super::{Poller, Provider, Snapshot};
use crate::core::paths;
use crate::core::util::{clean, hash_key, log};
use crate::engine::http;

const MPRIS: &str = "org.mpris.MediaPlayer2";
const MPRIS_PLAYER: &str = "org.mpris.MediaPlayer2.Player";
const MPRIS_PATH: &str = "/org/mpris/MediaPlayer2";
const COMMANDS: [&str; 6] = ["PlayPause", "Play", "Pause", "Next", "Previous", "Stop"];
const ART_LIMIT: u64 = 16 << 20;

/// Turns "Mozilla firefox_firefox" (snap) into "Firefox".
pub fn player_name(identity: &str) -> String {
    let name = identity.replace("Mozilla ", "");
    let name = name.split('_').next().unwrap_or("").trim().to_string();
    let mut chars = name.chars();
    chars.next().map(|f| f.to_uppercase().collect::<String>() + chars.as_str()).unwrap_or_default()
}

fn text(v: &ZValue) -> Option<String> {
    match v {
        ZValue::Str(s) => Some(s.to_string()),
        ZValue::ObjectPath(p) => Some(p.to_string()),
        ZValue::Value(inner) => text(inner),
        _ => None,
    }
}

fn number(v: &ZValue) -> Option<f64> {
    match v {
        ZValue::I64(n) => Some(*n as f64),
        ZValue::U64(n) => Some(*n as f64),
        ZValue::I32(n) => Some(*n as f64),
        ZValue::U32(n) => Some(*n as f64),
        ZValue::F64(n) => Some(*n),
        ZValue::Value(inner) => number(inner),
        _ => None,
    }
}

fn strings(v: &ZValue) -> Vec<String> {
    match v {
        ZValue::Array(a) => a.iter().filter_map(text).collect(),
        ZValue::Value(inner) => strings(inner),
        other => text(other).into_iter().collect(),
    }
}

fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

#[derive(Clone, Debug, Default)]
struct PlayerState {
    name: String,
    identity: String,
    status: String,
    trackid: String,
    title: String,
    artist: String,
    album: String,
    length: f64,
    pos: f64,
    ts: f64,
    rate: f64,
    art_url: String,
    changed: f64,
}

/// The player to show: the most recently changed one that is playing, else the most recent paused one.
fn active(players: &HashMap<String, PlayerState>) -> Option<&PlayerState> {
    for status in ["Playing", "Paused"] {
        let found = players.values().filter(|p| p.status == status && !p.title.is_empty()).max_by(|a, b| a.changed.partial_cmp(&b.changed).unwrap_or(std::cmp::Ordering::Equal));
        if found.is_some() {
            return found;
        }
    }
    None
}

struct Art {
    url: String,
    path: Arc<Mutex<String>>,
}

pub struct Media {
    snapshot: Snapshot,
    poller: Option<Poller>,
    connection: Option<Connection>,
    active_name: Arc<Mutex<Option<String>>>,
}

impl Media {
    pub fn new() -> Media {
        Media { snapshot: Snapshot::default(), poller: None, connection: None, active_name: Arc::new(Mutex::new(None)) }
    }
}

fn list_players(conn: &Connection) -> Result<Vec<String>, String> {
    let reply = conn
        .call_method(Some("org.freedesktop.DBus"), "/org/freedesktop/DBus", Some("org.freedesktop.DBus"), "ListNames", &())
        .map_err(|e| e.to_string())?;
    let names: Vec<String> = reply.body().deserialize().map_err(|e| e.to_string())?;
    Ok(names.into_iter().filter(|n| n.starts_with(&format!("{MPRIS}."))).collect())
}

fn read_player(conn: &Connection, state: &mut PlayerState) -> Result<(), String> {
    let reply = conn
        .call_method(Some(state.name.as_str()), MPRIS_PATH, Some("org.freedesktop.DBus.Properties"), "GetAll", &(MPRIS_PLAYER,))
        .map_err(|e| e.to_string())?;
    let props: HashMap<String, OwnedValue> = reply.body().deserialize().map_err(|e| e.to_string())?;
    let t = now();
    let status = props.get("PlaybackStatus").and_then(|v| text(v)).unwrap_or_else(|| "Stopped".into());
    let rate = props.get("Rate").and_then(|v| number(v)).filter(|r| *r != 0.0).unwrap_or(1.0);
    let (mut trackid, mut title, mut artist, mut album, mut length, mut art_url) = (String::new(), String::new(), String::new(), String::new(), 0.0, String::new());
    if let Some(ZValue::Dict(meta)) = props.get("Metadata").map(|v| &**v) {
        for (key, value) in meta.iter() {
            match text(key).as_deref() {
                Some("mpris:trackid") => trackid = text(value).unwrap_or_default(),
                Some("xesam:title") => title = text(value).unwrap_or_default(),
                Some("xesam:artist") => artist = strings(value).join(", "),
                Some("xesam:album") => album = text(value).unwrap_or_default(),
                Some("mpris:length") => length = number(value).unwrap_or(0.0) / 1e6,
                Some("mpris:artUrl") => art_url = text(value).unwrap_or_default(),
                _ => {}
            }
        }
    }
    if status != state.status || title != state.title || trackid != state.trackid {
        state.changed = t;
    }
    let new_track = (trackid.as_str(), title.as_str()) != (state.trackid.as_str(), state.title.as_str());
    state.status = status;
    state.rate = rate;
    state.trackid = trackid;
    state.title = title;
    state.artist = artist;
    state.album = album;
    state.length = length;
    state.art_url = art_url;
    // Position can be missing or fail; then the old one keeps running with the playback rate
    match props.get("Position").and_then(|v| number(v)) {
        Some(micros) => {
            state.pos = micros / 1e6;
            state.ts = t;
        }
        None => {
            if new_track {
                state.pos = 0.0;
                state.ts = t;
            } else if state.status == "Playing" {
                state.pos += (t - state.ts) * state.rate;
                state.ts = t;
            }
        }
    }
    Ok(())
}

fn read_identity(conn: &Connection, name: &str) -> Option<String> {
    let reply = conn.call_method(Some(name), MPRIS_PATH, Some("org.freedesktop.DBus.Properties"), "Get", &(MPRIS, "Identity")).ok()?;
    let value: OwnedValue = reply.body().deserialize().ok()?;
    text(&value)
}

/// Cover art: a local file is used as it is, a web address is downloaded once.
fn art_path(art: &mut Art, url: &str) -> String {
    if url != art.url {
        art.url = url.to_string();
        *art.path.lock().unwrap() = String::new();
        if let Some(rest) = url.strip_prefix("file://") {
            let decoded = percent_encoding::percent_decode_str(rest).decode_utf8_lossy().into_owned();
            *art.path.lock().unwrap() = decoded;
        } else if url.starts_with("https://") || url.starts_with("http://") {
            let out: PathBuf = paths::images().join(format!("art-{}", &hash_key(url)[..16]));
            let (url, shared) = (url.to_string(), art.path.clone());
            std::thread::spawn(move || {
                if !out.exists() {
                    if let Err(e) = http::fetch_to_file(&url, &out, ART_LIMIT) {
                        log(format!("cover art {}: {e}", url.chars().take(80).collect::<String>()));
                        return;
                    }
                }
                prune_art(&out);
                let mut slot = shared.lock().unwrap();
                *slot = out.to_string_lossy().into_owned();
            });
        }
    }
    art.path.lock().unwrap().clone()
}

/// Cover art older than the current one is not needed any more.
fn prune_art(keep: &std::path::Path) {
    let Ok(entries) = std::fs::read_dir(paths::images()) else { return };
    let mut files: Vec<(std::time::SystemTime, PathBuf)> = entries
        .filter_map(|e| e.ok())
        .filter(|e| e.file_name().to_string_lossy().starts_with("art-") && e.path() != keep)
        .filter_map(|e| Some((e.metadata().ok()?.modified().ok()?, e.path())))
        .collect();
    files.sort();
    if files.len() > 4 {
        for (_, old) in &files[..files.len() - 4] {
            let _ = std::fs::remove_file(old);
        }
    }
}

impl Provider for Media {
    fn start(&mut self) -> Result<(), String> {
        let conn = Connection::session().map_err(|e| e.to_string())?;
        self.connection = Some(conn.clone());
        let snapshot = self.snapshot.clone();
        snapshot.publish(json!({"status": "none"}));
        let active_name = self.active_name.clone();
        let mut players: HashMap<String, PlayerState> = HashMap::new();
        let mut art = Art { url: String::new(), path: Arc::new(Mutex::new(String::new())) };
        self.poller = Some(Poller::spawn("media", Duration::from_secs(1), move |_| {
            let names = list_players(&conn)?;
            players.retain(|n, _| names.contains(n));
            for name in &names {
                let state = players.entry(name.clone()).or_insert_with(|| PlayerState {
                    name: name.clone(),
                    identity: read_identity(&conn, name).unwrap_or_else(|| name[MPRIS.len() + 1..].split('.').next().unwrap_or("").to_string()),
                    status: "Stopped".into(),
                    rate: 1.0,
                    ..PlayerState::default()
                });
                // a player may vanish between the listing and the call
                let _ = read_player(&conn, state);
            }
            match active(&players) {
                None => {
                    *active_name.lock().unwrap() = None;
                    snapshot.publish(json!({"status": "none"}));
                }
                Some(p) => {
                    *active_name.lock().unwrap() = Some(p.name.clone());
                    let art = art_path(&mut art, &p.art_url);
                    snapshot.publish(json!({
                        "status": p.status, "player": clean(&player_name(&p.identity)),
                        "title": clean(&p.title), "artist": clean(&p.artist), "album": clean(&p.album),
                        "length": p.length, "pos": p.pos, "ts": p.ts, "rate": p.rate, "art": art,
                    }));
                }
            }
            Ok(())
        }));
        Ok(())
    }

    fn stop(&mut self) {
        if let Some(p) = self.poller.as_mut() {
            p.stop();
        }
    }

    fn snapshot(&self) -> Option<Value> {
        self.snapshot.get()
    }

    fn control(&self, command: &str) -> bool {
        if !COMMANDS.contains(&command) {
            return false;
        }
        let (Some(name), Some(conn)) = (self.active_name.lock().unwrap().clone(), self.connection.clone()) else { return false };
        let command = command.to_string();
        std::thread::spawn(move || {
            let _ = conn.call_method(Some(name.as_str()), MPRIS_PATH, Some(MPRIS_PLAYER), command.as_str(), &());
        });
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn player_names() {
        assert_eq!(player_name("Mozilla firefox_firefox"), "Firefox");
        assert_eq!(player_name("spotify"), "Spotify");
        assert_eq!(player_name(""), "");
    }

    #[test]
    fn the_playing_player_wins_over_a_paused_one() {
        let mk = |name: &str, status: &str, changed: f64| PlayerState { name: name.into(), status: status.into(), title: "t".into(), changed, ..PlayerState::default() };
        let mut players = HashMap::new();
        players.insert("a".to_string(), mk("a", "Paused", 100.0));
        players.insert("b".to_string(), mk("b", "Playing", 1.0));
        players.insert("c".to_string(), mk("c", "Playing", 5.0));
        assert_eq!(active(&players).map(|p| p.name.as_str()), Some("c"));
        players.remove("b");
        players.remove("c");
        assert_eq!(active(&players).map(|p| p.name.as_str()), Some("a"));
        players.get_mut("a").unwrap().title.clear();
        assert!(active(&players).is_none());
    }
}
