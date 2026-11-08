//! Battery of wireless mice that report it only over a vendor HID protocol.
//!
//! Many 2.4 GHz gaming mice built on Compx/"CX" chips (Pulsar, Kysona, ATK, VXE and others) share
//! one protocol, documented by the Linux hid-pulsar and hid-kysona drivers. Where no kernel driver
//! exposes the battery yet, DeskWidgets asks the mouse itself through /dev/hidraw*:
//!
//!   host -> device  output report 0x08, 17 bytes: [0x08, cmd, 14 zero bytes, checksum]
//!   device -> host  input report 0x08, 17 bytes, byte 1 echoes cmd
//!   checksum        (0x55 - sum of bytes 0..15) & 0xff
//!   cmd 0x03 STATUS byte 6 = 1 when the mouse is awake and linked to its receiver
//!   cmd 0x04 POWER  byte 6 = charge %, byte 7 = 1 while the cable is plugged in, bytes 8-9 = mV
//!
//! Both commands are read-only. The hidraw node needs a udev rule (assets/udev/70-deskwidgets-hid.rules)
//! that gives the logged-in user access to the receiver's vendor interface only.
use crate::core::i18n::tr;

#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
const VENDORS: [u16; 3] = [0x379a, 0x3554, 0x373b]; // Compx, CX, CX
const REPORT_ID: u8 = 0x08;
const LENGTH: usize = 17;
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
const CMD_STATUS: u8 = 0x03;
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
const CMD_POWER: u8 = 0x04;

#[derive(Debug, Clone, PartialEq)]
pub struct Mouse {
    pub node: String,
    pub name: String,
    pub id: String,
    pub access: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Reading {
    pub online: bool,
    pub pct: Option<u8>,
    pub charging: bool,
    pub mv: u32,
}

/// Report 0x08 with 16 data bytes as both input and output (the command channel).
pub fn speaks_protocol(descriptor: &[u8]) -> bool {
    let has = |pattern: &[u8]| descriptor.windows(pattern.len()).any(|w| w == pattern);
    has(&[0x85, 0x08]) && has(&[0x95, 0x10]) && descriptor.contains(&0x91)
}

pub fn pretty_name(hid_name: &str) -> String {
    let mut name = hid_name.split_whitespace().collect::<Vec<_>>().join(" ");
    for vendor in ["Compx ", "CX ", "I-CHIP "] {
        if let Some(rest) = name.strip_prefix(vendor) {
            name = rest.to_string();
            break;
        }
    }
    for suffix in [" Wireless", " Receiver", " Dongle"] {
        if let Some(rest) = name.strip_suffix(suffix) {
            name = rest.to_string();
            break;
        }
    }
    if name.is_empty() { hid_name.to_string() } else { name }
}

pub fn packet(cmd: u8) -> [u8; LENGTH] {
    let mut pkt = [0u8; LENGTH];
    pkt[0] = REPORT_ID;
    pkt[1] = cmd;
    let sum: u32 = pkt[..16].iter().map(|b| *b as u32).sum();
    pkt[LENGTH - 1] = (0x55u32.wrapping_sub(sum) & 0xff) as u8;
    pkt
}

/// Interprets the answers to STATUS and POWER.
pub fn interpret(status: &[u8], power: Option<&[u8]>) -> Option<Reading> {
    if status.len() < 10 || status[6] > 1 {
        return None;
    }
    if status[6] != 1 {
        return Some(Reading { online: false, pct: None, charging: false, mv: 0 });
    }
    let power = power?;
    if power.len() < 10 || power[6] > 100 || power[7] > 1 {
        return None;
    }
    Some(Reading { online: true, pct: Some(power[6]), charging: power[7] == 1, mv: ((power[8] as u32) << 8) | power[9] as u32 })
}

#[cfg(target_os = "linux")]
mod linux {
    use super::*;
    use std::fs;
    use std::io::{Read, Write};
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::OpenOptionsExt;
    use std::path::Path;
    use std::time::{Duration, Instant};

    const SYSFS: &str = "/sys/class/hidraw";

    fn uevent(dev: &Path) -> std::collections::HashMap<String, String> {
        fs::read_to_string(dev.join("uevent"))
            .map(|text| text.lines().filter_map(|l| l.split_once('=')).map(|(k, v)| (k.to_string(), v.to_string())).collect())
            .unwrap_or_default()
    }

