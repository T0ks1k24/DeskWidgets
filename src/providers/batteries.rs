//! Batteries of things that are not the laptop: Bluetooth mice and headphones (UPower) and
//! wireless gaming mice (their vendor HID protocol, see hid_mice.rs). Linux only; elsewhere the
//! list is simply empty.
use std::collections::HashMap;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use regex::Regex;
use serde_json::{json, Value};

use super::{hid_mice, Poller, Provider, Snapshot};
use crate::core::util::{clean, run_command};

const HID_SLEEP_GRACE: u64 = 1800; // a sleeping mouse keeps showing its last charge for this long (seconds)

/// Devices UPower knows that are not power supplies: [{name, percent, charging, online, kind}].
pub fn parse_upower(output: &str) -> Vec<Value> {
    let kind_re = Regex::new(r"(?m)^  ([a-z][\w-]*)\s*$").unwrap();
    let supply_re = Regex::new(r"power supply:\s*(\w+)").unwrap();
    let percent_re = Regex::new(r"percentage:\s*([\d.]+)%").unwrap();
    let model_re = Regex::new(r"model:\s*(.*)").unwrap();
    let state_re = Regex::new(r"state:\s*([\w-]+)").unwrap();
    let mut devices = Vec::new();
    for block in Regex::new(r"(?m)^Device:").unwrap().split(output).skip(1) {
        let path = block.split('\n').next().unwrap_or("").trim();
        let supply = supply_re.captures(block);
        let Some(percent) = percent_re.captures(block) else { continue };
        if supply.map(|s| &s[1] != "no").unwrap_or(false) || path.contains("DisplayDevice") {
            continue;
        }
        let kind = kind_re.captures(block).map(|k| k[1].to_string()).unwrap_or_default();
        let model = model_re.captures(block).map(|m| clean(&m[1])).unwrap_or_default();
        let charging = state_re.captures(block).map(|s| &s[1] == "charging").unwrap_or(false);
        devices.push(json!({
            "name": if model.is_empty() { kind.clone() } else { model },
            "percent": percent[1].parse::<f64>().unwrap_or(0.0),
            "charging": charging, "online": true, "kind": kind,
        }));
    }
    devices
}

pub struct Batteries {
    snapshot: Snapshot,
    poller: Option<Poller>,
}

impl Batteries {
    pub fn new() -> Batteries {
        Batteries { snapshot: Snapshot::default(), poller: None }
    }
}

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

impl Provider for Batteries {
    fn start(&mut self) -> Result<(), String> {
        let snapshot = self.snapshot.clone();
        if cfg!(windows) {
            snapshot.publish(json!({"devices": [], "denied": ""}));
            return Ok(());
        }
        let mut last_hid: HashMap<String, (hid_mice::Reading, u64)> = HashMap::new();
        self.poller = Some(Poller::spawn("batteries", Duration::from_secs(30), move |_| {
            let (mut devices, mut denied) = (Vec::<Value>::new(), Vec::<String>::new());
            for dev in hid_mice::find() {
                if !dev.access {
                    denied.push(dev.name.clone());
                    continue;
                }
                let t = now();
                let mut reading = hid_mice::read(&dev);
                match &reading {
                    Some(r) if r.online => {
                        last_hid.insert(dev.node.clone(), (r.clone(), t));
                    }
                    _ => {
                        if let Some((old, at)) = last_hid.get(&dev.node) {
                            if t - at < HID_SLEEP_GRACE {
                                reading = Some(hid_mice::Reading { online: false, ..old.clone() });
                            }
                        }
                    }
                }
                if let Some(r) = reading {
                    devices.push(json!({
                        "name": clean(&dev.name), "percent": r.pct, "charging": r.charging, "online": r.online, "kind": "mouse",
                    }));
                }
            }
            let (code, output) = run_command("upower", &["-d"], Duration::from_secs(10));
            if code == Some(0) {
                devices.extend(parse_upower(&output));
            }
            snapshot.publish(json!({"devices": devices, "denied": denied.join(", ")}));
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
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_upower_dump() {
        let dump = "Device: /org/freedesktop/UPower/devices/battery_BAT0\n  power supply: yes\n  percentage: 80%\n\n\
Device: /org/freedesktop/UPower/devices/mouse_hid_aa\n  native-path: x\n  model: MX Master\n  power supply: no\n  \
updated: now\n  has history: yes\n  mouse\n    state: charging\n    percentage: 55%\n\n\
Device: /org/freedesktop/UPower/devices/DisplayDevice\n  power supply: no\n  percentage: 80%\n";
        let list = parse_upower(dump);
        assert_eq!(list.len(), 1);
        assert_eq!(list[0]["name"], "MX Master");
        assert_eq!(list[0]["percent"], 55.0);
        assert_eq!(list[0]["charging"], true);
        assert_eq!(list[0]["kind"], "mouse");
    }
}
