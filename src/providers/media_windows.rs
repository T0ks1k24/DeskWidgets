//! Now playing on Windows: the system media controls (the same session the volume flyout shows).
//!
//! Uses the WinRT `GlobalSystemMediaTransportControlsSessionManager` through the `windows` crate. The player
//! is polled once a second: cheap, and robust against players that send no change notifications.
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use windows::Media::Control::{
    GlobalSystemMediaTransportControlsSession, GlobalSystemMediaTransportControlsSessionManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
};
use windows::Storage::Streams::DataReader;

use super::{Poller, Provider, Snapshot};
use crate::core::paths;
use crate::core::util::{clean, hash_key, write_atomic_bytes};

pub struct Media {
    snapshot: Snapshot,
    poller: Option<Poller>,
    session: std::sync::Arc<std::sync::Mutex<Option<GlobalSystemMediaTransportControlsSession>>>,
}

impl Media {
    pub fn new() -> Media {
        Media { snapshot: Snapshot::default(), poller: None, session: Default::default() }
    }
}

/// "Spotify.exe" or "Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic" -> a readable name.
pub fn player_name(app_id: &str) -> String {
    let name = app_id.split('!').next().unwrap_or("").split('_').next().unwrap_or("");
    let name = name.strip_suffix(".exe").unwrap_or(name);
    name.rsplit('.').next().unwrap_or("").to_string()
}

fn read(manager: &GlobalSystemMediaTransportControlsSessionManager, art: &mut (String, String)) -> windows::core::Result<Value> {
    let session = match manager.GetCurrentSession() {
        Ok(s) => s,
        Err(_) => return Ok(json!({"status": "none"})),
    };
    let playback = session.GetPlaybackInfo()?;
    let status = match playback.PlaybackStatus()? {
        Status::Playing => "Playing",
        Status::Paused => "Paused",
        _ => return Ok(json!({"status": "none"})),
    };
    let props = session.TryGetMediaPropertiesAsync()?.get()?;
    let title = props.Title()?.to_string();
    if title.is_empty() {
        return Ok(json!({"status": "none"}));
    }
    let timeline = session.GetTimelineProperties()?;
    let secs = |t: windows::Foundation::TimeSpan| t.Duration as f64 / 1e7;
    let length = (secs(timeline.EndTime()?) - secs(timeline.StartTime()?)).max(0.0);
    let key = format!("{title}\u{0}{}", props.Artist()?);
    if art.0 != key {
        art.0 = key.clone();
        art.1 = String::new();
        if let Ok(thumbnail) = props.Thumbnail() {
            if let Ok(stream) = thumbnail.OpenReadAsync()?.get() {
                let size = stream.Size()? as u32;
                if size > 0 {
                    let reader = DataReader::CreateDataReader(&stream.GetInputStreamAt(0)?)?;
                    reader.LoadAsync(size)?.get()?;
                    let mut buffer = vec![0u8; size as usize];
                    reader.ReadBytes(&mut buffer)?;
                    let out = paths::images().join(format!("art-{}", &hash_key(&key)[..16]));
                    if write_atomic_bytes(&out, &buffer).is_ok() {
                        art.1 = out.to_string_lossy().into_owned();
                    }
                }
            }
        }
    }
    let now = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
    Ok(json!({
        "status": status, "player": player_name(&session.SourceAppUserModelId()?.to_string()),
        "title": clean(&title), "artist": clean(&props.Artist()?.to_string()), "album": clean(&props.AlbumTitle()?.to_string()),
        "length": length, "pos": secs(timeline.Position()?), "ts": now,
        "rate": playback.PlaybackRate().ok().and_then(|r| r.Value().ok()).unwrap_or(1.0), "art": art.1,
    }))
}

impl Provider for Media {
    fn start(&mut self) -> Result<(), String> {
        let snapshot = self.snapshot.clone();
        snapshot.publish(json!({"status": "none"}));
        let shared = self.session.clone();
        let mut manager: Option<GlobalSystemMediaTransportControlsSessionManager> = None;
        let mut art = (String::new(), String::new());
        self.poller = Some(Poller::spawn("media", Duration::from_secs(1), move |_| {
            if manager.is_none() {
                manager = Some(
                    GlobalSystemMediaTransportControlsSessionManager::RequestAsync()
                        .and_then(|op| op.get())
                        .map_err(|e| e.to_string())?,
                );
            }
            let m = manager.as_ref().unwrap();
            *shared.lock().unwrap() = m.GetCurrentSession().ok();
            match read(m, &mut art) {
                Ok(v) => snapshot.publish(v),
                Err(_) => snapshot.publish(json!({"status": "none"})), // the session can vanish between two calls
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
        let Some(session) = self.session.lock().unwrap().clone() else { return false };
        let known = matches!(command, "PlayPause" | "Next" | "Previous" | "Play" | "Pause" | "Stop");
        let command = command.to_string();
        std::thread::spawn(move || {
            let _ = match command.as_str() {
                "PlayPause" => session.TryTogglePlayPauseAsync().and_then(|o| o.get()),
                "Next" => session.TrySkipNextAsync().and_then(|o| o.get()),
                "Previous" => session.TrySkipPreviousAsync().and_then(|o| o.get()),
                "Play" => session.TryPlayAsync().and_then(|o| o.get()),
                "Pause" => session.TryPauseAsync().and_then(|o| o.get()),
                "Stop" => session.TryStopAsync().and_then(|o| o.get()),
                _ => Ok(false),
            };
        });
        known
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn app_ids() {
        assert_eq!(player_name("Spotify.exe"), "Spotify");
        assert_eq!(player_name("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic"), "ZuneMusic");
        assert_eq!(player_name(""), "");
    }
}