    /// Receivers that speak the protocol and are not handled by a kernel driver.
    pub fn find() -> Vec<Mouse> {
        let mut found = Vec::new();
        let Ok(entries) = fs::read_dir(SYSFS) else { return found };
        let mut nodes: Vec<String> = entries.filter_map(|e| e.ok()).map(|e| e.file_name().to_string_lossy().into_owned()).collect();
        nodes.sort();
        let id = regex::Regex::new(r"^[0-9A-Fa-f]{4}:([0-9A-Fa-f]{4}):([0-9A-Fa-f]{4})\.").unwrap();
        for node in nodes {
            let Ok(dev) = fs::canonicalize(Path::new(SYSFS).join(&node).join("device")) else { continue };
            let base = dev.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            let Some(m) = id.captures(&base) else { continue };
            let vendor = u16::from_str_radix(&m[1], 16).unwrap_or(0);
            if !VENDORS.contains(&vendor) {
                continue;
            }
            if dev.join("power_supply").is_dir() {
                continue; // a kernel driver reports it already
            }
            match fs::read(dev.join("report_descriptor")) {
                Ok(descriptor) if speaks_protocol(&descriptor) => {}
                _ => continue,
            }
            let name = uevent(&dev).get("HID_NAME").cloned().unwrap_or_else(|| "Wireless mouse".into());
            let path = format!("/dev/{node}");
            let access = {
                let c = std::ffi::CString::new(path.clone()).unwrap();
                unsafe { libc::access(c.as_ptr(), libc::R_OK | libc::W_OK) == 0 }
            };
            found.push(Mouse { node: path, name: pretty_name(&name), id: format!("{}:{}", &m[1], &m[2]).to_lowercase(), access });
        }
        found
    }

    fn request(file: &mut fs::File, cmd: u8, timeout: Duration) -> Option<Vec<u8>> {
        file.write_all(&packet(cmd)).ok()?;
        let deadline = Instant::now() + timeout;
        loop {
            let left = deadline.checked_duration_since(Instant::now())?;
            let mut fd = libc::pollfd { fd: file.as_raw_fd(), events: libc::POLLIN, revents: 0 };
            let ready = unsafe { libc::poll(&mut fd, 1, left.as_millis().min(i32::MAX as u128) as i32) };
            if ready <= 0 {
                return None;
            }
            let mut buf = [0u8; 64];
            match file.read(&mut buf) {
                Ok(n) if n >= 10 && buf[0] == REPORT_ID && buf[1] == cmd => return Some(buf[..n].to_vec()),
                Ok(_) => continue,
                Err(_) => return None,
            }
        }
    }

    /// The reading, or None when unreadable.
    pub fn read(mouse: &Mouse) -> Option<Reading> {
        let mut file = fs::OpenOptions::new().read(true).write(true).custom_flags(libc::O_NONBLOCK).open(&mouse.node).ok()?;
        let status = request(&mut file, CMD_STATUS, Duration::from_secs(1))?;
        if status[6] != 1 {
            return interpret(&status, None);
        }
        let power = request(&mut file, CMD_POWER, Duration::from_secs(1))?;
        interpret(&status, Some(&power))
    }
}

#[cfg(target_os = "linux")]
pub use linux::{find, read};

#[cfg(not(target_os = "linux"))]
pub fn find() -> Vec<Mouse> {
    Vec::new()
}

#[cfg(not(target_os = "linux"))]
pub fn read(_mouse: &Mouse) -> Option<Reading> {
    None
}

/// `deskwidgets mice`: what DeskWidgets can read right now.
pub fn main() -> i32 {
    let devices = find();
    if devices.is_empty() {
        println!("{}", tr("Підтримуваних приймачів бездротових мишей не знайдено", "No supported wireless mouse receivers found"));
        return 1;
    }
    for d in &devices {
        let head = format!("{} ({}, {})", d.name, d.id, d.node);
        if !d.access {
            println!("{head}: {}", tr("немає доступу — потрібне правило udev", "no access — the udev rule is needed"));
            continue;
        }
        match read(d) {
            None => println!("{head}: {}", tr("не відповідає", "no answer")),
            Some(r) if !r.online => println!("{head}: {}", tr("спить або не зв’язана з приймачем", "asleep or not linked")),
            Some(r) => {
                let charging = if r.charging { tr(", заряджається", ", charging") } else { "" };
                println!("{head}: {}%{charging}, {} mV", r.pct.unwrap_or(0), r.mv);
            }
        }
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checksum_and_answers() {
        let p = packet(CMD_POWER);
        assert_eq!(&p[..2], &[0x08, 0x04]);
        assert_eq!(p[16], (0x55u32 - 0x08 - 0x04) as u8);
        let mut status = [0u8; 17];
        status[6] = 1;
        let mut power = [0u8; 17];
        power[6] = 87;
        power[7] = 1;
        power[8] = 0x0f;
        power[9] = 0xa0;
        assert_eq!(interpret(&status, Some(&power)), Some(Reading { online: true, pct: Some(87), charging: true, mv: 4000 }));
        status[6] = 0;
        assert_eq!(interpret(&status, None).map(|r| r.online), Some(false));
        status[6] = 2;
        assert_eq!(interpret(&status, None), None);
        power[6] = 101;
        status[6] = 1;
        assert_eq!(interpret(&status, Some(&power)), None);
    }

    #[test]
    fn descriptors_and_names() {
        assert!(speaks_protocol(&[0x06, 0x00, 0xff, 0x85, 0x08, 0x95, 0x10, 0x91, 0x02]));
        assert!(!speaks_protocol(&[0x85, 0x08]));
        assert_eq!(pretty_name("Compx  2.4G Wireless Receiver"), "2.4G Wireless");
        assert_eq!(pretty_name("CX Pulsar Wireless"), "Pulsar");
    }
}
